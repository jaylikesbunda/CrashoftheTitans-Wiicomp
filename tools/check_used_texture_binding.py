"""Compare selection with actual generated shader references and the staged binding loop."""
from pathlib import Path
from port_paths import default_storage
import json,subprocess,hashlib,os
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
w=default_storage()/'used-texture-tests';w.mkdir(exist_ok=True)
helper=(p/'runtime-overlays/used_texture_mask.inc').read_text()
renderer=(rt/'src/hle/gx/renderer_gl.cpp').read_text()
a=renderer.index('        if (!s_batch_open && g_ctx_ptr) {');b=renderer.index('        DrawPrimitive(cmd);',a)
binding=renderer[a:b]
code=r'''#include "runtime/gx/tev_shader_gen.h"
#include <cstdlib>
#include <iostream>
#include <string>
#include <regex>
using namespace nwii::runtime::gx;
'''+helper+r'''
namespace native_trace {void mark(const char*){}}
using GLuint=unsigned;
const unsigned GL_TEXTURE0=0,GL_TEXTURE_2D=0;
unsigned looked=0,unit=0;
void glActiveTexture(unsigned i){unit=i;}
void glBindTexture(unsigned,unsigned){}
void NativeApplyTextureSampler(int,unsigned,unsigned){}
struct Context{};Context context;Context* g_ctx_ptr=&context;
bool s_batch_open=false;namespace nwii::runtime::gx {GXState g_state;}
namespace nwii::runtime::hle {struct TextureCache {
 static TextureCache& get(){static TextureCache c;return c;}
 unsigned get_texture(Context&,const gx::TexStage&){looked|=1u<<unit;return 1;}
 unsigned find_efb_texture(unsigned){return 0;}
 void ensure_mips(unsigned){}
};}
void bind(){
'''+binding+r'''}
uint32_t rng=123;uint32_t next(){rng=rng*1664525u+1013904223u;return rng;}
int main(){unsigned checks=0,saved=0,total=0;
for(unsigned family=0;family<2048;++family){GXState state{};
 state.numTevStages=family%17;
 for(auto& v:state.bp)v=next()&0xffffff;
 state.bp[0]=(family%5)<<16;
 for(auto& t:state.tevStages){t.texMap=next()%3?next()%8:0xff;t.texCoord=next()%8;}
 for(auto& t:state.texStages){t.base_addr=0x80010000;t.width=t.height=8;}
 auto shader=GenerateTEVShader(state,0x90).fragment_source;
 // Ignore declarations; textureSize/texture/textureGrad/texelFetch reads count.
 shader=shader.substr(shader.find("void main()"));
 std::regex ref("uTex([0-7])");unsigned refs=0;
 for(std::sregex_iterator i(shader.begin(),shader.end(),ref),e;i!=e;++i)refs|=1u<<unsigned((*i)[1].str()[0]-'0');
 auto mask=NativeUsedTextureMask(state);++checks;
 if(refs!=mask){std::cout<<"MASK_FAIL "<<family<<" refs="<<refs<<" mask="<<mask<<'\n';return 1;}
 g_state=state;looked=0;bind();++checks;
 if(looked!=(std::getenv("NWII_ALL_TEXTURE_SLOTS")?255:mask)){std::cout<<"BIND_FAIL "<<family<<'\n';return 1;}
 total+=8;for(unsigned i=0;i<8;++i)if(!(mask&(1u<<i)))++saved;
}
 // Decisive simple draw and indirect-only (direct texture disabled) probes.
 g_state={};g_state.numTevStages=1;g_state.tevStages[0].texMap=2;
 g_state.texStages[2].base_addr=0x80010000;
 ++checks;if(NativeUsedTextureMask(g_state)!=4)return 1;
 g_state.tevStages[0].texMap=0xff;g_state.bp[0]=1<<16;g_state.bp[0x10]=1<<7;g_state.bp[0x27]=7;
 ++checks;if(NativeUsedTextureMask(g_state)!=(1|128))return 1;
 g_state={};g_state.numTevStages=1;g_state.tevStages[0].texMap=3;
 ++checks;if(NativeUsedTextureMask(g_state)!=(1|8))return 1;
 g_state.texStages[3].base_addr=0x80010000;
 ++checks;if(NativeUsedTextureMask(g_state)!=8)return 1;
 std::cout<<"USED_TEXTURE_BINDING "<<checks<<"/"<<checks<<" synthetic_skipped="<<saved<<"/"<<total<<'\n';}
'''
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(code);rows=[]
compile_cmd=['g++','-std=c++20','-O2','-static','-I',str(rt/'include'),str(cpp),str(rt/'src/hle/gx/tev_shader_gen.cpp'),'-o',str(exe)]
for cmd,all_slots in ((compile_cmd,False),([str(exe)],False),([str(exe)],True)):
 env=os.environ.copy();env.pop('NWII_ALL_TEXTURE_SLOTS',None)
 if all_slots:env['NWII_ALL_TEXTURE_SLOTS']='1'
 r=subprocess.run(cmd,capture_output=True,text=True,timeout=240,env=env);rows.append(dict(command=cmd,all_slots=all_slots,exit_code=r.returncode,output=r.stdout+r.stderr));print((r.stdout+r.stderr)[-3000:],flush=True)
 if r.returncode:break
(p/'reports/used-texture-binding-tests.json').write_text(json.dumps(dict(exit_code=r.returncode,game_launched=False,generator_sha256=hashlib.sha256((rt/'src/hle/gx/tev_shader_gen.cpp').read_bytes()).hexdigest(),runs=rows),indent=2)+'\n')
raise SystemExit(r.returncode)
