"""Give generated per-stage GLSL temporaries their own scope."""
from pathlib import Path

PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/tev_shader_gen.cpp'
s=p.read_text()
old='        fs << "    // TEV Stage " << i << "\\n";'
new=old+'\n        // NATIVE_TEV_STAGE_SCOPE_V1\n        fs << "    {\\n";'
if new not in s:
    if s.count(old)!=1:raise SystemExit('TEV stage entry context changed')
    s=s.replace(old,new,1)
old='        fs << "    tevReg[" << (int)stage.alphaRegId << "].a = aOut;\\n";'
new=old+'\n        fs << "    }\\n";'
if new not in s:
    if s.count(old)!=1:raise SystemExit('TEV stage exit context changed')
    s=s.replace(old,new,1)
if p.read_text()!=s:p.write_text(s)
print('Generated GLSL TEV stages scoped independently.')
