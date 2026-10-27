"""Keep the timestamps of files whose content did not change across a build pass, so ninja only recompiles what really changed.

The pipeline re-stages the same files every pass (setup_full_build rewrites cpu_context.h, lazy-FPU strips guards that codegen-slim puts back,
the hot-split stage deletes and rewrites build/crash-split and build/crash-hot, ...). The final bytes are identical, but every rewrite bumps the
modification time, and cpu_context.h is included by every translation unit, so the build recompiled everything every time.

  preserve_mtimes.py snapshot   at the start of a pass: record sha256 + mtime of every tracked file
  preserve_mtimes.py restore    before the compile: a file whose content equals its snapshot gets its old mtime back

Tracked: the generated chunks (crash-generated, crash-split, crash-hot) and the staged runtime sources/headers. Nothing is ever written
except timestamps. Stale or missing snapshot entries simply mean the file keeps its new timestamp (a rebuild, never a wrong skip).
"""
from pathlib import Path
import hashlib, json, os, sys
p = Path(__file__).resolve().parents[1]
MANIFEST = p / 'build/mtime-manifest.json'
ROOTS = [p / 'build/crash-generated', p / 'build/crash-split', p / 'build/crash-hot', p / 'build/nwii-full-source/nWiiRuntime/src', p / 'build/nwii-full-source/nWiiRuntime/include']
EXT = {'.cpp', '.h', '.hpp', '.inc', '.c'}


def tracked():
    for root in ROOTS:
        if root.exists():
            for f in root.rglob('*'):
                if f.is_file() and f.suffix in EXT:
                    yield f


def digest(f):
    h = hashlib.sha256()
    with f.open('rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == 'snapshot':
        data = {}
        for f in tracked():
            data[f.relative_to(p).as_posix()] = [digest(f), f.stat().st_mtime_ns]
        MANIFEST.write_text(json.dumps(data))
        print('snapshot: %d files' % len(data))
    elif mode == 'restore':
        if not MANIFEST.exists():
            print('restore: no snapshot, nothing to do')
            return
        old = json.loads(MANIFEST.read_text())
        kept = changed = new = 0
        for f in tracked():
            rel = f.relative_to(p).as_posix()
            if rel not in old:
                new += 1
                continue
            digest_old, mtime_old = old[rel]
            if f.stat().st_mtime_ns != mtime_old:
                if digest(f) == digest_old:
                    os.utime(f, ns=(mtime_old, mtime_old))
                    kept += 1
                else:
                    changed += 1
        print('restore: %d timestamps kept, %d files really changed, %d new' % (kept, changed, new))
    else:
        raise SystemExit('usage: preserve_mtimes.py snapshot|restore')


if __name__ == '__main__':
    main()
