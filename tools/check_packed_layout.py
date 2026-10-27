"""Compare actual layout replay with the production masked BP resolver and upload projection."""
from pathlib import Path
from port_paths import default_storage
import subprocess,json
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime';w=default_storage()/'packed-layout-tests';w.mkdir(exist_ok=True)
s=(rt/'src/hle/gx/renderer_gl.cpp').read_text()
def function(name):
 a=s.index(name);b=s.index('{',a)+1;depth=1
 while depth:depth+=(s[b]=='{')-(s[b]=='}');b+=1
 return s[a:b]
replay=function('void NativePreparePackedLayouts(');resolver=function('static uint32_t NativeBpRegisterValue(')
a=s.index('  const unsigned active=',s.index('static void NativeUploadBatch()'));b=s.index('  const size_t stride=',a);projection=s[a:b]
code='''#include "runtime/gx_state.h"
#include "runtime/gx/command_stream.h"
#include "runtime/gx_command.h"
#include <cstring>
#include <algorithm>
#include <iostream>
using namespace nwii::runtime::gx;
namespace native_state_epochs{inline bool ShaderBp(unsigned){return false;}inline void Invalidate(){}}
namespace nwii::runtime::gx {GXState g_state;}static uint32_t s_native_bp_shadow[256];static uint32_t s_native_bp_mask=0xffffff;static bool s_gpu_vertex_mode=true;
'''+resolver+replay+'\nunsigned needed(){\n'+projection+'return generators;}\n'+r'''
uint32_t rng=55;uint32_t next(){rng=rng*1664525u+1013904223u;return rng;}
int main(){unsigned checks=0;for(unsigned trial=0;trial<1000;++trial){for(auto& b:s_native_bp_shadow)b=next()&0xffffff;s_native_bp_mask=next()&0xffffff;for(unsigned t=0;t<8;++t)g_state.xf[0x40+t]=next();
 std::vector<GXCommand> commands;
 for(unsigned i=0;i<120;++i){GXCommand c{};if(i%3==0){c.type=GXCommandType::DrawPrimitive;}
 else if(i%3==1){c.type=GXCommandType::BPRegister;c.reg=i%5?0:0xfe;c.val=next()&0xffffff;}
 else {c.type=GXCommandType::XFRegister;c.reg=0x1040+(next()%8);for(unsigned k=0;k<8;++k){uint32_t bits=next();float f;memcpy(&f,&bits,4);c.payload.push_back(f);}}
 commands.push_back(std::move(c));}
 CommandStream stream;
 for(const auto& c:commands){if(c.type==GXCommandType::BPRegister)stream.EmitRegister(c.type,uint8_t(c.reg),c.val);
   else stream.EmitHeavy(c.type,c.reg,c.val)=c;}
 NativePreparePackedLayouts(stream);
 for(size_t i=0;i<commands.size();++i)if(auto* c=stream.Heavy(stream.entries[i]))commands[i].native_packed_inputs=c->native_packed_inputs;
 for(const auto& c:commands){if(c.type==GXCommandType::BPRegister){uint32_t resolved=NativeBpRegisterValue(uint8_t(c.reg),c.val);if(c.reg==0)g_state.bp[0]=resolved;}
 else if(c.type==GXCommandType::XFRegister){for(size_t i=0;i<c.payload.size();++i)if(c.reg+i>=0x1040 && c.reg+i<0x1048)memcpy(g_state.xf+size_t(c.reg+i)-0x1000,&c.payload[i],4);}
 else {++checks;if(c.native_packed_inputs!=needed()){std::cout<<"LAYOUT_FAIL "<<trial<<'\n';return 1;}}}
 }
 std::cout<<"PACKED_LAYOUT "<<checks<<'/'<<checks<<'\n';}
'''
# Seed the reference projection from the exact initial renderer shadow.
code=code.replace(' NativePreparePackedLayouts(stream);',' g_state.bp[0]=s_native_bp_shadow[0];NativePreparePackedLayouts(stream);')
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(code);rows=[]
for cmd in (['g++','-std=c++20','-O2','-static','-I',str(rt/'include'),str(cpp),'-o',str(exe)],[str(exe)]):
 r=subprocess.run(cmd,capture_output=True,text=True,timeout=120);rows.append(dict(command=cmd,exit_code=r.returncode,output=r.stdout+r.stderr));print((r.stdout+r.stderr)[-1200:])
 if r.returncode:break
(p/'reports/packed-layout-tests.json').write_text(json.dumps(rows,indent=2)+'\n');raise SystemExit(r.returncode)
