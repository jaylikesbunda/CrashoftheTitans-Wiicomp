"""Bind only textures referenced by the production TEV shader, including indirect maps."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]
f=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
s=f.read_text()
s=s.replace('if(!(used&(1u<<i))){native_trace::mark("texture.unused_slot");continue;}', 'if(!(used&(1u<<i)))continue;')
helper=(p/'runtime-overlays/used_texture_mask.inc').read_text()
if 'static unsigned NativeUsedTextureMask(' in s:
    a=s.index('// Conservative set of every sampler referenced by GenerateTEVShader')
    b=s.index('  return mask;\n}',a)+len('  return mask;\n}')
    s=s[:a]+helper.rstrip()+s[b:]
elif helper not in s:
    anchor='static GLShader s_active_shader = {0};'
    assert s.count(anchor)==1
    s=s.replace(anchor,anchor+'\n'+helper,1)
old='''        if (!s_batch_open && g_ctx_ptr) {
          for (int i = 0; i < 8; ++i) {
            const auto &stage = g_state.texStages[i];'''
new='''        if (!s_batch_open && g_ctx_ptr) {
          // NATIVE_USED_TEXTURE_BINDING_V1: validate only the shader's source textures.
          static const bool used_only=std::getenv("NWII_ALL_TEXTURE_SLOTS")==nullptr;
          const unsigned used=used_only?NativeUsedTextureMask(g_state):0xffu;
          for (int i = 0; i < 8; ++i) {
            if(!(used&(1u<<i)))continue;
            const auto &stage = g_state.texStages[i];'''
if new not in s:
    assert s.count(old)==1
    s=s.replace(old,new,1)
if s!=f.read_text():f.write_text(s)
print('Texture binding follows direct/dimension/indirect shader references; NWII_ALL_TEXTURE_SLOTS=1 restores all slots.')
