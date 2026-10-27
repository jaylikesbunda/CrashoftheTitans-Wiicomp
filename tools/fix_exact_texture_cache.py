"""Full-content hot texture validation, without serial sampled hash on hits."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
h=rt/'include/runtime/texture_cache_gl.h';s=h.read_text()
if 'NATIVE_EXACT_TEXTURE_HOT_V1' not in s:
    (p/'runtime-overlays/exact-texture-before.h').write_text(s)
    s=s.replace('#include <cstdlib>','#include <cstdlib>\n#include <vector>',1)
    s=s.replace('struct Entry { GLuint tex; uint32_t bytes; uint64_t stamp; };','struct Entry { GLuint tex; uint32_t bytes; uint64_t stamp; std::vector<uint8_t> source,palette; };')
    s=s.replace('    uint64_t tick = 0;','    // NATIVE_EXACT_TEXTURE_HOT_V1: metadata -> last validated content key.\n    std::unordered_map<TextureKey,TextureKey,TextureKeyHash> hot_keys;\n    uint64_t tick = 0;',1)
    h.write_text(s)
f=rt/'src/hle/texture_cache_gl.cpp';s=f.read_text()
if 'NATIVE_EXACT_TEXTURE_HOT_V1' not in s:
    (p/'runtime-overlays/exact-texture-before.cpp').write_text(s)
    s=s.replace('    cache.clear();','    cache.clear();hot_keys.clear();',1)
    s=s.replace('void TextureCache::evict_old() {','void TextureCache::evict_old() {\n    hot_keys.clear();',1)
    anchor='    uint32_t stride = size > 32 * 4096 ? size / 4096 : 32;'
    code='''    // NATIVE_EXACT_TEXTURE_HOT_V1: vectorized full comparison catches unsampled edits.
    static const bool exact=std::getenv("NWII_NO_EXACT_TEXTURE_CACHE")==nullptr;
    const uint32_t physical=stage.base_addr&0x3fffffffu;
    const uint8_t* source=ctx.mmu.get_ptr(stage.base_addr);
    const bool span=source && ((physical<ctx.mmu.mem1.size() && size<=ctx.mmu.mem1.size()-physical) ||
        (physical>=0x10000000u && physical-0x10000000u<ctx.mmu.mem2.size() && size<=ctx.mmu.mem2.size()-(physical-0x10000000u)));
    uint32_t palette_bytes=stage.format==8?32:stage.format==9?512:stage.format==10?32768:0;
    if(stage.tlut_offset>=sizeof(gx::g_state.tlutMem))palette_bytes=0;
    else palette_bytes=std::min(palette_bytes,uint32_t(sizeof(gx::g_state.tlutMem)-stage.tlut_offset));
    const uint8_t* palette=palette_bytes?gx::g_state.tlutMem+stage.tlut_offset:nullptr;
    const TextureKey metadata={stage.base_addr,stage.width,stage.height,stage.format,stage.tlut_offset,stage.tlut_format,0};
    auto unchanged=[&](const Entry& entry){return span && entry.source.size()==size && entry.palette.size()==palette_bytes &&
       (!size || std::memcmp(source,entry.source.data(),size)==0) && (!palette_bytes || std::memcmp(palette,entry.palette.data(),palette_bytes)==0);};
    if(exact && span){auto hot=hot_keys.find(metadata);if(hot!=hot_keys.end()){auto entry=cache.find(hot->second);
      if(entry!=cache.end() && unchanged(entry->second)){entry->second.stamp=tick;native_trace::mark("texture.exact_hot");return entry->second.tex;}}}
'''
    assert anchor in s;s=s.replace(anchor,code+anchor,1)
    s=s.replace('    if (it != cache.end()) {\n        native_trace::mark("texture.hit");','''    if(it!=cache.end() && exact && span && !unchanged(it->second)){
        // A sampled-hash collision must not reuse stale texels or a stale palette.
        total_bytes-=it->second.bytes;glDeleteTextures(1,&it->second.tex);cache.erase(it);it=cache.end();
        native_trace::mark("texture.content_changed");
    }
    if (it != cache.end()) {
        if(exact && span)hot_keys[metadata]=key;
        native_trace::mark("texture.hit");''',1)
    s=s.replace('    total_bytes += entry_bytes;','''    total_bytes += entry_bytes;
    if(exact && span){auto& entry=cache.at(key);entry.source.assign(source,source+size);if(palette_bytes)entry.palette.assign(palette,palette+palette_bytes);
       entry.bytes+=size+palette_bytes;total_bytes+=size+palette_bytes;hot_keys[metadata]=key;}
''',1)
    f.write_text(s)
print('Exact texture hot cache staged; unchanged hits bypass sampled hashing, collisions invalidate.')
# EFB texture ownership is unchanged. Validate every byte of the existing
# destination tracking range, rather than only its sampled four-byte words.
s=h.read_text()
if 'std::vector<uint8_t> source; };' not in s:
    s=s.replace('struct EfbGuest { uint32_t hash; uint32_t bytes; };','struct EfbGuest { uint32_t hash; uint32_t bytes; std::vector<uint8_t> source; };')
    h.write_text(s)
s=f.read_text()
if 'NATIVE_EXACT_EFB_DEST_V1' not in s:
    old='''        if (const uint8_t* p = g_ctx_ptr->mmu.get_ptr(addr))
            efb_guest[addr & 0x01FFFFFF] = {NativeGuestHash(p, bytes), bytes};'''
    new='''        if (const uint8_t* p = g_ctx_ptr->mmu.get_ptr(addr)) {
            auto& entry=efb_guest[addr & 0x01FFFFFF];entry={NativeGuestHash(p,bytes),bytes,{}};
            // NATIVE_EXACT_EFB_DEST_V1: preserve the established tracking range.
            const uint32_t physical=addr&0x3fffffffu;auto& mmu=g_ctx_ptr->mmu;
            const bool span=(physical<mmu.mem1.size() && bytes<=mmu.mem1.size()-physical) ||
              (physical>=0x10000000u && physical-0x10000000u<mmu.mem2.size() && bytes<=mmu.mem2.size()-(physical-0x10000000u));
            static const bool exact=std::getenv("NWII_NO_EXACT_TEXTURE_CACHE")==nullptr;
            if(exact && span)entry.source.assign(p,p+bytes);
        }'''
    assert old in s;s=s.replace(old,new,1)
    s=s.replace('    return NativeGuestHash(p, it->second.bytes) == it->second.hash;','    if(!it->second.source.empty())return std::memcmp(p,it->second.source.data(),it->second.bytes)==0;\n    return NativeGuestHash(p, it->second.bytes) == it->second.hash;',1)
    f.write_text(s)
print('EFB destination reuse now validates all bytes in its established tracking range.')
