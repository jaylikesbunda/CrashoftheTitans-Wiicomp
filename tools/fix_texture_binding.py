"""Select the destination texture unit before cache upload can change bindings."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
MARK='NATIVE_TEXTURE_UNIT_V1'
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp';s=p.read_text()
before='''              GLuint tex = nwii::runtime::hle::TextureCache::get().get_texture(
                  *g_ctx_ptr, stage);
              glActiveTexture(GL_TEXTURE0 + i);
              glBindTexture(GL_TEXTURE_2D, tex);'''
after='''              // NATIVE_TEXTURE_UNIT_V1: cache misses upload on the active unit.
              glActiveTexture(GL_TEXTURE0 + i);
              GLuint tex = nwii::runtime::hle::TextureCache::get().get_texture(
                  *g_ctx_ptr, stage);
              glBindTexture(GL_TEXTURE_2D, tex);'''
if before in s:s=s.replace(before,after)
# Later stages (the bind shadow, the sampler/mipmap stages) rewrite the calls around this block, so the
# applied state is recognised by its own marker rather than by the exact block.
elif MARK not in s:raise SystemExit('Texture binding context changed')
if p.read_text()!=s:p.write_text(s)
print('Texture destination selected before cache lookup/upload.')
