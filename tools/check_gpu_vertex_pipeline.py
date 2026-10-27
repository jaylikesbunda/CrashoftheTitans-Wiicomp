"""Actual GL shader transform-feedback comparison with CPU XF/texgen baseline."""
from pathlib import Path
from port_paths import default_storage
import json,subprocess
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime';work=default_storage()/'gpu-vertex-tests';work.mkdir(exist_ok=True)
s=(rt/'src/hle/gx/renderer_gl.cpp').read_text()
parser=(rt/'src/hle/gx/fifo_parser.cpp').read_text();a=parser.index('VertexData GXCommand::NativeDecodedVertex(');b=parser.index('\n}\n',a)+3
getter='namespace nwii::runtime::gx {\n'+parser[a:b]+'\n}\n'
def section(a,b):return s[s.index(a):s.index(b,s.index(a))]
struct=section('struct BatchVertex {','static bool s_gpu_vertex_mode=')
convert=section('static void NativeTexGen(','// NATIVE_VERTEX_INPLACE_V1')
ubo=section('// std140 rows;','// NATIVE_SHADER_SAMPLER_ALIAS_V1:' if '// NATIVE_SHADER_SAMPLER_ALIAS_V1:' in s else 'static void SetupDrawState(const GXCommand &cmd) {')
upload=section('// NATIVE_STREAM_UPLOAD_V1:','static void FlushBatch() {')
code=r'''
#define SDL_MAIN_HANDLED
#include <SDL.h>
#include <glad/glad.h>
#include "runtime/gx/fifo_parser.h"
#include "runtime/gx/tev_shader_gen.h"
#include "runtime/native_frame_trace_gl.h"
#include "runtime/native_geometry_stats.h"
#include <iostream>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>
using namespace nwii::runtime::gx;
namespace nwii::runtime::gx {GXState g_state;}
'''+getter+struct+'''
static bool s_gpu_vertex_mode=false;
static std::shared_ptr<DrawRaw> s_gpu_vertex_snapshot;
static std::vector<BatchVertex> s_batch;
// fused-batch declarations referenced by the staged NativeUploadBatch; this fixture only exercises the non-span path (tools/check_fused_batch.py covers the spans)
struct NativeGpuSpan {const VertexData* vertices; size_t count;const GXCommand* packed=nullptr;};
static std::vector<NativeGpuSpan> s_gpu_spans;
static size_t s_gpu_span_vertices=0;
static inline size_t NativeBatchSize(){return s_gpu_span_vertices?s_gpu_span_vertices:s_batch.size();}
static inline void NativePackGpuVertex(const VertexData&,float*,unsigned){}
'''+convert+upload+ubo+r'''
GLuint compile(GLenum type,const std::string& source){GLuint shader=glCreateShader(type);const char* ptr=source.c_str();glShaderSource(shader,1,&ptr,nullptr);glCompileShader(shader);GLint ok;glGetShaderiv(shader,GL_COMPILE_STATUS,&ok);if(!ok){char log[8192];glGetShaderInfoLog(shader,sizeof(log),nullptr,log);std::cerr<<log;exit(4);}return shader;}
int main(){SDL_SetMainReady();if(SDL_Init(SDL_INIT_VIDEO))return 2;SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK,SDL_GL_CONTEXT_PROFILE_CORE);
auto window=SDL_CreateWindow("Offline GPU vertex fixture",0,0,32,32,SDL_WINDOW_OPENGL|SDL_WINDOW_HIDDEN);auto context=SDL_GL_CreateContext(window);if(!context||!gladLoadGLLoader((GLADloadproc)SDL_GL_GetProcAddress))return 3;
GLuint vao,vbo,feedback;glGenVertexArrays(1,&vao);glBindVertexArray(vao);glGenBuffers(1,&vbo);glBindBuffer(GL_ARRAY_BUFFER,vbo);for(unsigned i=0;i<13;++i)glEnableVertexAttribArray(i);
glGenBuffers(1,&feedback);glBindBuffer(GL_TRANSFORM_FEEDBACK_BUFFER,feedback);glBufferData(GL_TRANSFORM_FEEDBACK_BUFFER,36*4*12,nullptr,GL_STREAM_READ);glBindBufferBase(GL_TRANSFORM_FEEDBACK_BUFFER,0,feedback);
uint64_t checks=0,failed=0,packed_bytes=0,full_bytes=0;float max_error=0;
for(unsigned scenario=0;scenario<512;++scenario){
 g_state={};g_state.bp[0]=1+scenario%8;g_state.xf[0x12]=(scenario>>3)&1;
 for(unsigned i=0;i<256;++i){g_state.posMatrices[i]=(int((i*7+scenario)%31)-15)*.03125f;g_state.postMatrices[i]=(int((i*3+scenario)%17)-8)*.0625f;}
 for(unsigned t=0;t<8;++t){const unsigned sources[]={0,1,5,6,7,8,11,12};g_state.xf[0x40+t]=(sources[(t+scenario)%8]<<7)|(scenario&6);g_state.xf[0x50+t]=((t*7+scenario)%64)|((scenario&16)?256:0);}
 if(scenario>=256)for(unsigned t=0;t<8;++t)g_state.xf[0x40+t]|=(2u+(t&1u))<<4;
 auto snapshot=std::make_shared<DrawRaw>();snapshot->have_snap_mtx=true;
 for(unsigned i=0;i<12;++i)snapshot->snap_pos_mtx[i]=(int((i+scenario)%11)-5)*.125f;
 for(unsigned i=0;i<9;++i)snapshot->snap_nrm_mtx[i]=(int((i+scenario)%7)-3)*.25f;
 if(scenario&1){snapshot->snap_pos_bank.resize(256);snapshot->snap_nrm_bank.resize(96);for(unsigned i=0;i<256;++i)snapshot->snap_pos_bank[i]=(int((i+scenario)%23)-11)*.0625f;for(unsigned i=0;i<96;++i)snapshot->snap_nrm_bank[i]=(int((i+scenario)%13)-6)*.125f;}
 // Exercise RGB/alpha lighting independently, including all attenuation branches.
 g_state.xf[0x0e]=g_state.xf[0x10]=1|2|4|64|((scenario%3)<<7)|((scenario%4)<<9);
 g_state.xf[0x0f]=g_state.xf[0x11]=g_state.xf[0x0e];
 auto source=GenerateTEVShader(g_state,0x90);GLuint program=glCreateProgram();glAttachShader(program,compile(GL_VERTEX_SHADER,source.vertex_source));glAttachShader(program,compile(GL_FRAGMENT_SHADER,source.fragment_source));
 const char* outputs[]={"gl_Position","vColor0","vColor1","vTex0","vTex1","vTex2","vTex3","vTex4","vTex5","vTex6","vTex7"};glTransformFeedbackVaryings(program,11,outputs,GL_INTERLEAVED_ATTRIBS);glLinkProgram(program);GLint linked;glGetProgramiv(program,GL_LINK_STATUS,&linked);if(!linked){char log[8192];glGetProgramInfoLog(program,sizeof(log),nullptr,log);std::cerr<<log;return 5;}glUseProgram(program);glUniformBlockBinding(program,glGetUniformBlockIndex(program,"NativeGpuXF"),1);
 const float identity[]={1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1};glUniformMatrix4fv(glGetUniformLocation(program,"mvp"),1,GL_FALSE,identity);
 const float white[]={1,1,1,1,1,1,1,1};glUniform4fv(glGetUniformLocation(program,"uMatColor[0]"),2,white);glUniform4fv(glGetUniformLocation(program,"uAmbColor[0]"),2,white);
 int controls[]={int(g_state.xf[0x0e]),int(g_state.xf[0x0f]),int(g_state.xf[0x10]),int(g_state.xf[0x11])};glUniform1iv(glGetUniformLocation(program,"uChanCtrl[0]"),4,controls);
 float lights[32];for(unsigned j=0;j<32;++j)lights[j]=.125f+float(j%4)*.25f;
 for(auto name:{"uLightCol[0]","uLightPos[0]","uLightDir[0]","uLightCosAtt[0]","uLightDistAtt[0]"})glUniform4fv(glGetUniformLocation(program,name),8,lights);
 std::vector<VertexData> raw(12),cpu(12);
 for(unsigned i=0;i<12;++i){auto& v=raw[i];v.has_pos=true;v.has_norm=scenario&2;v.has_color=scenario&4;v.posMtxIdx=(i*3+scenario)%60;if(v.posMtxIdx>=30)v.posMtxIdx+=2;
 for(unsigned j=0;j<3;++j){v.pos[j]=(int(i*3+j)-17)*.0625f;v.norm[j]=(int(i+j)-7)*.125f;v.object_pos[j]=v.pos[j];v.object_norm[j]=v.has_norm?v.norm[j]:0;}
 for(unsigned j=0;j<4;++j){v.color[j]=(i+j)%8*.125f;v.color1[j]=(i+j)%5*.25f;}
 for(unsigned t=0;t<8;++t){v.tex[t][0]=float(i+t)*.125f;v.tex[t][1]=float(int(i)-int(t))*.0625f;v.texMtxIdx[t]=(scenario&32)?60:uint8_t((t*7+scenario)%62);}
 cpu[i]=v;auto& c=cpu[i];const float* m=snapshot->snap_pos_bank.empty()?snapshot->snap_pos_mtx:snapshot->snap_pos_bank.data()+v.posMtxIdx*4;
 for(unsigned row=0;row<3;++row)c.pos[row]=m[row*4]*v.pos[0]+m[row*4+1]*v.pos[1]+m[row*4+2]*v.pos[2]+m[row*4+3];
 if(v.has_norm){const float* n=snapshot->snap_nrm_bank.empty()?snapshot->snap_nrm_mtx:snapshot->snap_nrm_bank.data()+3*(v.posMtxIdx&31);for(unsigned row=0;row<3;++row)c.norm[row]=n[row*3]*v.norm[0]+n[row*3+1]*v.norm[1]+n[row*3+2]*v.norm[2];}c.pre_xf=true;
 }
 float reference[432],got[432];
 for(unsigned mode=0;mode<2;++mode){s_gpu_vertex_mode=mode;s_gpu_vertex_snapshot=snapshot;s_batch.assign(12,BatchVertex{});for(unsigned i=0;i<12;++i)NativeWriteConvertedVertex(mode?raw[i]:cpu[i],s_batch[i]);
 if((scenario&15)==0)s_gpu_xf_ring.cursor=s_gpu_xf_ring.capacity; // force orphan before reuse
 NativeUploadGpuXf();
 GLint64 offset=0,size=0;GLint alignment=0;glGetInteger64i_v(GL_UNIFORM_BUFFER_START,1,&offset);glGetInteger64i_v(GL_UNIFORM_BUFFER_SIZE,1,&size);glGetIntegerv(GL_UNIFORM_BUFFER_OFFSET_ALIGNMENT,&alignment);
 if(offset%alignment || size!=sizeof(NativeGpuXfBlock)){std::cerr<<"Bad XF range\n";return 8;}
 glBindBuffer(GL_ARRAY_BUFFER,vbo);NativeUploadBatch();
 if(mode){GLint stride=0;glGetVertexAttribiv(0,GL_VERTEX_ATTRIB_ARRAY_STRIDE,&stride);unsigned needed=std::min(8u,std::max(1u,unsigned(g_state.bp[0]&15u)));for(unsigned t=0;t<(g_state.bp[0]&15u);++t){unsigned info=g_state.xf[0x40+t],type=(info>>4)&7u,source=(info>>7)&31u;if(type!=2u&&type!=3u&&source>=5u&&source<=12u)needed=std::max(needed,source-4u);}if(size_t(stride)!=offsetof(BatchVertex,texgen)+12*needed)return 9;packed_bytes+=stride*12;full_bytes+=sizeof(BatchVertex)*12;}
 glEnable(GL_RASTERIZER_DISCARD);glBeginTransformFeedback(GL_POINTS);glDrawArrays(GL_POINTS,s_upload_first,12);glEndTransformFeedback();glDisable(GL_RASTERIZER_DISCARD);glBindBuffer(GL_TRANSFORM_FEEDBACK_BUFFER,feedback);glGetBufferSubData(GL_TRANSFORM_FEEDBACK_BUFFER,0,sizeof(reference),mode?got:reference);}
 for(unsigned i=0;i<432;++i){++checks;float error=std::abs(reference[i]-got[i]);max_error=std::max(max_error,error);bool ok=(std::isnan(reference[i])&&std::isnan(got[i])) || reference[i]==got[i] || error<=3e-5f*(1+std::abs(reference[i]));if(!ok){if(failed++<10)std::cout<<"FAIL scenario="<<scenario<<" component="<<i<<" ref="<<reference[i]<<" got="<<got[i]<<'\n';}}
 glDeleteProgram(program);
}
auto err=glGetError();std::cout<<"GPU_XF_COMPONENTS "<<checks-failed<<"/"<<checks<<" max_abs_error="<<max_error<<" gl_error="<<err<<'\n';std::cout<<"GPU_PACK_BYTES "<<packed_bytes<<"/"<<full_bytes<<'\n';SDL_GL_DeleteContext(context);SDL_DestroyWindow(window);SDL_Quit();return failed||err?1:0;}
'''
cpp=work/'test.cpp';exe=work/'test.exe';cpp.write_text(code)
cmd=json.loads((p/'reports/gx-depth-mask-tests.json').read_text())[0]['command']
cmd=[str(cpp) if x.endswith(('gx-depth-tests/test.cpp','gx-depth-tests\\test.cpp')) else str(exe) if x.endswith(('gx-depth-tests/test.exe','gx-depth-tests\\test.exe')) else x for x in cmd]
cmd+=['-I',str(rt/'include'),str(rt/'src/hle/gx/tev_shader_gen.cpp')]
rows=[]
for command in (cmd,[str(exe)]):
    r=subprocess.run(command,capture_output=True,text=True,timeout=240);rows.append(dict(command=command,exit_code=r.returncode,output=r.stdout+r.stderr));(p/'reports/gpu-vertex-tests.json').write_text(json.dumps(rows,indent=2)+'\n');print(rows[-1]['output'][-5000:],flush=True)
    if r.returncode:raise SystemExit(r.returncode)
