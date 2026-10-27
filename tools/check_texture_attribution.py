"""Checks for tools/fix_texture_attribution.py. Works in a scratch directory; nothing in the repository is written.

1. Stage on a synthetic texture_cache_gl.cpp that carries every anchor line (one per edit, the text the memo stage leaves):
   one marker pair per edit, every marker closed, idempotent, exact restore.
2. The eviction budget expression keeps the soft-limit cap (kTextureEvictPerPass) and bounds the hard pass (512).

Usage: python check_texture_attribution.py [--work DIR]
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fix_texture_attribution as fx  # noqa: E402


def main(argv):
    work = Path(argv[argv.index('--work') + 1]) if '--work' in argv else Path(tempfile.mkdtemp(prefix='texture-attribution-'))
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    failures = []

    def check(ok, label):
        print(('PASS ' if ok else 'FAIL ') + label)
        if not ok:
            failures.append(label)

    root = work / 'nwii/src'
    target = root / fx.CACHE
    target.parent.mkdir(parents=True, exist_ok=True)
    original = ''.join(old + '\r\n' for _, old, _ in fx.EDITS).encode('utf-8')
    target.write_bytes(original)
    stage = [sys.executable, str(HERE / 'fix_texture_attribution.py'), '--root', str(root)]
    subprocess.run(stage, check=True)
    applied = target.read_bytes()
    n = len(fx.EDITS)
    check(applied.count(b'/* NATIVE_TEXTURE_ATTRIBUTION_V1:') == n, 'one marker per edit (%d)' % n)
    check(applied.count(b'/* END_NATIVE_TEXTURE_ATTRIBUTION_V1:') == n, 'every marker closed')
    check(b'texture.track_query' in applied and b'texture.memcmp' in applied and b'texture.evict' in applied,
          'track query, memcmp and eviction scopes are present')
    check(b'evict_names.push_back' in applied and b'NativeTextureDelete(GLsizei(evict_names.size())' in applied,
          'eviction collects names and deletes them in one call')
    check(b'hard ? size_t(512) : native_cache_bounds::kTextureEvictPerPass' in applied,
          'hard limit is bounded per pass, soft limit keeps kTextureEvictPerPass')
    subprocess.run(stage, check=True)
    check(target.read_bytes() == applied, 'apply is idempotent')
    subprocess.run(stage + ['--restore'], check=True)
    check(target.read_bytes() == original, 'restore reproduces the original bytes')

    print('RESULT: %s (%d failure(s))' % ('PASS' if not failures else 'FAIL', len(failures)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
