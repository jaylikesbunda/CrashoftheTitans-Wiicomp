"""Make sure every chunk that calls a native lowering can see its overlay header.

`fix_native_lowering.py` adds the overlay includes to the chunks whose text it
rewrites, but the hot split (which runs later) moves whole functions into
`build/crash-hot/hot_*.cpp` with its own prelude, so a rewritten call site can
end up in a unit that never saw the include.  This stage closes that gap: it
scans the staged tree and inserts the missing includes immediately after
`#include "functions.h"`.

Idempotent: a chunk that already has the include is left untouched, and the
stage never duplicates one.  `--restore` removes exactly the includes it added
(it is the same line, so a chunk that legitimately carried it is restored to
the same text).
"""
from pathlib import Path
import sys

PORT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT / 'tools'))
import native_lowering as NL

MARK = '// NATIVE_LOWERING_INCLUDES_V1'
ANCHOR = '#include "functions.h"\n'


def headers():
    """The overlay headers the adopted candidates need, in a stable order."""
    out = []
    for entry in NL.candidates():
        header = entry.get('header')
        if header and header not in out:
            out.append(header)
    return out


def chunk_paths(root):
    paths = []
    for pattern in ('build/crash-hot/hot_*.cpp', 'build/crash-split/output_*.cpp',
                    'build/crash-generated/output_*.cpp'):
        paths.extend(sorted(root.glob(pattern)))
    return paths


def main(restore=False):
    root = PORT
    wanted = headers()
    includes = ['#include "%s"' % (PORT / h).as_posix() for h in wanted]
    changed, added, removed = [], 0, 0
    for path in chunk_paths(root):
        text = path.read_text(errors='surrogateescape')
        needs = 'native_lower::' in text or 'native_lower_' in text
        present = [line for line in includes if line in text]
        if restore:
            if not present:
                continue
            for line in present:
                text = text.replace(line + '\n', '')
                removed += 1
            path.write_text(text, errors='surrogateescape')
            changed.append(path.name)
            continue
        if not needs:
            continue
        missing = [line for line in includes if line not in text]
        if not missing:
            continue
        if ANCHOR not in text:
            raise SystemExit('%s: no #include "functions.h" anchor' % path.name)
        block = MARK + ': native lowering overlays used by a rewritten call site\n' + '\n'.join(missing) + '\n'
        text = text.replace(ANCHOR, ANCHOR + block, 1)
        path.write_text(text, errors='surrogateescape')
        added += len(missing)
        changed.append(path.name)
    verb = 'removed' if restore else 'added'
    print('native lowering includes: %d %s in %d chunk(s)' % (removed if restore else added, verb, len(changed)))
    for name in changed[:10]:
        print('  ' + name)


if __name__ == '__main__':
    main(restore='--restore' in sys.argv)
