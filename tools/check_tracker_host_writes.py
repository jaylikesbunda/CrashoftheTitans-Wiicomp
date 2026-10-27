"""Checks for fix_tracker_host_writes.py. Works on a scratch copy of the five staged files; the input is only read.

Verifies: apply wraps each host write once and adds the include once, apply is idempotent, the wrapped
text has balanced braces, CRLF is preserved, restore is byte-identical, restore is idempotent, the scopes do
not nest, and NWII_BUILD_GUEST_ARENA=0 leaves the files untouched.

Usage: python check_tracker_host_writes.py [<nWiiRuntime/src root>] [--work DIR]
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fix_tracker_host_writes as fx  # noqa: E402

DEFAULT = HERE.parent / 'build/nwii-full-source/nWiiRuntime/src'


def main(argv):
    work_dir = None
    if '--work' in argv:
        work_dir = Path(argv[argv.index('--work') + 1])
    args = [a for i, a in enumerate(argv) if not a.startswith('--') and (i == 0 or argv[i - 1] != '--work')]
    source = Path(args[0]) if args else DEFAULT
    work = work_dir or Path(tempfile.mkdtemp(prefix='hostwrite-check-'))
    work.mkdir(parents=True, exist_ok=True)
    failures = []

    def check(ok, label):
        print(('PASS ' if ok else 'FAIL ') + label)
        if not ok:
            failures.append(label)

    # Scratch copy of the files the stage touches (and nothing else).
    rels = list(fx.FILES)
    originals = {}
    for rel in rels:
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / rel, dst)
        originals[rel] = dst.read_bytes()
    check(all(fx.MARK not in o.decode('utf-8', 'surrogateescape') for o in originals.values()), 'input is unstaged (no marker)')

    env = dict(os.environ)
    env.pop('NWII_BUILD_GUEST_ARENA', None)
    stage = [sys.executable, str(HERE / 'fix_tracker_host_writes.py'), '--root', str(work)]
    subprocess.run(stage, check=True, env=env)
    applied = {rel: (work / rel).read_bytes() for rel in rels}

    for rel in rels:
        text = applied[rel].decode('utf-8', 'surrogateescape')
        n_edits = len(fx.FILES[rel])
        check(text.count('/* ' + fx.MARK + ':') == n_edits + (1 if rel in fx.INCLUDES else 0),
              '%s: one marker per edit%s' % (rel, ' plus the include' if rel in fx.INCLUDES else ''))
        check(text.count('/* END_' + fx.MARK + ':') == text.count('/* ' + fx.MARK + ':'), '%s: every marker closed' % rel)
        check(text.count('HostWriteScope tracker_scope') == n_edits, '%s: %d scope(s)' % (rel, n_edits))
        # Each edit adds balanced braces: the added text is a full statement block.
        added = fx.BLOCK.findall(text)
        unbalanced = [tag for tag, _, body in added if body.count('{') != body.count('}')]
        check(not unbalanced, '%s: braces balanced in every block %s' % (rel, unbalanced or ''))
        bare = lambda s: s.count('\n') - s.count('\r\n')  # virtual_disc.cpp mixes CRLF and LF in the original
        check(bare(text) == bare(originals[rel].decode('utf-8', 'surrogateescape')), '%s: line endings preserved' % rel)
        check(fx.unblock(text).encode('utf-8', 'surrogateescape') == originals[rel], '%s: unblock restores original bytes' % rel)

    # Apply is idempotent on its own output.
    subprocess.run(stage, check=True, env=env)
    check(all((work / rel).read_bytes() == applied[rel] for rel in rels), 'apply is idempotent')

    # Restore is byte-identical; restore again changes nothing.
    subprocess.run(stage + ['--restore'], check=True, env=env)
    check(all((work / rel).read_bytes() == originals[rel] for rel in rels), 'restore reproduces the original bytes')
    subprocess.run(stage + ['--restore'], check=True, env=env)
    check(all((work / rel).read_bytes() == originals[rel] for rel in rels), 'restore is idempotent')

    # Opt-out: the stage leaves the files untouched when the arena is off.
    env_off = dict(env, NWII_BUILD_GUEST_ARENA='0')
    subprocess.run(stage, check=True, env=env_off)
    check(all((work / rel).read_bytes() == originals[rel] for rel in rels), 'NWII_BUILD_GUEST_ARENA=0 leaves the files untouched')

    print('RESULT: %s (%d failure(s))' % ('PASS' if not failures else 'FAIL', len(failures)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
