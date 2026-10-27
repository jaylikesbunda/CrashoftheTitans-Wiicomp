"""Restore indirect TEV coordinate generation; keep game FIFO/register data authoritative."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/tev_shader_gen.cpp';s=p.read_text()
marker='// NATIVE_INDIRECT_V1'
if marker in s:
    a=s.index(marker);b=s.index('// NATIVE_FOG_V1',a)
    s=s[:a]+(PORT/'runtime-overlays/indirect_codegen.inc').read_text()+'\n'+s[b:]
else:
    needle='// NATIVE_FOG_V1';assert s.count(needle)==1
    s=s.replace(needle,(PORT/'runtime-overlays/indirect_codegen.inc').read_text()+'\n'+needle,1)
needle='    fs << "    tevReg[0] = uTevColor[0];\\n";'
if '    fs << "    vec2 indPrevious' not in s:
    assert s.count(needle)==1;s=s.replace(needle,'    fs << "    vec2 indPrevious=vec2(0.0);float indAlpha=0.0;\\n";\n'+needle,1)
old='''            fs << "    texColor = texture(uTex" << (int)stage.texMap << ", vTex" << (int)stage.texCoord << ".xy / vTex" << (int)stage.texCoord << ".z);\\n";'''
new='''            NativeWriteIndirect(fs,state,i);
            if(state.bp[0x10+i]&(1<<19)) {
                fs << "    vec2 originalUV=vTex" << unsigned(stage.texCoord) << ".xy/vTex" << unsigned(stage.texCoord) << ".z;\\n";
                fs << "    texColor=textureGrad(uTex" << unsigned(stage.texMap) << ",tevUV,dFdx(originalUV),dFdy(originalUV));\\n";
            } else fs << "    texColor=texture(uTex" << unsigned(stage.texMap) << ",tevUV);\\n";'''
if new not in s:
    assert s.count(old)==1;s=s.replace(old,new,1)
needle='''        } else {
            fs << "    texColor = vec4(1.0);\\n";'''
replacement='''        } else {
            NativeWriteIndirect(fs,state,i);
            fs << "    texColor = vec4(1.0);\\n";'''
if replacement not in s:
    assert s.count(needle)==1;s=s.replace(needle,replacement,1)
if p.read_text()!=s:p.write_text(s)
print('Indirect orders, scales, formats, bias, matrices, wrap, accumulation, bump alpha and unmodified LOD staged.')
