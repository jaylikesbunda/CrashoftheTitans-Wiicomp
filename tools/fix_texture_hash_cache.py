"""NATIVE_TEXTURE_HASH_CACHE_V1 -- texture validation: when is a cached texture's source still the same bytes?

What a lookup does in the staged texture cache (src/hle/texture_cache_gl.cpp):
  * A lookup whose metadata is in hot_keys (address, size, format, TLUT location) is validated by unchanged(): the source is
    compared with the stored copy, unless the OS write tracker proves that no page of it was written since it was verified.
    The palette is compared only when the TLUT epoch moved. The sampled source hash and the TLUT hash run only on a hot-key
    miss (first use, changed content, eviction), not on every lookup.
  * The tracker is native_ram_newest_write(): GetWriteWatch under arena-off (ram_write_watch.cpp), the software protection
    tracker under the mirrored arena with NWII_ARENA_TRACK=1, and UINT64_MAX (untracked, so a byte comparison) otherwise. The
    default arena build does not enable the software tracker, so there every hot hit is a full byte comparison.

What this stage changes (all inside the texture cache; nothing else is touched):
  1. include/runtime/native_texture_hash_cache.h (from runtime-overlays): the shared rules. CleanSince() is the single
     definition of "the tracker proves the source unchanged"; Enabled() is the kill switch; TrackMinBytes() is the threshold.
  2. unchanged() decides through CleanSince() (same condition as before, one definition).
  3. The tracker threshold reads NWII_TEXTURE_TRACK_MIN; the default stays 65536 bytes.
  4. NWII_NO_TEXTURE_HASH_CACHE=1 turns off the hot-key shortcut and the tracker: every lookup hashes and compares, as before.

Soundness, in short: the cached texture is returned without a byte comparison only when CleanSince() holds, i.e. the tracker
answered for every page of the source, the entry was verified at a generation sampled before its bytes were read, and no
page was stamped with a newer generation since. A page written after verification is stamped by the next query, so the
newer stamp fails CleanSince() and the bytes are compared. An untracked answer (UINT64_MAX) always compares. The stage does
not change what is cached or when entries are verified.

Idempotent. Every edit keeps its original text base64-encoded in its marker, so --restore returns the exact staged text.
Pass --root to work on another copy of nWiiRuntime. Runs after fix_cache_bounds.py (it anchors on text that stage leaves alone).
"""
from pathlib import Path
import argparse, base64, re

MARK = 'NATIVE_TEXTURE_HASH_CACHE_V1'
BLOCK = re.compile(r'/\* ' + MARK + r':(\w+) ([A-Za-z0-9+/=]*) \*/(.*?)/\* END_' + MARK + r':\1 \*/', re.S)
HEADER = 'include/runtime/native_texture_hash_cache.h'
CACHE = 'src/hle/texture_cache_gl.cpp'
port = Path(__file__).resolve().parents[1]


def read(path):
    return path.read_bytes().decode('utf-8', 'surrogateescape')


def write(path, text):
    path.write_bytes(text.encode('utf-8', 'surrogateescape'))


def edit(text, tag, old, new):
    """Replace `old` by a restorable block holding `new`; a no-op when this tag is already applied."""
    if f'/* {MARK}:{tag} ' in text:
        return text
    eol = '\r\n' if '\r\n' in text else '\n'
    old = old.replace('\n', eol)
    new = new.replace('\n', eol)
    assert text.count(old) == 1, (tag, text.count(old))
    packed = base64.b64encode(old.encode('utf-8', 'surrogateescape')).decode()
    return text.replace(old, f'/* {MARK}:{tag} {packed} */{new}/* END_{MARK}:{tag} */', 1)


def unblock(text):
    return BLOCK.sub(lambda m: base64.b64decode(m.group(2)).decode('utf-8', 'surrogateescape'), text)


def patch_cache(text):
    # Anchored on the TLUT epoch include: stage-owned text that no other texture-cache stage rewrites.
    text = edit(text, 'inc', '#include "runtime/native_tlut_epoch.h"\n',
                '#include "runtime/native_tlut_epoch.h"\n#include "runtime/native_texture_hash_cache.h"\n')
    # The kill switch disables the tracker; without it the byte comparison runs for every validation.
    text = edit(text, 'gate', 'static const bool write_watch=std::getenv("NWII_NO_WRITE_WATCH")==nullptr;',
                'static const bool write_watch=std::getenv("NWII_NO_WRITE_WATCH")==nullptr && native_texture_hash::Enabled();')
    # The hot-key shortcut (no hash, no lookup) is disabled by the kill switch.
    text = edit(text, 'hot', 'if(exact && span){auto hot=hot_keys.find(metadata);',
                'if(exact && span && native_texture_hash::Enabled()){auto hot=hot_keys.find(metadata);')
    # One threshold, from the environment (default unchanged).
    text = edit(text, 'track', 'if(write_watch && size>=65536){',
                'if(write_watch && size>=native_texture_hash::TrackMinBytes()){')
    # One rule for "the tracker proves the bytes unchanged".
    text = edit(text, 'clean', 'if(entry.verified_gen && newest<=entry.verified_gen){native_trace::mark("texture.write_watch_clean");return true;}',
                'if(native_texture_hash::CleanSince(newest,entry.verified_gen)){native_trace::mark("texture.write_watch_clean");return true;}')
    return text


FILES = (
    (CACHE, patch_cache),
)


def apply(port, rt):
    shared = (port / 'runtime-overlays/native_texture_hash_cache.h').read_text()
    target = rt / HEADER
    if not target.exists() or target.read_text() != shared:
        target.write_text(shared)
    for rel, patch in FILES:
        path = rt / rel
        old = read(path)
        new = patch(old)
        if new != old:
            write(path, new)
    print('Texture validation: tracker-proven entries skip the byte comparison; NWII_NO_TEXTURE_HASH_CACHE=1 disables it.')


def restore(port, rt):
    for rel, _ in FILES:
        path = rt / rel
        old = read(path)
        new = unblock(old)
        if new != old:
            write(path, new)
    (rt / HEADER).unlink(missing_ok=True)
    print('Texture hash cache stage removed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    rt = args.root or port / 'build/nwii-full-source/nWiiRuntime'
    (restore if args.restore else apply)(port, rt)
