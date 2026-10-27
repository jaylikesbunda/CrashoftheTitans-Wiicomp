"""Checks for fix_texture_validation_memo.py and runtime-overlays/native_texture_memo.h. Works in a scratch directory.

1. Unit test (tools/texture_memo_tests.cpp): the memo is invalidated by a write to any page of its entry and by a TLUT
   change, and never arms for an untracked entry. A model of the tracker stands in for native_guest_arena.cpp.
2. The overlay tracker advances the write epoch where a page is dirtied (tracker_fault and HostWriteScope).
3. Stage on scratch copies of texture_cache_gl.h and .cpp: marker counts, idempotence, exact restore.

Usage: python check_texture_validation_memo.py [<nWiiRuntime root>] [--work DIR]
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = HERE.parent
sys.path.insert(0, str(HERE))
import fix_texture_validation_memo as fx  # noqa: E402

DEFAULT = PORT / 'build/nwii-full-source/nWiiRuntime'


def main(argv):
    work = Path(argv[argv.index('--work') + 1]) if '--work' in argv else Path(tempfile.mkdtemp(prefix='memo-check-'))
    args = [a for i, a in enumerate(argv) if not a.startswith('--') and (i == 0 or argv[i - 1] != '--work')]
    root = Path(args[0]) if args else DEFAULT
    work.mkdir(parents=True, exist_ok=True)
    failures = []

    def check(ok, label):
        print(('PASS ' if ok else 'FAIL ') + label)
        if not ok:
            failures.append(label)

    shim = work / 'shim/runtime'
    shim.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PORT / 'runtime-overlays/native_tlut_epoch.h', shim / 'native_tlut_epoch.h')
    shutil.copyfile(PORT / 'runtime-overlays/native_texture_memo.h', shim / 'native_texture_memo.h')
    exe = work / 'texture_memo_tests.exe'
    build = subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-I', str(work / 'shim'), '-I', str(PORT / 'runtime-overlays'),
                            str(HERE / 'texture_memo_tests.cpp'), '-o', str(exe)], capture_output=True, text=True)
    check(build.returncode == 0, 'memo unit test compiles' + (' ' + build.stderr[:300] if build.returncode else ''))
    if build.returncode == 0:
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        check(run.returncode == 0 and 'TEXTURE_MEMO_TESTS PASS' in run.stdout, 'memo unit test passes')

    arena = (PORT / 'runtime-overlays/native_guest_arena.cpp').read_text(encoding='utf-8', errors='replace')
    check(len(re.findall(r'next_write_epoch\(\)', arena)) == 4, 'page epoch stamped in tracker_fault, HostWriteScope and apply_sticky (+ definition)')
    check(arena.count('page_epoch[i].store(next_write_epoch()') == 2, 'tracker_fault and HostWriteScope stamp the written page')
    check('uint64_t native_ram_pages_epoch(' in arena and 'uint64_t native_ram_pages_armed_total()' in arena, 'pages epoch and armed-total accessors defined')

    scratch = work / 'nwii'
    files = [fx.HEADER, fx.CACHE]
    originals = {}
    for rel in files:
        (scratch / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / rel, scratch / rel)
    # A fully staged tree carries the later texture-cache stages on top of the memo: undo them on the scratch copy first
    # (their restores are exact), so the memo stage sees the text it was written for.
    if b'/* NATIVE_TEXTURE_ATTRIBUTION_V1:' in (scratch / fx.CACHE).read_bytes():
        subprocess.run([sys.executable, str(HERE / 'fix_texture_attribution.py'), '--root', str(scratch / 'src'), '--restore'], check=True)
    if fx.MARK.encode() in (scratch / fx.CACHE).read_bytes() or fx.MARK.encode() in (scratch / fx.HEADER).read_bytes():
        subprocess.run([sys.executable, str(HERE / 'fix_texture_validation_memo.py'), '--root', str(scratch), '--restore'], check=True)
    for rel in files:
        originals[rel] = (scratch / rel).read_bytes()
    check(all(fx.MARK.encode() not in o for o in originals.values()), 'input is unstaged')
    stage = [sys.executable, str(HERE / 'fix_texture_validation_memo.py'), '--root', str(scratch)]
    subprocess.run(stage, check=True)
    applied = {rel: (scratch / rel).read_bytes() for rel in files}
    header_markers = applied[fx.HEADER].count(b'/* NATIVE_TEXTURE_VALIDATION_MEMO_V1:')
    cache_markers = applied[fx.CACHE].count(b'/* NATIVE_TEXTURE_VALIDATION_MEMO_V1:')
    check(header_markers == 3, 'header: texture entry, EFB entry and include (%d markers)' % header_markers)
    check(cache_markers == 8, 'cache: skip, arm, thrash guard, thrash count, query, EFB pre-query, EFB arm and EFB refresh (%d markers)' % cache_markers)
    check(all(a.count(b'/* END_NATIVE_TEXTURE_VALIDATION_MEMO_V1:') == a.count(b'/* NATIVE_TEXTURE_VALIDATION_MEMO_V1:')
              for a in applied.values()), 'every marker closed')
    check((scratch / 'include/runtime/native_texture_memo.h').exists(), 'memo header copied into include/runtime')
    subprocess.run(stage, check=True)
    check(all((scratch / rel).read_bytes() == applied[rel] for rel in files), 'apply is idempotent')
    subprocess.run(stage + ['--restore'], check=True)
    check(all((scratch / rel).read_bytes() == originals[rel] for rel in files), 'restore reproduces the original bytes')
    subprocess.run(stage + ['--restore'], check=True)
    check(all((scratch / rel).read_bytes() == originals[rel] for rel in files), 'restore is idempotent')

    print('RESULT: %s (%d failure(s))' % ('PASS' if not failures else 'FAIL', len(failures)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
