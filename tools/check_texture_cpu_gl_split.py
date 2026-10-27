"""Harness for the texture CPU/GL split (NATIVE_TEXTURE_CPU_GL_SPLIT_V1; tools/fix_texture_cpu_gl_split.py).

Checks, in order:
  1. Reversibility: apply, apply again (must be a no-op), restore, restore again. The restored runtime must be byte-identical
     to the input for every touched file.
  2. Compile: the touched translation units compile with the runtime's own flags (needs NWII_CXX and NWII_CXXFLAGS, which
     must include the -I paths; skipped with a notice if unset).
  3. Stage (a) is the only code that reads guest RAM for textures: prepare_texture and the prepass may read it; the GL stage
     (run_ops, execute, resolve_texture, flush_ops) and the draw-time texture block in RendererGL::Render may not.
  4. Ordering: the prepass call sits before NativeSignalGpuCompletionEarly in ProcessGXFifo; Render flushes the queue before
     returning; the prepass flushes first.
  5. Pairing: the prepass records exactly one draw entry per DrawPrimitive it visits, and the draw block consumes exactly one
     entry per DrawPrimitive, so the draw index cannot drift.
  6. Prepare-time GL calls are an explicit, bounded list (prompt textures and the one-time S3TC query). Anything else fails.

What this does NOT check: the bound textures and uploaded bytes per draw against the pre-split runtime. That needs a GL trace from
two real runs (old and new) and is done in the F3 comparison, not here.

Usage: python tools/check_texture_cpu_gl_split.py [--source <nWiiRuntime dir>]
Exit status 0 means every check passed.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

p = Path(__file__).resolve().parents[1]
STAGE = p / 'tools/fix_texture_cpu_gl_split.py'
TOUCHED = ('src/hle/texture_cache_gl.cpp', 'src/hle/gx/renderer_gl.cpp', 'src/hle/gx.cpp', 'include/runtime/texture_cache_gl.h')
MARK = 'NATIVE_TEXTURE_CPU_GL_SPLIT_V1'
failures = []


def check(ok, label):
    print(('PASS ' if ok else 'FAIL ') + label)
    if not ok:
        failures.append(label)


def live_code(text):
    """The applied text without the '#if 0' blocks that keep the original code. Those are dead by construction."""
    return re.sub(r'#if 0 // [^\n]*\n.*?\n#endif // [^\n]*\n', '', text, flags=re.S)


def function_body(text, signature):
    """Text from the signature to the matching closing brace at column 0 (the runtime's functions end with '}' at column 0)."""
    i = text.find(signature)
    if i < 0:
        return None
    j = text.find('\n}', i)
    return text[i:j + 2] if j >= 0 else None


def run_stage(root, restore=False):
    # The stage addresses build/nwii-full-source/nWiiRuntime relative to its own tree; mirror that layout in a scratch area.
    scratch = Path(tempfile.mkdtemp(prefix='tcs_'))
    (scratch / 'tools').mkdir()
    (scratch / 'runtime-overlays').mkdir()
    (scratch / 'build/nwii-full-source/nWiiRuntime').mkdir(parents=True)
    shutil.copyfile(STAGE, scratch / 'tools/fix_texture_cpu_gl_split.py')
    for name in ('texture_cpu_gl_split_walk.inc',):
        shutil.copyfile(p / 'runtime-overlays' / name, scratch / 'runtime-overlays' / name)
    for part in ('src', 'include', 'CMakeLists.txt'):
        src = root / part
        dst = scratch / 'build/nwii-full-source/nWiiRuntime' / part
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copyfile(src, dst)
    # The renderer-state overlays are copied by fix_renderer_state_split.py before this stage runs; do the same here.
    for name, dest in (('render_state.h', 'include/runtime/gx/render_state.h'), ('render_state.cpp', 'src/hle/gx/render_state.cpp')):
        shutil.copyfile(p / 'runtime-overlays' / name, scratch / 'build/nwii-full-source/nWiiRuntime' / dest)
    args = [sys.executable, str(scratch / 'tools/fix_texture_cpu_gl_split.py')] + (['--restore'] if restore else [])
    r = subprocess.run(args, capture_output=True, text=True)
    return scratch, r


def tree_bytes(base):
    return {rel: (base / rel).read_bytes() for rel in TOUCHED}


def check_reversibility(source):
    """Runs apply, apply, restore, restore on a private copy. Each run works on a scratch layout and the touched files are
    carried back into the copy, so every step sees the previous step's result."""
    work = Path(tempfile.mkdtemp(prefix='tcs_src_')) / 'nWiiRuntime'
    for part in ('src', 'include', 'CMakeLists.txt'):
        s = source / part
        if s.is_dir():
            shutil.copytree(s, work / part)
        else:
            work.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(s, work / part)
    pristine = tree_bytes(work)
    for mode in ('apply', 'apply again', 'restore', 'restore again'):
        scratch, r = run_stage(work, restore=mode.startswith('restore'))
        check(r.returncode == 0, f'stage {mode} exits 0 ({(r.stdout + r.stderr).strip()[-160:]})')
        if r.returncode == 0:
            for rel in TOUCHED:
                (work / rel).write_bytes((scratch / 'build/nwii-full-source/nWiiRuntime' / rel).read_bytes())
        shutil.rmtree(scratch, ignore_errors=True)
    return pristine, tree_bytes(work), work


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, default=p / 'build/nwii-full-source/nWiiRuntime')
    args = ap.parse_args()
    source = args.source
    if not (source / 'src/hle/texture_cache_gl.cpp').exists():
        raise SystemExit(f'no runtime source at {source}; build the tree first or pass --source')

    # 1. reversibility, byte-exact
    pristine, restored, copy = check_reversibility(source)
    check(restored == pristine, 'apply, apply, restore, restore is byte-identical for all touched files')

    # applied text for the static checks: one fresh application to a scratch layout
    scratch, r = run_stage(source)
    check(r.returncode == 0, 'stage applies to the runtime source')
    applied = {}
    for rel in TOUCHED:
        raw = (scratch / 'build/nwii-full-source/nWiiRuntime' / rel).read_text(encoding='utf-8', errors='replace')
        applied[rel] = live_code(raw.replace('\r', ''))

    tc = applied['src/hle/texture_cache_gl.cpp']
    rn = applied['src/hle/gx/renderer_gl.cpp']
    gx = applied['src/hle/gx.cpp']

    # 3. guest-RAM reads: only stage (a) may touch them
    stage_b = ('TextureCache::run_ops', 'TextureCache::execute', 'TextureCache::resolve_texture', 'TextureCache::flush_ops')
    for sig in stage_b:
        body = function_body(tc, sig + '(') or function_body(tc, sig + '(const')
        check(body is not None, f'{sig} found')
        if body:
            bad = [w for w in ('ctx.mmu', 'get_ptr', 'g_ctx_ptr', 'mmu.', 'read8', 'read32') if w in body]
            check(not bad, f'{sig} reads no guest RAM ({bad or "clean"})')
    draw = rn[rn.find('// NATIVE_TEXTURE_CPU_GL_SPLIT_V1: stage (b).'):]
    draw = draw[:draw.find('DrawPrimitive(cmd);') + 1]
    check('mmu' not in draw and 'get_ptr' not in draw, 'draw-time texture block reads no guest RAM')

    # 6. GL calls allowed in the prepare path, as an explicit list
    prep = function_body(tc, 'TextureCache::TextureRef TextureCache::prepare_texture(')
    check(prep is not None, 'prepare_texture found')
    if prep:
        gl_calls = sorted(set(re.findall(r'\b(gl[A-Z]\w*|NativeTexture(?:Generate|Bind|Delete|Active)|NativeS3tcAvailable|prompts\.texture)\s*\(', prep)))
        allowed = {'prompts.texture', 'NativeS3tcAvailable'}
        check(set(gl_calls) <= allowed, f'prepare_texture GL calls are the allowed set (found {gl_calls})')
    # the walk's own GL use: flush_ops only (executes queued GL); nothing else
    walk_text = function_body(applied['src/hle/gx/renderer_gl.cpp'], 'void NativePrepareTextures(const CommandStream& commands) {') or ''
    check(walk_text != '', 'NativePrepareTextures found')
    walk_gl = re.findall(r'\bgl[A-Z]\w*\s*\(', walk_text)
    check(not walk_gl, 'NativePrepareTextures makes no direct GL call')

    # 4. ordering
    i_prepass = gx.find('NativePrepareTexturesBridge(frame);')
    i_signal = gx.find('NativeSignalGpuCompletionEarly(frame);')
    check(0 <= i_prepass < i_signal, 'prepass runs before NativeSignalGpuCompletionEarly')
    check('flush_ops(); // ' + MARK in rn, 'Render flushes the queue before it returns')
    walk = rn[rn.find('void NativePrepareTextures('):]
    walk = walk[:walk.find('\n}\n') + 3]
    check(walk.find('flush_ops()') < walk.find('prepare_texture('), 'the prepass flushes before it prepares')

    # 5. pairing: one record per DrawPrimitive, in both the prepass and the draw block
    draws_visited = len(re.findall(r'e\.kind == \(uint8_t\)GXCommandType::DrawPrimitive', walk))
    records_pushed = len(re.findall(r's_draw_textures\.push_back\(draw\)', walk))
    check(draws_visited == 1 and records_pushed == 1, 'prepass pushes one record per DrawPrimitive entry')
    consumed = len(re.findall(r's_draw_index\+\+|\+\+s_draw_index', rn))
    check(consumed == 1, 'draw block consumes one record per DrawPrimitive')

    # 2. compile the touched translation units
    cxx = os.environ.get('NWII_CXX')
    cflags = os.environ.get('NWII_CXXFLAGS', '')
    if not cxx:
        print('SKIP compile (set NWII_CXX and NWII_CXXFLAGS, with -I paths for glad, SDL2 and the runtime include dir)')
    else:
        root = scratch / 'build/nwii-full-source/nWiiRuntime'
        for rel in ('src/hle/texture_cache_gl.cpp', 'src/hle/gx/renderer_gl.cpp', 'src/hle/gx.cpp'):
            cmd = [cxx, '-fsyntax-only', f'-I{root / "include"}'] + cflags.split() + [str(root / rel)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            check(r.returncode == 0, f'compiles: {rel}' + ('' if r.returncode == 0 else '\n' + r.stderr[:1500]))

    shutil.rmtree(scratch, ignore_errors=True)
    print('')
    print('ALL CHECKS PASSED' if not failures else f'{len(failures)} CHECK(S) FAILED')
    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(main())
