"""Checks for fix_gl_state_redundancy.py. Works on a scratch copy; never writes the input.

Verifies: apply changes only the GL-call helpers, apply is idempotent, restore is
byte-identical to the original, restore is idempotent, CRLF is preserved, and the
opt-out env var leaves the file untouched.

Usage: python check_gl_state_redundancy.py [renderer_gl.cpp] [--work DIR]
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fix_gl_state_redundancy as fx  # noqa: E402

ALLOWED = {'NativeRetainedGpuBind', 'NativeUploadBatch', 'FlushBatch', 'NativeBindArrayBuffer'}
HEADER = re.compile(r'^static [^\n;]*?\b(\w+)\([^\n;]*\)\s*(?:const\s*)?\{\s*$', re.M)


def enclosing_function(text, offset):
    name = None
    for m in HEADER.finditer(text):
        if m.start() <= offset:
            name = m.group(1)
        else:
            break
    return name


def main(argv):
    work_dir = None
    if '--work' in argv:
        work_dir = Path(argv[argv.index('--work') + 1])
    args = [a for i, a in enumerate(argv) if not a.startswith('--') and (i == 0 or argv[i - 1] != '--work')]
    source = Path(args[0]) if args else fx.DEFAULT
    work = work_dir or Path(tempfile.mkdtemp(prefix='glred-check-'))
    work.mkdir(parents=True, exist_ok=True)
    copy = work / 'renderer_gl.cpp'
    shutil.copyfile(source, copy)
    original = copy.read_bytes()
    failures = []

    def check(ok, label):
        print(('PASS ' if ok else 'FAIL ') + label)
        if not ok:
            failures.append(label)

    text0 = original.decode('utf-8')
    check(fx.MARK not in text0, 'input is unstaged (no marker)')

    # 1. Apply, then confine every changed line to the allowed helpers.
    text1, changed = fx.apply_text(text0)
    check(changed and fx.MARK in text1, 'apply inserts marker and edits')
    check(not text1.replace('\r\n', '').count('\n'), 'CRLF preserved (no bare LF)')
    # Offsets of each edit's new text; the header search uses the text at that point.
    bad = []
    for old, new in fx.EDITS:
        at = text1.index(new)
        func = enclosing_function(text1, at + (0 if old == fx.RETAINED_HEAD else len(new) - 1))
        if old == fx.RETAINED_HEAD:
            func = 'NativeRetainedGpuBind'
        if func not in ALLOWED:
            bad.append((func, old[:50]))
    check(not bad, 'every edit sits inside an allowed GL-call helper %s' % (bad or ''))
    check(len(fx.EDITS) <= 8, 'edit set is minimal (%d edits)' % len(fx.EDITS))

    # 2. Apply is idempotent on its own output.
    text1b, changed_again = fx.apply_text(text1)
    check(not changed_again and text1b == text1, 'apply is idempotent')

    # 3. Restore is byte-identical to the original.
    text2, restored = fx.restore_text(text1)
    check(restored and text2 == text0, 'restore reproduces the original bytes')
    text2b, restored_again = fx.restore_text(text2)
    check(not restored_again and text2b == text0, 'restore is idempotent')

    # 4. The CLI applies to a copy, and the opt-out leaves the copy untouched.
    stage = HERE / 'fix_gl_state_redundancy.py'
    env = dict(os.environ)
    env.pop('NWII_NO_GL_STATE_REDUNDANCY', None)
    subprocess.run([sys.executable, str(stage), str(copy)], check=True, env=env)
    check(copy.read_bytes() == text1.encode('utf-8'), 'CLI apply matches the module apply')
    subprocess.run([sys.executable, str(stage), '--restore', str(copy)], check=True, env=env)
    check(copy.read_bytes() == original, 'CLI restore is byte-identical')
    env['NWII_NO_GL_STATE_REDUNDANCY'] = '1'
    subprocess.run([sys.executable, str(stage), str(copy)], check=True, env=env)
    check(copy.read_bytes() == original, 'opt-out leaves the file untouched')

    print('RESULT: %s (%d failure(s))' % ('PASS' if not failures else 'FAIL', len(failures)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
