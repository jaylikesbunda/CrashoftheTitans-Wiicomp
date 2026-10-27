"""Install opt-in last-device glyph substitution before normal texture-cache lookup."""
from pathlib import Path

MARK = 'NATIVE_LIVE_PROMPTS_V3'
ANCHOR = '    const TextureKey metadata='
LEGACY_HOOK = '''    // NATIVE_LIVE_PROMPTS_V1: exact source match; no writes to guest memory or the ordinary cache.
    if (stage.width==32 && stage.height==32 && stage.format==6) {
        const uint32_t p=stage.base_addr&0x3fffffffu;
        const bool valid=(p<ctx.mmu.mem1.size() && 4096<=ctx.mmu.mem1.size()-p) ||
            (p>=0x10000000u && p-0x10000000u<ctx.mmu.mem2.size() && 4096<=ctx.mmu.mem2.size()-(p-0x10000000u));
        if (valid) if (GLuint tex=native_prompts::textures().lookup(ctx.mmu.get_ptr(stage.base_addr))) return tex;
    }
'''
V2_HOOK = '''    // NATIVE_LIVE_PROMPTS_V2: decode each candidate once; full source/palette checks guard reuse.
    if (span && stage.width==32 && stage.height==32) {
        auto& prompts=native_prompts::textures();
        int glyph=prompts.probe(stage.base_addr,stage.format,stage.tlut_format,source,size,palette,palette_bytes);
        if(glyph==-2) {
            Image candidate=decode_texture(ctx,stage);
            glyph=prompts.record(stage.base_addr,static_cast<const unsigned char*>(candidate.data));
            UnloadImage(candidate);
        }
        if(GLuint tex=prompts.texture(glyph)) return tex;
    }
'''
HOOK = '''    // NATIVE_LIVE_PROMPTS_V3: resolve the named font slot before inspecting image pixels.
    if (span && stage.width==32 && stage.height==32) {
        auto& prompts=native_prompts::textures();
        const auto now=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
        int glyph=prompts.enabled()?NativePromptSlots().lookup({ctx.mmu.mem1.data(),ctx.mmu.mem1.size()},
            {ctx.mmu.mem2.data(),ctx.mmu.mem2.size()},stage.base_addr,stage.format,now):-1;
        if(glyph<0) {
            glyph=prompts.probe(stage.base_addr,stage.format,stage.tlut_format,source,size,palette,palette_bytes);
            if(glyph==-2) {
                Image candidate=decode_texture(ctx,stage);
                glyph=prompts.record(stage.base_addr,static_cast<const unsigned char*>(candidate.data));
                UnloadImage(candidate);
            }
        }
        if(GLuint tex=prompts.texture(glyph)) return tex;
    }
'''

def patch(text):
    if MARK in text:
        assert HOOK in text, 'partial live prompt stage'
        return text
    if 'NATIVE_LIVE_PROMPTS_V2' in text:
        assert text.count(V2_HOOK)==1, 'legacy V2 prompt hook moved'
        text=text.replace(V2_HOOK,'',1)
    if 'NATIVE_LIVE_PROMPTS_V1' in text:
        assert text.count(LEGACY_HOOK)==1, 'legacy prompt hook moved'
        text=text.replace(LEGACY_HOOK,'',1)
    assert text.count(ANCHOR) == 1, 'texture lookup anchor moved'
    assert text.count('void TextureCache::clear() {') == 1, 'texture cleanup anchor moved'
    include='#include "runtime/native_prompt_textures.h"\n'
    if include not in text: text = include + text
    slots='#include "runtime/native_prompt_slots.h"\n#include <chrono>\nstatic native_prompts::FontSlots& NativePromptSlots(){static native_prompts::FontSlots slots;return slots;}\n'
    if 'static native_prompts::FontSlots& NativePromptSlots()' not in text:text=slots+text
    text = text.replace(ANCHOR, HOOK + ANCHOR, 1)
    if '    native_prompts::textures().clear();' not in text:
        text=text.replace('void TextureCache::clear() {', 'void TextureCache::clear() {\n    native_prompts::textures().clear();', 1)
    if '    NativePromptSlots().clear();' not in text:
        text=text.replace('void TextureCache::clear() {','void TextureCache::clear() {\n    NativePromptSlots().clear();',1)
    return text

def apply(port):
    rt = port / 'build/nwii-full-source/nWiiRuntime'
    path = rt / 'src/hle/texture_cache_gl.cpp'
    before = path.read_text(encoding='utf-8'); after = patch(before)
    for name in ('native_prompt_device.h', 'native_prompt_textures.h','native_prompt_slots.h'):
        data = (port / 'runtime-overlays' / name).read_bytes()
        dest = rt / 'include/runtime' / name
        if not dest.exists() or dest.read_bytes()!=data: dest.write_bytes(data)
    if before != after: path.write_text(after,encoding='utf-8')

if __name__ == '__main__':
    apply(Path(__file__).resolve().parents[1])
    print('Live prompts: opt-in exact glyph substitution installed.')
