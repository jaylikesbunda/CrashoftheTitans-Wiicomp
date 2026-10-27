"""Implement the GX TEV compare mode (bias field == 3).

In TEV_COLOR_ENV / TEV_ALPHA_ENV a bias of 3 does not mean "+0": it selects the
compare operators. The operator index is (scale << 1) | op:
  0 R8_GT   1 R8_EQ   2 GR16_GT   3 GR16_EQ   4 BGR24_GT   5 BGR24_EQ
  6 RGB8_GT (per component; alpha: A8_GT)   7 RGB8_EQ (alpha: A8_EQ)
result = d + (compare(a, b) ? c : 0), no scale applied, optional clamp.
The generator treated bias 3 as zero bias with a lerp, so materials that rely on
the compare operators (character shading) produced dark/transparent output.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
path = port / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/tev_shader_gen.cpp'
text = path.read_text()
marker = 'NATIVE_TEV_COMPARE_MODE_V1'
if marker not in text:
    # ---- colour ----
    old = 'if (stage.colorOp == 0) {'
    assert text.count(old) == 1
    new = r'''if (stage.colorBias == 3) { // NATIVE_TEV_COMPARE_MODE_V1
            const int sel = (stage.colorScale << 1) | stage.colorOp;
            const char* rel = (sel & 1) ? "==" : ">";
            auto q = [](const std::string& x) { return "floor((" + x + ")*255.0+0.5)"; };
            std::string a = "cIn[0]", b = "cIn[1]", lhs, rhs;
            if (sel >> 1 == 0) { lhs = q(a + ".r"); rhs = q(b + ".r"); }
            else if (sel >> 1 == 1) { lhs = q(a + ".g") + "*256.0+" + q(a + ".r"); rhs = q(b + ".g") + "*256.0+" + q(b + ".r"); }
            else if (sel >> 1 == 2) { lhs = "(" + q(a + ".b") + "*256.0+" + q(a + ".g") + ")*256.0+" + q(a + ".r"); rhs = "(" + q(b + ".b") + "*256.0+" + q(b + ".g") + ")*256.0+" + q(b + ".r"); }
            if (sel >> 1 < 3) {
                fs << "    cOut = cIn[3] + ((" << lhs << ") " << rel << " (" << rhs << ") ? cIn[2] : vec3(0.0));\n";
            } else {
                fs << "    cOut = cIn[3] + mix(vec3(0.0), cIn[2], vec3(" << ((sel & 1) ? "equal" : "greaterThan")
                   << "(floor(cIn[0]*255.0+0.5), floor(cIn[1]*255.0+0.5))));\n";
            }
        } else if (stage.colorOp == 0) {'''
    text = text.replace(old, new)
    old = 'if (stage.colorScale == 1) fs << "    cOut *= 2.0;\\n";'
    assert text.count(old) == 1
    text = text.replace(old, 'if (stage.colorBias == 3) {} else ' + old)
    # ---- alpha ----
    old = 'if (stage.alphaOp == 0) {'
    assert text.count(old) == 1
    new = r'''if (stage.alphaBias == 3) {
            const int sel = (stage.alphaScale << 1) | stage.alphaOp;
            const char* rel = (sel & 1) ? "==" : ">";
            fs << "    aOut = aIn[3] + ((floor(aIn[0]*255.0+0.5) " << rel << " floor(aIn[1]*255.0+0.5)) ? aIn[2] : 0.0);\n";
        } else if (stage.alphaOp == 0) {'''
    text = text.replace(old, new)
    old = 'if (stage.alphaScale == 1) fs << "    aOut *= 2.0;\\n";'
    assert text.count(old) == 1
    text = text.replace(old, 'if (stage.alphaBias == 3) {} else ' + old)
    path.write_text(text)
print('TEV compare mode (bias 3) implemented.')
