"""Restore basic GX fog without replacing assets or modifying game state."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
header=PORT/'build/nwii-full-source/nWiiRuntime/include/runtime/native_shader_data.h'
value=(PORT/'runtime-overlays/native_shader_data.h').read_text()
if not header.exists() or header.read_text()!=value:header.write_text(value)
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/tev_shader_gen.cpp';s=p.read_text()
include='#include "runtime/native_shader_data.h"\n'
if include not in s:s=include+s
marker='// NATIVE_FOG_V1'
if marker in s:
    a=s.index(marker);b=s.index('GeneratedShader GenerateTEVShader',a)
    s=s[:a]+(PORT/'runtime-overlays/fog_codegen.inc').read_text()+'\n'+s[b:]
else:
    needle='GeneratedShader GenerateTEVShader'
    assert s.count(needle)==1;s=s.replace(needle,(PORT/'runtime-overlays/fog_codegen.inc').read_text()+'\n'+needle,1)
needle='    fs << "    FragColor = tevReg[0];\\n";'
if '    NativeWriteFog(fs,state);' not in s:
    assert s.count(needle)==1;s=s.replace(needle,needle+'\n    NativeWriteFog(fs,state);',1)
if s!=p.read_text():p.write_text(s)
print('Basic perspective/orthographic GX fog and all six curves staged; range adjustment remains open.')
