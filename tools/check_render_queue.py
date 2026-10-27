"""Harness for the GL render queue (NATIVE_RENDER_QUEUE_V1; tools/fix_render_queue.py; model in tools/render_queue_tests.cpp).

Checks, in order:
  1. Queue equivalence: generated drains and frame tasks, run inline (the synchronous reference) and threaded through
     OrderedQueue with randomized scheduling, give the same GL call sequence, guest RAM, and guest-visible signal values.
     In-flight items never exceed the capacity. Run per seed, twice threaded.
  2. Negative control: with the flush rule off the same harness must detect a mismatch. Otherwise it cannot see violations.
  3. Reversibility of the stage: in a scratch copy (texture split applied first, as the build does), apply, apply again (no-op),
     restore, restore again. The restored tree must be byte-identical to the input for every touched file.
  4. Ordering and ownership, on the applied tree: the flush before the early signal comes before the early signal; the batch
     hand-over (SubmitBatch) replaces the synchronous Render; the savestate sync flushes; the prepass takes the BP shadow from
     the front end in queue mode; Start/Stop bracket the GL thread in main; the prepass has no GL call.
  5. Compile: the touched translation units compile with the runtime's own flags (needs NWII_CXX and NWII_CXXFLAGS, which
     must include the -I paths; skipped with a notice if unset).

Usage: python tools/check_render_queue.py [--seeds 300] [--source <nWiiRuntime dir>]
Compiler for check 1: $NWII_GXX or g++ on PATH. Exit status 0 means every check passed.
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
STAGE = p / 'tools/fix_render_queue.py'
TEX_STAGE = p / 'tools/fix_texture_cpu_gl_split.py'
ATTRIBUTION_STAGE = p / 'tools/fix_texture_attribution.py'
STATE_STAGE = p / 'tools/fix_renderer_state_split.py'
HARNESS = p / 'tools/render_queue_tests.cpp'
TOUCHED = ('src/hle/gx.cpp', 'src/hle/gx/renderer_gl.cpp', 'src/hle/texture_cache_gl.cpp', 'src/core/main.cpp',
           'include/runtime/texture_cache_gl.h', 'include/runtime/native_prompt_textures.h')
failures = []


def check(ok, label):
    print(('PASS ' if ok else 'FAIL ') + label)
    if not ok:
        failures.append(label)


def compiler():
    return os.environ.get('NWII_GXX') or shutil.which('g++')


def run_harness(extra, label):
    gxx = compiler()
    if not gxx:
        check(False, label + ' (no g++)')
        return
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / 'queue_tests.exe'
        build = subprocess.run([gxx, '-std=c++20', '-O2', '-pthread', '-I', str(p / 'runtime-overlays'), str(HARNESS), '-o', str(exe)],
                               capture_output=True, text=True)
        if build.returncode:
            check(False, label + ' (compile): ' + build.stderr[-400:])
            return
        r = subprocess.run([str(exe)] + extra, capture_output=True, text=True, timeout=600)
        print(r.stdout.rstrip())
        check(r.returncode == 0, label)


def stage_round_trip(source):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shutil.copytree(p / 'tools', root / 'tools')
        shutil.copytree(p / 'runtime-overlays', root / 'runtime-overlays')
        rt = root / 'build/nwii-full-source/nWiiRuntime'
        rt.parent.mkdir(parents=True)
        shutil.copytree(source, rt)
        # The build applies the state split and the texture split before this stage; do the same here.
        for stage in (STATE_STAGE, ATTRIBUTION_STAGE, TEX_STAGE):
            r = subprocess.run([sys.executable, str(root / 'tools' / stage.name)], capture_output=True, text=True)
            if r.returncode:
                check(False, 'prerequisite ' + stage.name + ': ' + r.stderr[-300:])
                return None
        snapshot = {rel: (rt / rel).read_bytes() for rel in TOUCHED}
        staged = root / 'tools/fix_render_queue.py'
        for args, label in (([], 'apply'), ([], 'apply again (no-op)'), (['--restore'], 'restore'), (['--restore'], 'restore again')):
            r = subprocess.run([sys.executable, str(staged)] + args, capture_output=True, text=True)
            if r.returncode:
                check(False, 'stage ' + label + ': ' + r.stderr[-300:])
                return None
        check(all((rt / rel).read_bytes() == snapshot[rel] for rel in TOUCHED) and not (rt / 'src/hle/render_queue_glue.inc').exists(),
              'stage apply/apply/restore/restore leaves every touched file byte-identical')
        # Apply once more and keep the result for the ordering checks and the compile.
        subprocess.run([sys.executable, str(staged)], capture_output=True, text=True, check=True)
        keep = Path(tempfile.mkdtemp(prefix='render_queue_applied_'))
        shutil.copytree(rt, keep / 'nWiiRuntime', dirs_exist_ok=True)
        return keep / 'nWiiRuntime'


def ordering(rt):
    gx = (rt / 'src/hle/gx.cpp').read_text(encoding='utf-8', errors='replace')
    flush = gx.find('render_queue::FlushForCopy(); // NATIVE_RENDER_QUEUE_V1')
    early = gx.find('{ // NATIVE_EARLY_PE_SIGNAL_V1: the guest sees every token')
    check(0 <= flush < early, 'EFB-copy flush sits before the early PE signal')
    check('render_queue::SubmitBatch(frame)' in gx and gx.index('render_queue::SubmitBatch(frame)') < gx.index('} else if (g_window) {'),
          'queued batches are submitted in place of the synchronous Render')
    check('{ nwii::runtime::render_queue::FlushForSavestate(); return true; }' in gx, 'savestate sync flushes the GL thread before returning')
    rend = (rt / 'src/hle/gx/renderer_gl.cpp').read_text(encoding='utf-8', errors='replace')
    m = re.search(r'void NativePrepareTextures\(const CommandStream& commands\) \{(.*?)\n\}\n', rend, re.S)
    body = m.group(1) if m else ''
    check(bool(body) and 'FrontBpShadow' in body and not re.search(r'\bgl[A-Z]\w*\(|NativeTexture(Generate|Bind|Delete)\(', body),
          'prepass takes the front-end BP shadow and makes no GL call')
    check(bool(re.search(r'TextureRef::Prompt: return native_prompts::textures\(\)\.texture\(ref\.slot\)',
                         (rt / 'src/hle/texture_cache_gl.cpp').read_text(encoding='utf-8', errors='replace'))),
          'prompt glyph textures are made on the GL thread by resolve_texture')
    mn = (rt / 'src/core/main.cpp').read_text(encoding='utf-8', errors='replace')
    check('render_queue::Start(' in mn and mn.index('render_queue::Start(') < mn.index('\n  if (headless) {\n'),
          'main starts the GL thread before the loop')
    check('render_queue::Stop(); // NATIVE_RENDER_QUEUE_V1' in mn and mn.index('render_queue::Stop(); // NATIVE_RENDER_QUEUE_V1') < mn.index('SDL_GL_DeleteContext(gl_ctx);'),
          'main stops and joins the GL thread before the context is deleted')


def compile_touched(rt):
    gxx = compiler()
    flags = os.environ.get('NWII_CXXFLAGS')
    if flags:
        flags = flags.replace('{RT}', str(rt))  # the -I paths name the tree under test, e.g. -I{RT}/include
    cxx = os.environ.get('NWII_CXX') or gxx
    if not flags or not cxx:
        print('SKIP compile of touched translation units (set NWII_CXX and NWII_CXXFLAGS, including the -I paths)')
        return
    for rel in ('src/hle/gx.cpp', 'src/hle/texture_cache_gl.cpp', 'src/hle/gx/renderer_gl.cpp', 'src/core/main.cpp'):
        r = subprocess.run([cxx, '-fsyntax-only'] + flags.split() + [str(rt / rel)], capture_output=True, text=True)
        check(r.returncode == 0, 'compiles: ' + rel + (': ' + r.stderr[-300:] if r.returncode else ''))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=300)
    ap.add_argument('--source', type=Path, default=p / 'build/nwii-full-source/nWiiRuntime')
    args = ap.parse_args()
    run_harness([str(args.seeds)], 'queue equivalence (inline vs threaded, randomized)')
    run_harness([str(max(50, args.seeds // 2)), '--no-flush'], 'negative control: flush rule off is detected')
    if args.source.is_dir():
        rt = stage_round_trip(args.source)
        if rt:
            ordering(rt)
            compile_touched(rt)
    else:
        print('SKIP stage checks: no runtime source at ' + str(args.source))
    print('ALL PASS' if not failures else 'FAILED: ' + '; '.join(failures))
    sys.exit(1 if failures else 0)
