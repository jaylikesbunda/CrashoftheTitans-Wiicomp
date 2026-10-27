"""Checks for tools/fix_fallback_gates.py and native_gx_work::FallbackGates (runtime-overlays/native_gx_work_metrics.h).

1. Unit test (tools/fallback_gates_tests.cpp): each gate bit is set by the VAT feature it names, and an all-indexed F32 draw of
   40 vertices fails no gate.
2. Stage on a synthetic fifo_parser.cpp that carries the census anchor: one marker pair, idempotent, exact restore.

Usage: python check_fallback_gates.py [--work DIR]
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = HERE.parent
sys.path.insert(0, str(HERE))

ANCHOR = 'native_gx_work::AddFallbackPos(unsigned(c.raw->vat.posType),bool(c.raw->vat.posMatIdx));'


def main(argv):
    work = Path(argv[argv.index('--work') + 1]) if '--work' in argv else Path(tempfile.mkdtemp(prefix='fallback-gates-'))
    work.mkdir(parents=True, exist_ok=True)
    failures = []

    def check(ok, label):
        print(('PASS ' if ok else 'FAIL ') + label)
        if not ok:
            failures.append(label)

    shim = work / 'shim/runtime'
    shim.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PORT / 'runtime-overlays/native_gx_work_metrics.h', shim / 'native_gx_work_metrics.h')
    exe = work / 'fallback_gates_tests.exe'
    build = subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-I', str(work / 'shim'), str(HERE / 'fallback_gates_tests.cpp'), '-o', str(exe)],
                           capture_output=True, text=True)
    check(build.returncode == 0, 'gate unit test compiles' + (' ' + build.stderr[:300] if build.returncode else ''))
    if build.returncode == 0:
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        check(run.returncode == 0 and 'FALLBACK_GATES_TESTS PASS' in run.stdout, 'gate unit test passes')

    root = work / 'nwii/src'
    target = root / 'hle/gx/fifo_parser.cpp'
    target.parent.mkdir(parents=True, exist_ok=True)
    original = ('void a() {\r\n  ' + ANCHOR + ' return NativePackedGeneric<false>(c);\r\n}\r\n').encode()
    target.write_bytes(original)
    stage = [sys.executable, str(HERE / 'fix_fallback_gates.py'), '--root', str(root)]
    subprocess.run(stage, check=True)
    applied = target.read_bytes()
    check(applied.count(b'/* NATIVE_FALLBACK_REASONS_V1:') == 1 and applied.count(b'/* END_NATIVE_FALLBACK_REASONS_V1:') == 1,
          'one marker pair around the anchor')
    check(b'FallbackGates(*c.raw,inputs)' in applied, 'the gate call is present')
    subprocess.run(stage, check=True)
    check(target.read_bytes() == applied, 'apply is idempotent')
    subprocess.run(stage + ['--restore'], check=True)
    check(target.read_bytes() == original, 'restore reproduces the original bytes')

    print('RESULT: %s (%d failure(s))' % ('PASS' if not failures else 'FAIL', len(failures)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
