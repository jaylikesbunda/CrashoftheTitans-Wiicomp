"""Checks for fix_fallback_census.py and the counter table in runtime-overlays/native_gx_work_metrics.h.

Verifies (no game, no GL): the enum and the names table have the same length; every metric the stage names exists in
the enum; apply wraps each call site once and adds the include once; apply is idempotent; restore is byte-identical;
restore is idempotent. The input files are only read; the work is in a scratch copy.

Usage: python check_fallback_census.py [<nWiiRuntime/src root>] [--work DIR]
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fix_fallback_census as fx  # noqa: E402

PORT = HERE.parent
DEFAULT = PORT / 'build/nwii-full-source/nWiiRuntime/src'
HEADER = PORT / 'runtime-overlays/native_gx_work_metrics.h'


def enum_names(header):
    body = re.search(r'enum Metric : unsigned \{(.*?)Count \};', header, re.S).group(1)
    return [n for n in re.findall(r'\b([A-Za-z]\w*)\b', re.sub(r'//[^\n]*', '', body)) if n not in ('unsigned',)]


def table_names(header):
    body = re.search(r'names\[Count\]=\{(.*?)\};', header, re.S).group(1)
    return re.findall(r'"([^"]+)"', body)


def main(argv):
    work_dir = None
    if '--work' in argv:
        work_dir = Path(argv[argv.index('--work') + 1])
    args = [a for i, a in enumerate(argv) if not a.startswith('--') and (i == 0 or argv[i - 1] != '--work')]
    source = Path(args[0]) if args else DEFAULT
    work = work_dir or Path(tempfile.mkdtemp(prefix='census-check-'))
    work.mkdir(parents=True, exist_ok=True)
    failures = []

    def check(ok, label):
        print(('PASS ' if ok else 'FAIL ') + label)
        if not ok:
            failures.append(label)

    header = HEADER.read_text(encoding='utf-8')
    enum = enum_names(header)
    names = table_names(header)
    check(len(enum) == len(names), 'enum (%d) and names table (%d) have the same length' % (len(enum), len(names)))
    check(len(set(names)) == len(names), 'metric names are unique')
    used = {m for edits in fx.FILES.values() for _, _, new in edits for m in re.findall(r'native_gx_work::(\w+)', new)}
    missing = sorted(m for m in used if m not in enum and m not in ('Add', 'AddFallbackPos', 'AddTexTrack', 'CountedMemcmp'))
    check(not missing, 'every metric the stage names exists in the enum %s' % (missing or ''))

    originals = {}
    for rel in fx.FILES:
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / rel, dst)
        originals[rel] = dst.read_bytes()
    check(all(fx.MARK.encode() not in o for o in originals.values()), 'input is unstaged (no marker)')

    stage = [sys.executable, str(HERE / 'fix_fallback_census.py'), '--root', str(work)]
    subprocess.run(stage, check=True)
    applied = {rel: (work / rel).read_bytes() for rel in fx.FILES}
    for rel, edits in fx.FILES.items():
        text = applied[rel].decode('utf-8', 'surrogateescape')
        expect = len(edits) + (1 if rel in fx.INCLUDES else 0)
        check(text.count('/* ' + fx.MARK + ':') == expect, '%s: one marker per edit%s' % (rel, ' plus the include' if rel in fx.INCLUDES else ''))
        check(text.count('/* END_' + fx.MARK + ':') == text.count('/* ' + fx.MARK + ':'), '%s: every marker closed' % rel)
        bare = lambda s: s.count('\n') - s.count('\r\n')
        check(bare(text) == bare(originals[rel].decode('utf-8', 'surrogateescape')), '%s: line endings preserved' % rel)
        check(fx.unblock(text).encode('utf-8', 'surrogateescape') == originals[rel], '%s: unblock restores original bytes' % rel)

    subprocess.run(stage, check=True)
    check(all((work / rel).read_bytes() == applied[rel] for rel in fx.FILES), 'apply is idempotent')
    subprocess.run(stage + ['--restore'], check=True)
    check(all((work / rel).read_bytes() == originals[rel] for rel in fx.FILES), 'restore reproduces the original bytes')
    subprocess.run(stage + ['--restore'], check=True)
    check(all((work / rel).read_bytes() == originals[rel] for rel in fx.FILES), 'restore is idempotent')

    print('RESULT: %s (%d failure(s))' % ('PASS' if not failures else 'FAIL', len(failures)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
