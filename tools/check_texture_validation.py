"""NATIVE_TEXTURE_VALIDATION_V1 oracle, no game and no GL context. Everything is written into a temporary directory.

1. Stage on a scratch copy of the staged texture cache: apply, second apply is a no-op, the marker count is exact, restore
   returns the staged text byte for byte, re-apply reproduces the first result.
2. Syntax: the staged and the patched src/hle/texture_cache_gl.cpp are compiled with g++ -fsyntax-only (C++20). The GL
   headers are not vendored here, so both compiles carry the same GL-only errors. The check fails only when the patched file
   reports a diagnostic the staged one does not (line numbers removed), which catches a misspelled name or a broken block.

Usage: check_texture_validation.py [--staged <nWiiRuntime root>]. The default is the build tree used by the other texture
checks. The staged tree is only read.
"""
from pathlib import Path
import argparse, collections, os, re, shutil, subprocess, tempfile
from port_paths import winlibs_bin
import fix_texture_validation as stage

port = Path(__file__).resolve().parents[1]
overlays = port / 'runtime-overlays'
MARK_COUNT = 5  # helper, stale, hotstale, dup, efbhash
MARKERS = re.compile(r'/\* ' + stage.MARK + r':\w+ ')
GLAD_STUB = '#pragma once\n#include <GL/gl.h>\n#include <GL/glext.h>\n'


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, **kwargs)


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


parser = argparse.ArgumentParser()
parser.add_argument('--staged', type=Path, default=port / 'build/nwii-full-source/nWiiRuntime')
args = parser.parse_args()
staged = args.staged
cache_rel = stage.CACHE

with tempfile.TemporaryDirectory(prefix='texture-validation-') as temp:
    temp = Path(temp)
    rt = temp / 'rt'
    shutil.copytree(staged / 'include', rt / 'include')
    (rt / cache_rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(staged / cache_rel, rt / cache_rel)
    originals = (rt / cache_rel).read_bytes()
    assert originals.count(b'texture.exact_hot') == 1 and originals.count(b'if(!reused)entry.hash=') == 1, \
        'the staged cache lacks the anchors (is the texture hash cache stage applied?)'

    stage.apply(port, rt)
    first = (rt / cache_rel).read_bytes()
    assert first != originals, 'the stage must change the texture cache'
    markers = len(MARKERS.findall(first.decode('utf-8', 'surrogateescape')))
    assert markers == MARK_COUNT, f'expected {MARK_COUNT} edits, found {markers}'
    stage.apply(port, rt)
    assert (rt / cache_rel).read_bytes() == first, 'second apply must be a no-op'
    print('STAGE_IDEMPOTENT ok')

    shutil.copytree(rt, temp / 'rt2')
    stage.restore(port, temp / 'rt2')
    assert (temp / 'rt2' / cache_rel).read_bytes() == originals, 'restore must be exact'
    stage.apply(port, temp / 'rt2')
    assert (temp / 'rt2' / cache_rel).read_bytes() == first, 'apply after restore must reproduce the first result'
    print('STAGE_RESTORE ok')

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
    orig_path = temp / 'orig' / 'texture_cache_gl.cpp'
    orig_path.parent.mkdir(parents=True)
    orig_path.write_bytes(originals)
    before = diagnostics(compiler, flags, orig_path, include_dirs)
    after = diagnostics(compiler, flags, rt / cache_rel, include_dirs)
    new = after - before
    print(f'diagnostics: staged {sum(before.values())}, patched {sum(after.values())}, new {sum(new.values())}')
    for message, count in new.items():
        print(f'  NEW x{count}: {message}')
    assert not new, 'the patched texture cache reports diagnostics the staged one does not'
    print('SYNTAX_DELTA ok')
    print('CHECK_TEXTURE_VALIDATION ok')
