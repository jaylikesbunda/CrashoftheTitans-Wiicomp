"""GX outputs RGB/alpha from the last TEV stage destinations independently."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
p = PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/tev_shader_gen.cpp'
s = p.read_text()
needle = '    fs << "    FragColor = tevReg[0];\\n";'
replacement = '''    // NATIVE_TEV_LAST_OUTPUT_V1
    const auto& last=state.tevStages[numTevs-1];
    fs << "    FragColor = vec4(tevReg[" << unsigned(last.colorRegId)
       << "].rgb, tevReg[" << unsigned(last.alphaRegId) << "].a);\\n";'''
if '// NATIVE_TEV_LAST_OUTPUT_V1' not in s:
    assert s.count(needle) == 1
    s = s.replace(needle, replacement, 1)
    p.write_text(s)
print('Final TEV colour and alpha destinations staged.')
