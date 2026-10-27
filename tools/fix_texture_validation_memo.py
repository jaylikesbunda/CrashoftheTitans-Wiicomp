"""NATIVE_TEXTURE_VALIDATION_MEMO_V1 -- skip repeated unchanged() calls for entries the write tracker verified clean.

Same family as fix_texture_validation.py; runs after it and after fix_fallback_census.py (its anchors include the census text).
Edits:
  1. Entry:    texture_cache_gl.h gains a `memo` (native_texture_memo::Memo), default disarmed. EfbGuest gains one too.
  2. arm:      when the tracker answers clean for an entry, the memo records the PAGE epoch of the entry's own range and the TLUT
               epoch, both sampled BEFORE the query. Only writes to the entry's pages (or a TLUT change) disarm it; writes elsewhere
               leave it armed (per-page epochs, native_ram_pages_epoch in native_guest_arena.cpp).
  3. skip:     unchanged() returns true at once while the memo is valid. Counters: texture_validations (calls into unchanged) and
               texture_validation_memo_skips.
  4. efb:      the same memo for EFB-copy guest snapshots (efb_guest_unchanged): a clean answer arms it, a valid memo skips the query.
               A refreshed snapshot (register_efb_texture) disarms it.
  5. thrash:   an entry whose pages are rewritten every frame never arms, so every validation paid a query and then compared the
               bytes anyway. After 8 such answers in a row the entry skips the query for 64 validations and then probes once
               (native_texture_memo::SkipTrackerQuery/NoteDirtyQuery). Ranges over 64 KiB keep querying: a comparison there costs
               about as much as a query. Only the query is skipped, never the byte comparison.
Soundness is argued in runtime-overlays/native_texture_memo.h. Entries on untracked, sticky, mask-layout or GetWriteWatch pages
answer UINT64_MAX from native_ram_pages_epoch and never arm.
NWII_NO_TEXTURE_VALIDATION_MEMO=1 opts out. The default NWII_TEXTURE_TRACK_MIN lives in native_texture_hash_cache.h.

Idempotent; each edit keeps its original text base64-encoded so --restore is exact.
"""
from pathlib import Path
import argparse, base64, re

MARK = 'NATIVE_TEXTURE_VALIDATION_MEMO_V1'
BLOCK = re.compile(r'/\* ' + MARK + r':(\w+) ([A-Za-z0-9+/=]*) \*/(.*?)/\* END_' + MARK + r':\1 \*/', re.S)
HEADER = 'include/runtime/texture_cache_gl.h'
CACHE = 'src/hle/texture_cache_gl.cpp'
OVERLAY = 'native_texture_memo.h'
port = Path(__file__).resolve().parents[1]

FILES = {
    HEADER: [
        ('entry', 'uint64_t verified_gen=0; /* NATIVE_RAM_WRITE_WATCH_V1 */ uint64_t palette_gen=0;',
         'uint64_t verified_gen=0; /* NATIVE_RAM_WRITE_WATCH_V1 */ uint64_t palette_gen=0; native_texture_memo::Memo memo;'),
        ('efb_entry', 'struct EfbGuest { uint32_t hash; uint32_t bytes; std::vector<uint8_t> source; uint64_t verified_gen=0; uint32_t physical_base=UINT32_MAX; };',
         'struct EfbGuest { uint32_t hash; uint32_t bytes; std::vector<uint8_t> source; uint64_t verified_gen=0; uint32_t physical_base=UINT32_MAX; native_texture_memo::Memo memo; };'),
    ],
    CACHE: [
        ('skip', '    auto unchanged=[&](Entry& entry){',
         '    auto unchanged=[&](Entry& entry){ native_gx_work::Add(native_gx_work::TexValidations);\n'
         '        if (native_texture_memo::Enabled() && native_texture_memo::Valid(entry.memo, span ? nwii::runtime::native_ram_pages_epoch(ram_base,ram_bank_bytes,ram_offset,size) : UINT64_MAX, native_tlut::Read())) {\n'
         '            native_gx_work::Add(native_gx_work::TexMemoSkips);\n'
         '            return true;\n'
         '        }'),
        ('arm', 'native_gx_work::AddTexTrack(newest,entry.verified_gen);',
         'native_gx_work::AddTexTrack(newest,entry.verified_gen);\n'
         '               if (native_texture_memo::Enabled() && native_texture_hash::CleanSince(newest,entry.verified_gen)) native_texture_memo::Arm(entry.memo,memo_epoch,memo_tlut);'),
        # NATIVE_TEXTURE_TRACK_THRASH_V1: entries the tracker cannot help skip the query (never the byte comparison).
        # Anchored on the hash-cache threshold condition, which this stage runs after and later stages leave alone.
        ('query_guard', 'if(write_watch && size>=native_texture_hash::TrackMinBytes()){',
         'if(write_watch && size>=native_texture_hash::TrackMinBytes() && !(native_texture_memo::Enabled() && native_texture_memo::SkipTrackerQuery(entry.memo,size))){'),
        ('dirty', 'entry.verified_gen=generation;return true;',
         'if(native_texture_memo::Enabled()) native_texture_memo::NoteDirtyQuery(entry.memo);\n'
         '               entry.verified_gen=generation;return true;'),
        ('efb_pre', 'const uint64_t newest=native_ram_newest_write(bank,bank_bytes,offset,entry.bytes);',
         'const uint64_t memo_epoch=native_texture_memo::Enabled()?nwii::runtime::native_ram_pages_epoch(bank,bank_bytes,offset,entry.bytes):UINT64_MAX;\n'
         '        if (native_texture_memo::Valid(entry.memo, memo_epoch, 0)) { native_gx_work::Add(native_gx_work::TexMemoSkips); return true; }\n'
         '        const uint64_t newest=native_ram_newest_write(bank,bank_bytes,offset,entry.bytes);'),
        ('efb_arm', 'if(newest!=UINT64_MAX){if(entry.verified_gen && newest<=entry.verified_gen)return true;before=native_ram_generation();}',
         'if(newest!=UINT64_MAX){if(entry.verified_gen && newest<=entry.verified_gen){if(native_texture_memo::Enabled())native_texture_memo::Arm(entry.memo,memo_epoch,0);return true;}before=native_ram_generation();}'),
        ('efb_refresh', 'entry.source.assign(p,p+bytes);entry.verified_gen=before;}',
         'entry.source.assign(p,p+bytes);entry.verified_gen=before;entry.memo.armed=false;}'),
    ],
}
# The arm edit needs the epoch sampled before the query: placed on the query statement itself.
PRE_QUERY = ('const uint64_t newest=native_ram_newest_write(ram_base,ram_bank_bytes,ram_offset,size);',
             'const uint64_t memo_epoch=native_texture_memo::Enabled()?nwii::runtime::native_ram_pages_epoch(ram_base,ram_bank_bytes,ram_offset,size):UINT64_MAX; const uint64_t memo_tlut=native_tlut::Read();\n'
             '           const uint64_t newest=native_ram_newest_write(ram_base,ram_bank_bytes,ram_offset,size);')
HEADER_INCLUDE = '#include "runtime/native_texture_memo.h" // ' + MARK
CACHE_INCLUDE = None


def read(path):
    return path.read_bytes().decode('utf-8', 'surrogateescape')


def write(path, text):
    path.write_bytes(text.encode('utf-8', 'surrogateescape'))


def block(tag, original, new, eol):
    packed = base64.b64encode(original.encode('utf-8', 'surrogateescape')).decode()
    return f'/* {MARK}:{tag} {packed} */{new.replace(chr(10), eol)}/* END_{MARK}:{tag} */'


def edit(text, tag, old, new):
    if f'/* {MARK}:{tag} ' in text:
        return text
    eol = '\r\n' if '\r\n' in text else '\n'
    old_eol = old.replace('\n', eol)
    assert text.count(old_eol) == 1, (tag, text.count(old_eol))
    return text.replace(old_eol, block(tag, old_eol, new.replace('\n', eol), eol), 1)


def prepend_include(text, line):
    if f'/* {MARK}:include ' in text:
        return text
    eol = '\r\n' if '\r\n' in text else '\n'
    return block('include', '', line + '\n', eol) + text


def unblock(text):
    return BLOCK.sub(lambda m: base64.b64decode(m.group(2)).decode('utf-8', 'surrogateescape'), text)


def apply(rt):
    (rt / 'include/runtime').mkdir(parents=True, exist_ok=True)
    (rt / 'include/runtime' / OVERLAY).write_bytes((port / 'runtime-overlays' / OVERLAY).read_bytes())
    for rel, edits in FILES.items():
        path = rt / rel
        text = read(path)
        for tag, old, new in edits:
            text = edit(text, tag, old, new)
        if rel == HEADER:
            text = prepend_include(text, HEADER_INCLUDE)
        if rel == CACHE:
            text = edit(text, 'query', *PRE_QUERY)
        write(path, text)
    print('Texture validation memo: entries verified clean skip unchanged() until a tracked page or the TLUT changes. '
          'NWII_NO_TEXTURE_VALIDATION_MEMO=1 disables it.')


def restore(rt):
    for rel in FILES:
        path = rt / rel
        text = read(path)
        new = unblock(text)
        if new != text:
            write(path, new)
    print('Texture validation memo stage removed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    rt = args.root or port / 'build/nwii-full-source/nWiiRuntime'
    (restore if args.restore else apply)(rt)
