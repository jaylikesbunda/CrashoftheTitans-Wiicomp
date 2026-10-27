"""Texture cache: stale render-to-texture entries and whole-cache flushes.

1. EFB copies were registered by guest address only and never invalidated. A
   texture the game later uploads to a reused address was still answered with the
   old render-to-texture image (wrong or dark picture on a character, missing
   objects). The registration now remembers a sampled hash of the guest bytes at
   the destination; when the guest writes there later the hash differs and the
   normal decoded texture is used.
2. The cache dropped *every* texture (deleting them all) as soon as it held 513
   entries, so any level with more textures, or any texture rewritten each frame
   (each new hash is a new entry), forced a mass re-decode and a stall. It now
   evicts the least recently used entries once a ~640 MB / 6000-entry budget is
   exceeded, never touching entries used in the last 64 lookups.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
root = port / 'build/nwii-full-source/nWiiRuntime'
marker = 'NATIVE_TEXTURE_CACHE_V1'

h = root / 'include/runtime/texture_cache_gl.h'
t = h.read_text()
if marker not in t:
    old = '    std::unordered_map<TextureKey, GLuint, TextureKeyHash> cache;\n'
    assert t.count(old) == 1
    new = '''    // NATIVE_TEXTURE_CACHE_V1
    struct Entry { GLuint tex; uint32_t bytes; uint64_t stamp; };
    std::unordered_map<TextureKey, Entry, TextureKeyHash> cache;
    uint64_t tick = 0;
    size_t total_bytes = 0;
    struct EfbGuest { uint32_t hash; uint32_t bytes; };
    std::unordered_map<uint32_t, EfbGuest> efb_guest;
    bool efb_guest_unchanged(CPUContext& ctx, uint32_t addr);
    void evict_old();
'''
    t = t.replace(old, new)
    h.write_text(t)

c = root / 'src/hle/texture_cache_gl.cpp'
t = c.read_text()
if marker not in t:
    t = t.replace('#include <cstring>\n', '#include <cstring>\n#include <vector>\n#include <algorithm>\n', 1)

    old = 'namespace nwii::runtime::hle {\n\nusing gx::g_state;\n'
    assert t.count(old) == 1
    t = t.replace(old, 'namespace nwii::runtime { extern CPUContext *g_ctx_ptr; }\n\n' + old + '''
// NATIVE_TEXTURE_CACHE_V1: sampled hash shared by EFB-copy validation.
static uint32_t NativeGuestHash(const uint8_t* p, uint32_t bytes) {
    uint32_t stride = bytes > 32 * 4096 ? bytes / 4096 : 32;
    uint32_t hash = 2166136261u;
    for (uint32_t off = 0; off + 4 <= bytes; off += stride) {
        uint32_t w;
        std::memcpy(&w, p + off, 4);
        hash = (hash ^ w) * 16777619u;
    }
    return hash;
}
''')

    old = '''    for (auto& pair : cache) {
        glDeleteTextures(1, &pair.second);
    }
    cache.clear();'''
    assert t.count(old) == 1
    t = t.replace(old, '''    for (auto& pair : cache) {
        glDeleteTextures(1, &pair.second.tex);
    }
    cache.clear();
    total_bytes = 0;''')

    old = '''void TextureCache::register_efb_texture(uint32_t addr, GLuint tex) {
    efb_textures[addr & 0x01FFFFFF] = tex;
}'''
    assert t.count(old) == 1
    t = t.replace(old, '''void TextureCache::register_efb_texture(uint32_t addr, GLuint tex) {
    efb_textures[addr & 0x01FFFFFF] = tex;
    if (g_ctx_ptr) {
        uint32_t bytes = gx::g_state.efbW * gx::g_state.efbH * 2;
        bytes = std::min<uint32_t>(std::max<uint32_t>(bytes, 32), 1u << 20);
        if (const uint8_t* p = g_ctx_ptr->mmu.get_ptr(addr))
            efb_guest[addr & 0x01FFFFFF] = {NativeGuestHash(p, bytes), bytes};
    }
}

bool TextureCache::efb_guest_unchanged(CPUContext& ctx, uint32_t addr) {
    auto it = efb_guest.find(addr & 0x01FFFFFF);
    if (it == efb_guest.end()) return true;
    const uint8_t* p = ctx.mmu.get_ptr(addr);
    if (!p) return true;
    return NativeGuestHash(p, it->second.bytes) == it->second.hash;
}

void TextureCache::evict_old() {
    std::vector<std::pair<uint64_t, TextureKey>> order;
    order.reserve(cache.size());
    for (const auto& pair : cache) order.emplace_back(pair.second.stamp, pair.first);
    std::sort(order.begin(), order.end(),
              [](const auto& a, const auto& b) { return a.first < b.first; });
    const size_t target_bytes = (640ull << 20) * 7 / 10;
    const size_t target_count = 4200;
    for (const auto& item : order) {
        if (total_bytes <= target_bytes && cache.size() <= target_count) break;
        if (item.first + 64 > tick) break; // in use by the draws being set up now
        auto it = cache.find(item.second);
        if (it == cache.end()) continue;
        glDeleteTextures(1, &it->second.tex);
        total_bytes -= std::min<size_t>(total_bytes, it->second.bytes);
        cache.erase(it);
    }
}''')

    old = '''    if (GLuint efb = find_efb_texture(stage.base_addr))
        return efb;
'''
    assert t.count(old) == 1
    t = t.replace(old, '''    ++tick;
    if (GLuint efb = find_efb_texture(stage.base_addr)) {
        if (efb_guest_unchanged(ctx, stage.base_addr))
            return efb;
    }
''')

    old = '''    if (it != cache.end()) {
        return it->second;
    }

    if (cache.size() > 512)
        clear();
'''
    assert t.count(old) == 1
    t = t.replace(old, '''    if (it != cache.end()) {
        it->second.stamp = tick;
        return it->second.tex;
    }

    if (cache.size() > 6000 || total_bytes > (640ull << 20))
        evict_old();
''')

    old = '    cache[key] = tex;\n    return tex;'
    assert t.count(old) == 1
    t = t.replace(old, '''    const uint32_t entry_bytes = stage.width * stage.height * 4;
    cache[key] = Entry{tex, entry_bytes, tick};
    total_bytes += entry_bytes;
    return tex;''')
    c.write_text(t)
print('Texture cache: stale EFB entries invalidated, LRU eviction instead of full flush.')
