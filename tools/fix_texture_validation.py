"""NATIVE_TEXTURE_VALIDATION_V1 -- opt-out trims of two texture-validation repeats (src/hle/texture_cache_gl.cpp only).

Runs after fix_texture_hash_cache.py. Anchors are text that stage leaves alone. Four edits:
  1. helper:  NativeTextureValidationFix() reads NWII_NO_TEXTURE_VALIDATION_FIX (unset means on).
  2. stale:   a hot-key entry whose bytes just failed unchanged() is remembered for the rest of this lookup.
  3. hotstale: when the hashed lookup lands on that same entry, it is erased without a second unchanged() call. The second
     call would repeat the first on the same bytes (a full byte comparison, or a tracker query plus one), so the outcome
     is the same. If the guest writes between the two calls, the erase only costs one re-decode.
  4. efbhash: register_efb_texture() computes the sampled hash only when it does not keep the source copy. efb_guest_unchanged()
     reads the hash only when the source is empty, so a copy that keeps its source never reads it.

Set NWII_NO_TEXTURE_VALIDATION_FIX=1 to get the staged behaviour back for A/B comparison.

Idempotent. Each edit keeps its original text base64-encoded in its marker, so --restore returns the text the hash stage left.
Pass --root to work on another copy of nWiiRuntime. The default root is the build tree used by the other texture stages.
"""
from pathlib import Path
import argparse, base64, re

MARK = 'NATIVE_TEXTURE_VALIDATION_V1'
BLOCK = re.compile(r'/\* ' + MARK + r':(\w+) ([A-Za-z0-9+/=]*) \*/(.*?)/\* END_' + MARK + r':\1 \*/', re.S)
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
    text = edit(text, 'helper', 'using gx::g_state;',
                'using gx::g_state;\n'
                '// NATIVE_TEXTURE_VALIDATION_V1: opt-out switch for the two validation trims below (NWII_NO_TEXTURE_VALIDATION_FIX=1).\n'
                'static bool NativeTextureValidationFix() {\n'
                '    static const bool on = std::getenv("NWII_NO_TEXTURE_VALIDATION_FIX") == nullptr;\n'
                '    return on;\n'
                '}')
    text = edit(text, 'stale', 'const uint8_t* ram_base=nullptr;size_t ram_bank_bytes=0,ram_offset=0;',
                'const uint8_t* ram_base=nullptr;size_t ram_bank_bytes=0,ram_offset=0;\n'
                '    const Entry* stale_hot=nullptr; // NATIVE_TEXTURE_VALIDATION_V1: hot entry whose bytes already failed unchanged()')
    text = edit(text, 'hotstale', 'native_trace::mark("texture.exact_hot");return entry->second.tex;}}}',
                'native_trace::mark("texture.exact_hot");return entry->second.tex;}\n'
                '      if(NativeTextureValidationFix() && entry!=cache.end()) stale_hot=&entry->second;}}')
    text = edit(text, 'dup', 'if(it!=cache.end() && exact && span && !unchanged(it->second)){',
                'if(it!=cache.end() && exact && span && ((NativeTextureValidationFix() && &it->second==stale_hot) || !unchanged(it->second))){')
    text = edit(text, 'efbhash', 'if(!reused)entry.hash=NativeGuestHash(p,bytes);',
                'if(!reused && !(NativeTextureValidationFix() && !entry.source.empty())) entry.hash=NativeGuestHash(p,bytes);')
    return text


FILES = (
    (CACHE, patch_cache),
)


def apply(port, rt):
    for rel, patch in FILES:
        path = rt / rel
        old = read(path)
        new = patch(old)
        if new != old:
            write(path, new)
    print('Texture validation trims: no double byte compare on stale hot entries; EFB copies skip the unused hash. '
          'NWII_NO_TEXTURE_VALIDATION_FIX=1 disables both.')


def restore(port, rt):
    for rel, _ in FILES:
        path = rt / rel
        old = read(path)
        new = BLOCK.sub(lambda m: base64.b64decode(m.group(2)).decode('utf-8', 'surrogateescape'), old)
        if new != old:
            write(path, new)
    print('Texture validation stage removed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    rt = args.root or port / 'build/nwii-full-source/nWiiRuntime'
    (restore if args.restore else apply)(port, rt)
