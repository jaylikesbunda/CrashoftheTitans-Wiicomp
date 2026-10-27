"""NATIVE_TEXTURE_HASH_CACHE_V1 oracle, no game and no GL context.

1. Stage on a scratch copy of the staged texture cache: apply, second apply is a no-op, the marker count is exact, restore
   returns the canonical text byte for byte, re-apply reproduces the first result.
2. Syntax: the original and the patched src/hle/texture_cache_gl.cpp are compiled with g++ -fsyntax-only (C++20). The
   glad header is not vendored in this checkout and the mingw GL header lacks the GL 3+ entry points, so both compiles carry
   the same GL-only errors. The check fails only when the patched file reports a diagnostic the original does not (after
   removing line numbers), which catches a wrong include, a misspelled name or a broken block.
3. Unit test: tools/texture_hash_cache_tests.cpp (validation rules and the tracker model) builds with -Wall -Wextra and runs.
"""
from pathlib import Path
import collections, os, re, shutil, subprocess, tempfile
from port_paths import winlibs_bin
import fix_texture_hash_cache as stage

port = Path(__file__).resolve().parents[1]
staged = port / 'build/nwii-full-source/nWiiRuntime'
overlays = port / 'runtime-overlays'
tools = port / 'tools'
MARK_COUNT = 5  # inc, gate, hot, track, clean
MARKERS = re.compile(r'/\* ' + stage.MARK + r':\w+ [A-Za-z0-9+/=]* \*/|/\* END_' + stage.MARK + r':\w+ \*/')
GLAD_STUB = '#pragma once\n#include <GL/gl.h>\n#include <GL/glext.h>\n'


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    return result


def must(command, **kwargs):
    result = run(command, **kwargs)
    print((result.stdout + result.stderr).rstrip())
    if result.returncode:
        raise SystemExit(f'failed: {command[0]} exit {result.returncode}')
    return result


def diagnostics(compiler, flags, source, include_dirs):
    """Error messages with line numbers and paths removed, as a multiset."""
    command = [compiler, *flags, '-fmax-errors=0']
    for directory in include_dirs:
        command += ['-I', str(directory)]
    command += ['-c', str(source), '-o', os.devnull]
    result = run(command)
    counts = collections.Counter()
    for line in result.stderr.splitlines():
        if ': error' in line or ': fatal error' in line:
            counts[re.sub(r'^.*?:\d+:\d+: ', '', line)] += 1
    return counts


with tempfile.TemporaryDirectory(prefix='texture-hash-') as temp:
    temp = Path(temp)
    rt = temp / 'rt'
    shutil.copytree(staged / 'include', rt / 'include')
    cache_rel = stage.CACHE
    (rt / cache_rel).parent.mkdir(parents=True, exist_ok=True)
    # The staged tree may already carry the stage: the scratch copy starts from the canonical text.
    canonical = stage.unblock(stage.read(staged / cache_rel))
    stage.write(rt / cache_rel, canonical)
    originals = (rt / cache_rel).read_bytes()

    stage.apply(port, rt)
    first = (rt / cache_rel).read_bytes()
    assert first != originals, 'the stage must change the texture cache'
    assert (rt / stage.HEADER).exists(), 'the stage must install the header'
    markers = len(re.findall(r'/\* ' + stage.MARK + r':\w+ ', first.decode('utf-8', 'surrogateescape')))
    assert markers == MARK_COUNT, f'expected {MARK_COUNT} edits, found {markers}'
    stage.apply(port, rt)
    assert (rt / cache_rel).read_bytes() == first, 'second apply must be a no-op'
    print('STAGE_IDEMPOTENT ok')

    shutil.copytree(rt, temp / 'rt2')
    stage.restore(port, temp / 'rt2')
    assert (temp / 'rt2' / cache_rel).read_bytes() == originals, 'restore must be exact'
    assert not (temp / 'rt2' / stage.HEADER).exists()
    stage.apply(port, temp / 'rt2')
    assert (temp / 'rt2' / cache_rel).read_bytes() == first, 'apply after restore must reproduce the first result'
    print('STAGE_RESTORE ok')

    # Syntax: the overlay headers are laid out as runtime/<name> (the build copies them there); staged headers come first.
    overlay_root = temp / 'overlay'
    (overlay_root / 'runtime').mkdir(parents=True)
    for header in overlays.iterdir():
        if header.suffix in ('.h', '.inc', '.hpp'):
            shutil.copyfile(header, overlay_root / 'runtime' / header.name)
    glad = temp / 'stub'
    (glad / 'glad').mkdir(parents=True)
    (glad / 'glad' / 'glad.h').write_text(GLAD_STUB)
    compiler = shutil.which('g++') or str(winlibs_bin() / 'g++.exe')
    flags = ['-std=c++20', '-fsyntax-only']
    include_dirs = [glad, rt / 'include', overlay_root]
    (rt / 'orig' / 'src/hle').mkdir(parents=True)
    (rt / 'orig' / 'src/hle' / 'texture_cache_gl.cpp').write_bytes(originals)
    before = diagnostics(compiler, flags, rt / 'orig' / 'src/hle' / 'texture_cache_gl.cpp', include_dirs)
    after = diagnostics(compiler, flags, rt / cache_rel, include_dirs)
    new = after - before
    print(f'diagnostics: original {sum(before.values())}, patched {sum(after.values())}, new {sum(new.values())}')
    for message, count in new.items():
        print(f'  NEW x{count}: {message}')
    assert not new, 'the patched texture cache reports diagnostics the original does not'
    print('SYNTAX_DELTA ok')

    # Unit test of the validation rules and the tracker model.
    binary = temp / 'texture_hash_cache_tests.exe'
    must([compiler, '-std=c++20', '-Wall', '-Wextra', '-O1', '-I', str(overlays), str(tools / 'texture_hash_cache_tests.cpp'),
          '-o', str(binary)])
    env = {k: v for k, v in os.environ.items() if not k.startswith('NWII_')}
    must([str(binary)], env=env, timeout=600)
    print('CHECK_TEXTURE_HASH_CACHE ok')
