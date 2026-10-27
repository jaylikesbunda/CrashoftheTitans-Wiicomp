"""Cut per-draw CPU cost on the GX thread.

* Uniforms persist per GL program. The renderer re-uploaded ~25 uniform arrays
  (8 identity texture matrices, five 8-light arrays, channel colours, TEV
  colours, sampler slots) before every draw run. Upload only when the value
  differs from what that program already holds.
* NativeTexGen evaluated all eight generators for every vertex; only the first
  GEN_MODE.numTexGens (BP 0x00 low nibble, at least one) can be consumed.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
p = port / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
t = p.read_text()
marker = 'NATIVE_UNIFORM_CACHE_V1'
if marker not in t:
    t = '#include <unordered_map>\n#include <vector>\n#include <cstring>\n' + t

    anchor = 'static void SetupDrawState(const GXCommand &cmd) {'
    assert t.count(anchor) == 1
    helper = r'''// NATIVE_UNIFORM_CACHE_V1: programs keep their uniforms, so skip identical uploads.
static bool NativeUniformChanged(GLuint prog, GLint loc, const void *data, size_t bytes) {
  static std::unordered_map<uint64_t, std::vector<uint8_t>> cache;
  auto &entry = cache[(uint64_t(prog) << 32) | uint32_t(loc)];
  if (entry.size() == bytes && std::memcmp(entry.data(), data, bytes) == 0) return false;
  entry.assign(static_cast<const uint8_t *>(data), static_cast<const uint8_t *>(data) + bytes);
  return true;
}

'''
    t = t.replace(anchor, helper + anchor)

    S = 's_active_shader'
    reps = [
        ('glUniform4fv(%s.uMatColor, 2, mat);' % S, 'uMatColor', 'mat', 32, 'glUniform4fv(%s.uMatColor, 2, mat);' % S),
        ('glUniform4fv(%s.uAmbColor, 2, amb);' % S, 'uAmbColor', 'amb', 32, None),
        ('glUniform1iv(%s.uChanCtrl, 4, cc);' % S, 'uChanCtrl', 'cc', 16, None),
        ('glUniform4fv(%s.uLightCol, 8, lcol);' % S, 'uLightCol', 'lcol', 128, None),
        ('glUniform4fv(%s.uLightPos, 8, lpos);' % S, 'uLightPos', 'lpos', 128, None),
        ('glUniform4fv(%s.uLightDir, 8, ldir);' % S, 'uLightDir', 'ldir', 128, None),
        ('glUniform4fv(%s.uLightCosAtt, 8, lcos);' % S, 'uLightCosAtt', 'lcos', 128, None),
        ('glUniform4fv(%s.uLightDistAtt, 8, ldist);' % S, 'uLightDistAtt', 'ldist', 128, None),
        ('glUniform4fv(%s.uTevKColor, 4, kcolor);' % S, 'uTevKColor', 'kcolor', 64, None),
        ('glUniform4fv(%s.uTevColor, 4, color);' % S, 'uTevColor', 'color', 64, None),
    ]
    for old, name, var, nbytes, _ in reps:
        assert t.count(old) == 1, old
        new = 'if (NativeUniformChanged(%s.id, %s.%s, %s, %d)) %s' % (S, S, name, var, nbytes, old)
        t = t.replace(old, new)

    old = 'glUniformMatrix4fv(s_active_shader.uTexMtx[i], 1, GL_FALSE, mat);'
    assert t.count(old) == 1
    t = t.replace(old, 'if (NativeUniformChanged(s_active_shader.id, s_active_shader.uTexMtx[i], mat, 64)) ' + old)
    old = 'glUniform1i(s_active_shader.uTex[i], i);'
    assert t.count(old) == 1
    t = t.replace(old, 'if (NativeUniformChanged(s_active_shader.id, s_active_shader.uTex[i], &i, sizeof(i))) ' + old)

    old = '  for (unsigned t=0;t<8;++t) {\n    const uint32_t info=state.xf[0x40+t];'
    assert t.count(old) == 1
    new = ('  const unsigned texgen_count = std::min(8u, std::max(1u, unsigned(state.bp[0x00] & 0xF)));\n'
           '  for (unsigned t=0;t<texgen_count;++t) {\n    const uint32_t info=state.xf[0x40+t];')
    t = t.replace(old, new)
    p.write_text(t)
print('GX per-draw uniform cache and bounded texgen installed.')
