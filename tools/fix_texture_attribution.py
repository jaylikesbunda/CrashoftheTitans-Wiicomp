"""NATIVE_TEXTURE_ATTRIBUTION_V1 -- attribute the texture lookup's time, and make eviction batch its GL deletes.

Where the time of texture.lookup_hash goes (F3 scopes, nested inside it):
  texture.track_query   the write tracker query (arming pages included), in unchanged() and on the miss path
  texture.memcmp        the byte comparison (memcmp bytes: texture_memcmp_bytes)
  texture.evict         one eviction pass (entries: texture_evicted_entries, passes: texture_evict_passes)
  texture.decode, texture.upload   as before
Pages armed by the queries are exported as texture_pages_armed (the delta of the arena counter around each query).

Eviction changes (the 8-9 ms lookup_hash spikes were not explained by decode; a pass past the hard limits deleted about 1400
textures one glDeleteTextures call at a time, each with an invalidate_textures() pass):
  * the names of one pass are collected and deleted with one NativeTextureDelete call (texture_evict_delete_names counts them);
  * past the hard limits a pass is capped at kTextureEvictHardPass entries too (evict_pending carries the rest into the next
    misses), so no single lookup absorbs a whole sweep. The soft-limit cap (kTextureEvictPerPass) is unchanged.
NWII_NO_TEXTURE_EVICT_BUDGET=1 restores the unbounded pass (the batch delete stays).

Edits keep their original text base64-encoded in markers, so --restore is exact. Idempotent. Runs after fix_texture_validation_memo.py
(its arm/query anchors are that stage's text). Pass --root to work on another copy of nWiiRuntime/src.
"""
from pathlib import Path
import argparse, base64, re

MARK = 'NATIVE_TEXTURE_ATTRIBUTION_V1'
BLOCK = re.compile(r'/\* ' + MARK + r':(\w+) ([A-Za-z0-9+/=]*) \*/(.*?)/\* END_' + MARK + r':\1 \*/', re.S)
CACHE = 'hle/texture_cache_gl.cpp'
port = Path(__file__).resolve().parents[1]

EDITS = [
    ('track_query',
     'const uint64_t newest=native_ram_newest_write(ram_base,ram_bank_bytes,ram_offset,size);',
     'const uint64_t armed_before=nwii::runtime::native_ram_pages_armed_total(); '
     'const uint64_t newest=[&]{ native_trace::Scope trace_query("texture.track_query"); return native_ram_newest_write(ram_base,ram_bank_bytes,ram_offset,size); }(); '
     'native_gx_work::Add(native_gx_work::TexPagesArmed,nwii::runtime::native_ram_pages_armed_total()-armed_before);'),
    ('cmp_dirty',
     'if(native_gx_work::CountedMemcmp(source,entry.source.data(),size)!=0)return false;',
     '{ native_trace::Scope trace_cmp("texture.memcmp"); if(native_gx_work::CountedMemcmp(source,entry.source.data(),size)!=0)return false; }'),
    ('cmp_direct',
     'return native_gx_work::CountedMemcmp(source,entry.source.data(),size)==0;};',
     'native_trace::Scope trace_cmp("texture.memcmp"); return native_gx_work::CountedMemcmp(source,entry.source.data(),size)==0;};'),
    ('evict_begin',
     'if (tick < evict_retry_tick) return;',
     'if (tick < evict_retry_tick) return; native_trace::Scope trace_evict("texture.evict"); std::vector<GLuint> evict_names; // one glDeleteTextures per pass'),
    ('evict_budget',
     'const size_t budget = budgeted && !hard ? native_cache_bounds::kTextureEvictPerPass : SIZE_MAX;',
     'const size_t budget = budgeted ? (hard ? size_t(512) : native_cache_bounds::kTextureEvictPerPass) : SIZE_MAX;'),
    ('evict_erase',
     'mip_ready.erase(it->second.tex); NativeTextureDelete(1, &it->second.tex);',
     'mip_ready.erase(it->second.tex); evict_names.push_back(it->second.tex);'),
    ('evict_done',
     'const bool capped = removed >= budget && over();',
     'if (!evict_names.empty()) { NativeTextureDelete(GLsizei(evict_names.size()), evict_names.data()); native_gx_work::Add(native_gx_work::TexEvictDeletes, evict_names.size()); } '
     'native_gx_work::Add(native_gx_work::TexEvictPasses); native_gx_work::Add(native_gx_work::TexEvicted, evicted); '
     'const bool capped = removed >= budget && over();'),
    ('miss_query',
     'if(write_watch && span && native_ram_newest_write(ram_base,ram_bank_bytes,ram_offset,size)!=UINT64_MAX)generation_before=native_ram_generation();',
     'if(write_watch && span){ native_trace::Scope trace_query("texture.track_query"); if(native_ram_newest_write(ram_base,ram_bank_bytes,ram_offset,size)!=UINT64_MAX)generation_before=native_ram_generation(); }'),
]


def read(path):
    return path.read_bytes().decode('utf-8', 'surrogateescape')


def write(path, text):
    path.write_bytes(text.encode('utf-8', 'surrogateescape'))


def edit(text, tag, old, new):
    if f'/* {MARK}:{tag} ' in text:
        return text
    assert text.count(old) == 1, (tag, text.count(old))
    packed = base64.b64encode(old.encode('utf-8', 'surrogateescape')).decode()
    return text.replace(old, f'/* {MARK}:{tag} {packed} */{new}/* END_{MARK}:{tag} */', 1)


def unblock(text):
    return BLOCK.sub(lambda m: base64.b64decode(m.group(2)).decode('utf-8', 'surrogateescape'), text)


def apply(rt):
    path = rt / CACHE
    text = read(path)
    for tag, old, new in EDITS:
        text = edit(text, tag, old, new)
    write(path, text)
    print('Texture attribution: lookup scopes (track_query, memcmp, evict), pages armed, batched eviction deletes.')


def restore(rt):
    path = rt / CACHE
    text = read(path)
    new = unblock(text)
    if new != text:
        write(path, new)
    print('Texture attribution stage removed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    rt = args.root or port / 'build/nwii-full-source/nWiiRuntime/src'
    (restore if args.restore else apply)(rt)
