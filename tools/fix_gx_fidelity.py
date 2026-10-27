"""Small GX fidelity fixes found by auditing the renderer against GX/Dolphin semantics.

1. TEV rasterised-colour "null" selection yields (0,0,0,0) (it was alpha 1).
2. Alpha test compares the 8-bit quantised alpha (equality tests on floats failed).
3. Destination-alpha blend factors read 1.0 when the EFB pixel format has no alpha
   (BP 0x43 format != RGBA6): DST_ALPHA -> ONE, ONE_MINUS_DST_ALPHA -> ZERO.
4. CMODE0 logic ops (bit 1 enable, bits 12-15 op) are applied when blending is off.
5. Display-list cache identity hashes the whole list (it sampled every 512th byte,
   so in-place edited lists reused stale parsed draws).
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
gx = port / 'build/nwii-full-source/nWiiRuntime/src/hle/gx'

# ---- tev_shader_gen.cpp ----
p = gx / 'tev_shader_gen.cpp'
t = p.read_text()
if 'NATIVE_TEV_FIDELITY_V1' not in t and 'NATIVE_RAS_NULL_ALPHA_ONE_V1' not in t:
    old = 'else fs << "    rasColor = vec4(0.0, 0.0, 0.0, 1.0);\\n";'
    assert t.count(old) == 1
    t = t.replace(old, 'else fs << "    rasColor = vec4(0.0);\\n"; // NATIVE_TEV_FIDELITY_V1')
    old = 'std::snprintf(rb, sizeof(rb), "%.6f", ref / 255.0f);'
    assert t.count(old) == 1
    t = t.replace(old, 'std::snprintf(rb, sizeof(rb), "%.1f", float(ref));')
    for op in ['< ', '== ', '<= ', '> ', '!= ', '>= ']:
        o = 'return "FragColor.a ' + op + '" + r;'
        assert t.count(o) == 1, o
        t = t.replace(o, 'return "floor(FragColor.a*255.0+0.5) ' + op + '" + r;')
    p.write_text(t)

# ---- renderer_gl.cpp ----
p = gx / 'renderer_gl.cpp'
t = p.read_text()
if 'NATIVE_BLEND_FIDELITY_V1' not in t:
    old = '  if (!enable) { glDisable(GL_BLEND); return; }'
    assert t.count(old) == 1
    new = r'''  { // NATIVE_BLEND_FIDELITY_V1
    const bool logic_en = (cm >> 1) & 1;
    const unsigned lop = (cm >> 12) & 15;
    static const GLenum lops[16] = {
        GL_CLEAR, GL_AND, GL_AND_REVERSE, GL_COPY, GL_AND_INVERTED, GL_NOOP, GL_XOR, GL_OR,
        GL_NOR, GL_EQUIV, GL_INVERT, GL_OR_REVERSE, GL_COPY_INVERTED, GL_OR_INVERTED, GL_NAND, GL_SET};
    if (logic_en && lop != 3 && !enable) {
      glDisable(GL_BLEND);
      glEnable(GL_COLOR_LOGIC_OP);
      glLogicOp(lops[lop]);
      return;
    }
    glDisable(GL_COLOR_LOGIC_OP);
  }
  if (!enable) { glDisable(GL_BLEND); return; }'''
    t = t.replace(old, new)
    old = '    glBlendFunc(srcMap[srcF], dstMap[dstF]);'
    assert t.count(old) == 1
    new = r'''    GLenum sf = srcMap[srcF], df = dstMap[dstF];
    if ((g_state.bp[0x43] & 7) != 1) { // no EFB alpha: destination alpha reads as 1
      if (sf == GL_DST_ALPHA) sf = GL_ONE; else if (sf == GL_ONE_MINUS_DST_ALPHA) sf = GL_ZERO;
      if (df == GL_DST_ALPHA) df = GL_ONE; else if (df == GL_ONE_MINUS_DST_ALPHA) df = GL_ZERO;
    }
    glBlendFunc(sf, df);'''
    t = t.replace(old, new)
    p.write_text(t)

# ---- fifo_parser.cpp ----
p = gx / 'fifo_parser.cpp'
t = p.read_text()
if 'NATIVE_DL_FULL_HASH_V1' not in t:
    old = '''        for (uint32_t i = 0; i < size; i += 512) { ch ^= src[i]; ch *= 1099511628211ull; }
        for (uint32_t i = size > 32 ? size - 32 : 0; i < size; i++) { ch ^= src[i]; ch *= 1099511628211ull; }'''
    assert t.count(old) == 1
    new = '''        { // NATIVE_DL_FULL_HASH_V1: whole list, 8 bytes at a time
            uint32_t i = 0;
            for (; i + 8 <= size; i += 8) { uint64_t w; std::memcpy(&w, src + i, 8); ch ^= w; ch *= 1099511628211ull; ch ^= ch >> 29; }
            for (; i < size; i++) { ch ^= src[i]; ch *= 1099511628211ull; }
        }'''
    t = t.replace(old, new)
    p.write_text(t)
print('GX fidelity fixes applied.')
