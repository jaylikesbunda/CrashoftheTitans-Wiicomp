"""Mipmapped texture sampling (distant/small surfaces such as the XP orbs shimmer and look noisy without it).

Before: the texture cache uploaded level 0 only and the sampler collapsed every GX min filter to plain LINEAR/NEAREST, so any texture
minified on screen (small or distant objects) was point-sampled from the full-size image: aliasing noise that gets worse the smaller the
object is. Hardware samples a mip level chosen from the screen-space derivative, between the sampler's LOD clamps, with a LOD bias.

Now:
  * every texture the cache uploads gets a mip chain (glGenerateMipmap from level 0; the box filter approximates the game's own chain);
  * the sampler maps BP texmode0 min-filter bits (0 nearest, 1 near/mip-near, 2 near/mip-lin, 4 linear, 5 lin/mip-near, 6 lin/mip-lin) to the
    GL filters, applies texmode0's LOD bias (signed, 1/32 steps) and texmode1's min/max LOD clamps (1/16 steps). A game that sets max LOD 0
    therefore still samples level 0 only, exactly as before;
  * EFB-copy textures have no mips, so they are limited to level 0 (GL_TEXTURE_MAX_LEVEL=0) to stay complete under a mip filter.
NWII_NO_MIPMAPS=1 restores the old behaviour for A/B comparison. Idempotent.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime/src/hle'
MARK = 'NATIVE_MIPMAPS_V1'


def apply(path, old, new, probe=None):
    s = path.read_text(errors='surrogateescape')
    if (probe or new) in s:
        return
    assert s.count(old) == 1, (path.name, old[:80], s.count(old))
    path.write_text(s.replace(old, new, 1), errors='surrogateescape')


tc = rt / 'texture_cache_gl.cpp'
apply(tc, '    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);\n    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);\n    UnloadImage(img);\n\n    const uint32_t entry_bytes = stage.width * stage.height * 4;',
      '    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);\n    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);\n'
      '    static const bool mipmaps = std::getenv("NWII_NO_MIPMAPS") == nullptr; // ' + MARK + '\n'
      '    if (mipmaps) { native_trace::GPUScope mip_scope("gpu.texture_transfer_us", 1); glGenerateMipmap(GL_TEXTURE_2D); }\n'
      '    UnloadImage(img);\n\n'
      '    const uint32_t entry_bytes = stage.width * stage.height * 4 + (mipmaps ? stage.width * stage.height * 4 / 3 : 0);',
      'static const bool mipmaps = std::getenv("NWII_NO_MIPMAPS")')

r = rt / 'gx/renderer_gl.cpp'
s = r.read_text(errors='surrogateescape')
FN_PROBE = 'static const bool mipmaps=std::getenv("NWII_NO_MIPMAPS")==nullptr;\n  const uint64_t resolved'
if FN_PROBE not in s:
    a = s.index('static void NativeApplyTextureSampler(unsigned unit, uint32_t mode) {')
    b = s.index('static void NativeDrawArchive(', a)
    new_fn = '''static void NativeApplyTextureSampler(unsigned unit, uint32_t mode, uint32_t mode1 = 0) {
  static GLuint samplers[8]={};
  if(unit>=8)return;
  if(!samplers[unit])glGenSamplers(1,&samplers[unit]);
  // NATIVE_SAMPLER_MODE_CACHE_V1: only these bits affect current parameters.
  // NATIVE_MIPMAPS_V1: min filter (bits 5-7), LOD bias (bits 9-16) and the texmode1 LOD clamps now matter too.
  static uint64_t previous[8]={};static bool have[8]={};
  static const bool cache=std::getenv("NWII_NO_SAMPLER_MODE_CACHE")==nullptr;
  static const bool mipmaps=std::getenv("NWII_NO_MIPMAPS")==nullptr;
  const uint64_t resolved=uint64_t(mode&0x1ffffu)|(uint64_t(mode1&0xffffu)<<32);
  if(!cache || !have[unit] || previous[unit]!=resolved){
  auto wrap=[](unsigned value)->GLint {
    return value==1?GL_REPEAT:value==2?GL_MIRRORED_REPEAT:GL_CLAMP_TO_EDGE;
  };
  glSamplerParameteri(samplers[unit],GL_TEXTURE_WRAP_S,wrap(mode&3));
  glSamplerParameteri(samplers[unit],GL_TEXTURE_WRAP_T,wrap((mode>>2)&3));
  glSamplerParameteri(samplers[unit],GL_TEXTURE_MAG_FILTER,(mode&16)?GL_LINEAR:GL_NEAREST);
  if(mipmaps) {
    // GX min filter: 0 near, 1 near+mip near, 2 near+mip lin, 4 lin, 5 lin+mip near, 6 lin+mip lin (3 and 7 reserved: treat as linear).
    const unsigned f=(mode>>5)&7u;
    const GLint filter=f==0?GL_NEAREST:f==1?GL_NEAREST_MIPMAP_NEAREST:f==2?GL_NEAREST_MIPMAP_LINEAR:f==5?GL_LINEAR_MIPMAP_NEAREST:f==6?GL_LINEAR_MIPMAP_LINEAR:GL_LINEAR;
    glSamplerParameteri(samplers[unit],GL_TEXTURE_MIN_FILTER,filter);
    const int bias=int(int8_t((mode>>9)&0xffu));                  // signed, 1/32 LOD steps
    glSamplerParameterf(samplers[unit],GL_TEXTURE_LOD_BIAS,float(bias)/32.f);
    glSamplerParameterf(samplers[unit],GL_TEXTURE_MIN_LOD,float(mode1&0xffu)/16.f);
    glSamplerParameterf(samplers[unit],GL_TEXTURE_MAX_LOD,float((mode1>>8)&0xffu)/16.f);
  } else {
    // The cache currently uploads level zero only. Preserve the requested texel
    // filter without selecting an incomplete mip chain. Mip LOD remains open.
    glSamplerParameteri(samplers[unit],GL_TEXTURE_MIN_FILTER,(mode&128)?GL_LINEAR:GL_NEAREST);
  }
  previous[unit]=resolved;have[unit]=true;
  }
  glBindSampler(unit,samplers[unit]);
}

'''
    s = s[:a] + new_fn + s[b:]
    r.write_text(s, errors='surrogateescape')
s = r.read_text(errors='surrogateescape')
# call site: pass texmode1 (restaging may rewrite the helper but preserves or re-adds the old one-argument call)
old_call = 'NativeApplyTextureSampler(i,g_state.bp[(i<4?0x80:0xa0)+(i%4)]);'
if old_call in s:
    assert s.count(old_call) == 1, s.count(old_call)
    s = s.replace(old_call, 'NativeApplyTextureSampler(i,g_state.bp[(i<4?0x80:0xa0)+(i%4)],g_state.bp[(i<4?0x84:0xa4)+(i%4)]); // ' + MARK, 1)
    r.write_text(s, errors='surrogateescape')
# a restage of the sampler helper re-adds the one-argument call after ours: keep exactly one call site
_call3 = '              NativeApplyTextureSampler(i,g_state.bp[(i<4?0x80:0xa0)+(i%4)],g_state.bp[(i<4?0x84:0xa4)+(i%4)]); // ' + MARK + '\n'
if s.count(_call3) > 1:
    first = s.index(_call3) + len(_call3)
    s = s[:first] + s[first:].replace(_call3, '')
    r.write_text(s, errors='surrogateescape')
if 'GL_TEXTURE_MAX_LEVEL,0); // ' + MARK not in s:
    # EFB copy textures carry level 0 only
    old_efb = '  glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);\n  glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);\n  glBindFramebuffer(GL_READ_FRAMEBUFFER,GLuint(draw));\n  glBindFramebuffer(GL_DRAW_FRAMEBUFFER,copy_fbo);'
    assert s.count(old_efb) == 1, s.count(old_efb)
    s = s.replace(old_efb, '  glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);\n  glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);\n'
                  '  glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAX_LEVEL,0); // ' + MARK + ': a render-to-texture copy has no mip chain\n'
                  '  glBindFramebuffer(GL_READ_FRAMEBUFFER,GLuint(draw));\n  glBindFramebuffer(GL_DRAW_FRAMEBUFFER,copy_fbo);', 1)
    r.write_text(s, errors='surrogateescape')
# ---- V2: build the chain lazily. Generating it for every upload cost ~1 ms per texture (texture.upload went from 0.2 to 2+ ms per frame);
# only textures drawn with a mip filter and a non-zero max LOD need one. Until then a texture is complete with level 0 (MAX_LEVEL 0).
_gen = ('    if (mipmaps) { native_trace::GPUScope mip_scope("gpu.texture_transfer_us", 1); glGenerateMipmap(GL_TEXTURE_2D); }\n')
_lazy = ('    if (mipmaps) glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, 0); // ' + MARK + ': level 0 only until ensure_mips() builds the chain\n')
_t = tc.read_text(errors='surrogateescape')
if _gen in _t:
    _t = _t.replace(_gen, _lazy, 1)
    _t = _t.replace('const uint32_t entry_bytes = stage.width * stage.height * 4 + (mipmaps ? stage.width * stage.height * 4 / 3 : 0);',
                    'const uint32_t entry_bytes = stage.width * stage.height * 4;', 1)
    tc.write_text(_t, errors='surrogateescape')
apply(tc, 'void TextureCache::evict_old() {',
      'void TextureCache::ensure_mips(GLuint tex) { // ' + MARK + '\n'
      '    static const bool mipmaps = std::getenv("NWII_NO_MIPMAPS") == nullptr;\n'
      '    if (!mipmaps || !tex || mip_ready.count(tex)) return;\n'
      '    mip_ready.insert(tex);\n'
      '    GLint bound = 0; glGetIntegerv(GL_TEXTURE_BINDING_2D, &bound);\n'
      '    glBindTexture(GL_TEXTURE_2D, tex);\n'
      '    { native_trace::GPUScope mip_scope("gpu.texture_transfer_us", 1); glGenerateMipmap(GL_TEXTURE_2D); }\n'
      '    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, 1000);\n'
      '    glBindTexture(GL_TEXTURE_2D, GLuint(bound));\n'
      '}\n\n'
      'void TextureCache::evict_old() {', 'void TextureCache::ensure_mips(GLuint tex)')
# a deleted texture id may be reused: forget it
for _old, _new in (('        glDeleteTextures(1, &pair.second.tex);', '        mip_ready.erase(pair.second.tex); glDeleteTextures(1, &pair.second.tex);'),
                   ('        glDeleteTextures(1, &it->second.tex);', '        mip_ready.erase(it->second.tex); glDeleteTextures(1, &it->second.tex);')):
    apply(tc, _old, _new, _new)
th = p / 'build/nwii-full-source/nWiiRuntime/include/runtime/texture_cache_gl.h'
apply(th, '    void register_efb_texture(uint32_t addr, GLuint tex);',
      '    void register_efb_texture(uint32_t addr, GLuint tex);\n    void ensure_mips(GLuint tex); // ' + MARK + ': build the mip chain the first time a mip filter samples this texture',
      'void ensure_mips(GLuint tex);')
apply(th, '    std::unordered_map<uint32_t, GLuint> efb_textures;',
      '    std::unordered_map<uint32_t, GLuint> efb_textures;\n    std::unordered_set<GLuint> mip_ready; // ' + MARK,
      'std::unordered_set<GLuint> mip_ready;')
_h = th.read_text(errors='surrogateescape')
if '#include <unordered_set>' not in _h:
    th.write_text(_h.replace('#include <unordered_map>', '#include <unordered_map>\n#include <unordered_set>', 1), errors='surrogateescape')
_r = r.read_text(errors='surrogateescape')
_call3 = '              NativeApplyTextureSampler(i,g_state.bp[(i<4?0x80:0xa0)+(i%4)],g_state.bp[(i<4?0x84:0xa4)+(i%4)]); // ' + MARK + '\n'
_ens = ('              { const unsigned mip_filter=(g_state.bp[(i<4?0x80:0xa0)+(i%4)]>>5)&7u; // ' + MARK + ': lazy mip chain\n'
        '                if((mip_filter==1||mip_filter==2||mip_filter==5||mip_filter==6) && ((g_state.bp[(i<4?0x84:0xa4)+(i%4)]>>8)&0xffu)!=0 &&\n'
        '                   tex!=nwii::runtime::hle::TextureCache::get().find_efb_texture(stage.base_addr))\n'
        '                  nwii::runtime::hle::TextureCache::get().ensure_mips(tex); }\n')
if 'lazy mip chain' not in _r:
    assert _r.count(_call3) == 1, _r.count(_call3)
    r.write_text(_r.replace(_call3, _call3 + _ens, 1), errors='surrogateescape')
print('mipmaps installed')
