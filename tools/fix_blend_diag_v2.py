"""Extend the draw-signature log with the XF colour-channel state and vertex alpha.

For the blended draws that look translucent this shows the material/ambient
registers, channel controls (alpha lighting, sources) and the lowest vertex alpha
in the first draw of each signature, which is what the rasterised alpha depends on.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
p = port / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
t = p.read_text()
marker = 'NATIVE_BLEND_DIAG_V2'
if marker not in t:
    old = '        s.desc = b;\n        for (unsigned i = 0; i < g_state.numTevStages && i < 16; ++i) {'
    assert t.count(old) == 1
    new = r'''        s.desc = b;
        { // NATIVE_BLEND_DIAG_V2
          float min_a = 1.0f; unsigned colored = 0, total = 0;
          for (const auto &vtx : cmd.vertices) { ++total; if (vtx.has_color) { ++colored; min_a = std::min(min_a, vtx.color[3]); } }
          std::snprintf(b, sizeof b, " da=%06X xf amb=%08X/%08X mat=%08X/%08X cc=%X/%X ac=%X/%X vcol=%u/%u minA=%.2f |",
                        g_state.bp[0x42], g_state.xf[0xA], g_state.xf[0xB], g_state.xf[0xC], g_state.xf[0xD], g_state.xf[0xE],
                        g_state.xf[0xF], g_state.xf[0x10], g_state.xf[0x11], colored, total, min_a);
          s.desc += b;
        }
        for (unsigned i = 0; i < g_state.numTevStages && i < 16; ++i) {'''
    t = t.replace(old, new)
    p.write_text(t)
print('Blend diagnostics extended.')
