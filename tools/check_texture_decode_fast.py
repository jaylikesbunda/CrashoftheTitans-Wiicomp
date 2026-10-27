"""Checks for fix_texture_decode_fast.py and runtime-overlays/native_texture_decode.h. Works in a scratch directory.

1. Differential test (tools/texture_decode_tests.cpp): fast decoders and DXT1 conversion against a verbatim copy of the
   original decoders, plus an independent S3TC decoder. Needs g++.
2. Stage on a scratch copy of texture_cache_gl.cpp: apply adds one marker per edit and the include, apply is idempotent,
   restore is byte-identical, restore is idempotent.

Usage: python check_texture_decode_fast.py [<nWiiRuntime root>] [--work DIR]
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = HERE.parent
sys.path.insert(0, str(HERE))
import fix_texture_decode_fast as fx  # noqa: E402

DEFAULT = PORT / 'build/nwii-full-source/nWiiRuntime'


def main(argv):
    work = Path(argv[argv.index('--work') + 1]) if '--work' in argv else Path(tempfile.mkdtemp(prefix='texdec-check-'))
    args = [a for i, a in enumerate(argv) if not a.startswith('--') and (i == 0 or argv[i - 1] != '--work')]
    root = Path(args[0]) if args else DEFAULT
    work.mkdir(parents=True, exist_ok=True)
    failures = []

    def check(ok, label):
        print(('PASS ' if ok else 'FAIL ') + label)
        if not ok:
            failures.append(label)

    exe = work / 'texture_decode_tests.exe'
    build = subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-I', str(PORT / 'runtime-overlays'),
                            str(HERE / 'texture_decode_tests.cpp'), '-o', str(exe)], capture_output=True, text=True)
    check(build.returncode == 0, 'differential test compiles' + (' ' + build.stderr[:300] if build.returncode else ''))
    if build.returncode == 0:
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        check(run.returncode == 0 and 'TEXTURE_DECODE_TESTS PASS' in run.stdout, 'differential test passes')

    scratch = work / 'nwii'
    rel = fx.CACHE
    (scratch / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / rel, scratch / rel)
    original = (scratch / rel).read_bytes()
    check(b'NATIVE_TEXTURE_DECODE_FAST_V1' not in original, 'input is unstaged')
    stage = [sys.executable, str(HERE / 'fix_texture_decode_fast.py'), '--root', str(scratch)]
    subprocess.run(stage, check=True)
    applied = (scratch / rel).read_bytes()
    check(applied.count(b'/* NATIVE_TEXTURE_DECODE_FAST_V1:') == len(fx.EDITS) + 1, 'one marker per edit plus the include')
    check(applied.count(b'/* END_NATIVE_TEXTURE_DECODE_FAST_V1:') == applied.count(b'/* NATIVE_TEXTURE_DECODE_FAST_V1:'),
          'every marker closed')
    check((scratch / 'include/runtime/native_texture_decode.h').exists(), 'overlay header copied into include/runtime')
    subprocess.run(stage, check=True)
    check((scratch / rel).read_bytes() == applied, 'apply is idempotent')
    subprocess.run(stage + ['--restore'], check=True)
    check((scratch / rel).read_bytes() == original, 'restore reproduces the original bytes')
    subprocess.run(stage + ['--restore'], check=True)
    check((scratch / rel).read_bytes() == original, 'restore is idempotent')

    print('RESULT: %s (%d failure(s))' % ('PASS' if not failures else 'FAIL', len(failures)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
