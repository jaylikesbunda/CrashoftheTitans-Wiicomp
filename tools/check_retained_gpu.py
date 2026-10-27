"""Production upload path: byte and real-GL image comparisons through cache hits,
layout changes, copy-on-write mutations, live transforms and bounded eviction.
"""
from pathlib import Path
from port_paths import default_storage
import json,subprocess
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime';base=default_storage();w=base/'retained-gpu-tests';w.mkdir(exist_ok=True)
s=(rt/'src/hle/gx/renderer_gl.cpp').read_text()
def section(a,b):return s[s.index(a):s.index(b,s.index(a))]
struct=section('struct BatchVertex {','static bool s_gpu_vertex_mode=')
upload=section('// NATIVE_STREAM_UPLOAD_V1:','static void FlushBatch(')
code=r'''
#define SDL_MAIN_HANDLED
#include <SDL.h>
#include <glad/glad.h>
#include "runtime/native_gl_bind_shadow.h"
#include "runtime/gx/fifo_parser.h"
#include "runtime/native_geometry_stats.h"
#include "runtime/native_frame_trace_gl.h"
#include <iostream>
#include <algorithm>
#include <cstring>
#include <vector>
#include <unordered_map>
using namespace nwii::runtime::gx;
namespace nwii::runtime::gx {GXState g_state;}
'''+struct+r'''
static bool s_gpu_vertex_mode=true;
static GLuint s_vao=0,s_vbo=0;
static std::vector<BatchVertex> s_batch;
struct NativeGpuSpan {const VertexData* vertices;size_t count;const GXCommand* packed=nullptr;};
static std::vector<NativeGpuSpan> s_gpu_spans;
static size_t s_gpu_span_vertices=0;
static size_t NativeBatchSize(){return s_gpu_span_vertices?s_gpu_span_vertices:s_batch.size();}
static void NativePackGpuVertex(const VertexData&,float*,unsigned){std::abort();}
'''+upload+r'''
int main(){SDL_SetMainReady();if(SDL_Init(SDL_INIT_VIDEO))return 2;
 SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK,SDL_GL_CONTEXT_PROFILE_CORE);
 auto window=SDL_CreateWindow("Retained geometry fixture",0,0,32,32,SDL_WINDOW_OPENGL|SDL_WINDOW_HIDDEN);auto context=SDL_GL_CreateContext(window);
 if(!context || !gladLoadGLLoader((GLADloadproc)SDL_GL_GetProcAddress))return 3;
 auto compile=[](GLenum type,const char* source){auto sh=glCreateShader(type);glShaderSource(sh,1,&source,nullptr);glCompileShader(sh);GLint ok;glGetShaderiv(sh,GL_COMPILE_STATUS,&ok);if(!ok)std::abort();return sh;};
 const char* vs="#version 330 core\nlayout(location=0)in vec3 p;layout(location=1)in vec4 color;layout(location=3)in vec3 normal;layout(location=5)in vec3 t0;layout(location=6)in vec3 t1;uniform float offset;out vec4 c;void main(){gl_Position=vec4(p+vec3(offset,0,0),1);c=color+vec4((t0.xy+t1.xy)*.03,normal.x*.01,0);}";
 const char* fs="#version 330 core\nin vec4 c;out vec4 frag;void main(){frag=c;}";
 auto program=glCreateProgram();glAttachShader(program,compile(GL_VERTEX_SHADER,vs));glAttachShader(program,compile(GL_FRAGMENT_SHADER,fs));glLinkProgram(program);glUseProgram(program);glViewport(0,0,32,32);
 GLuint vao,vbo;glGenVertexArrays(1,&vao);glBindVertexArray(vao);glGenBuffers(1,&vbo);glBindBuffer(GL_ARRAY_BUFFER,vbo);
 s_vao=vao;s_vbo=vbo;
 for(unsigned i=0;i<5;++i)glEnableVertexAttribArray(i);
 unsigned checks=0;
 native_hud::on=true;
 for(unsigned scenario=0;scenario<540;++scenario){
  GXCommand c{};c.native_packed_stride=22;c.native_packed.resize(6*22);c.native_geometry_retained=true;
  for(unsigned i=0;i<6;++i){float* d=c.native_packed.data()+22*i;std::fill(d,d+22,0.f);
   d[0]=(i%3==1?.75f:-.75f);d[1]=(i%3==2?.75f:-.75f);d[2]=0.f;d[3]=.2f;d[4]=.4f;d[5]=.6f;d[6]=1.f;d[16]=float(i)*.1f;d[19]=float(i)*.05f;}
  GXCommand second=c;second.native_packed.data()[3]=.65f;second.native_packed.data()[0]+=.125f;
  const unsigned vertex_count=scenario%3?6:12;
  for(unsigned generators:{1u,2u})for(unsigned round=0;round<3;++round){
   g_state.bp[0]=generators;g_state.xf[0x40]=5<<7;g_state.xf[0x41]=6<<7;
   if(round==2){c.native_packed.data()[3]=.7f;} // detaches from both retained VAOs
   glUniform1f(glGetUniformLocation(program,"offset"),float(round)*.0625f);
   unsigned char reference[4096],actual[4096];
   for(unsigned mode=0;mode<2;++mode){
    glBindVertexArray(vao);glBindBuffer(GL_ARRAY_BUFFER,vbo);
    s_gpu_spans={{nullptr,6,&c}};if(vertex_count==12)s_gpu_spans.push_back({nullptr,6,&second});s_gpu_span_vertices=vertex_count;c.native_geometry_retained=second.native_geometry_retained=bool(mode);
    NativeUploadBatch();
    GLint bound=0,stride=0;glGetVertexAttribiv(0,GL_VERTEX_ATTRIB_ARRAY_BUFFER_BINDING,&bound);glGetVertexAttribiv(0,GL_VERTEX_ATTRIB_ARRAY_STRIDE,&stride);
    if(stride!=int(64+generators*12))return 4;
    glBindBuffer(GL_ARRAY_BUFFER,GLuint(bound));std::vector<uint8_t> bytes(size_t(stride)*vertex_count);glGetBufferSubData(GL_ARRAY_BUFFER,size_t(s_upload_first)*stride,bytes.size(),bytes.data());
    const auto& immutable=c;
    for(unsigned i=0;i<vertex_count;++i){const GXCommand& source=i<6?immutable:second;if(std::memcmp(bytes.data()+size_t(i)*stride,source.native_packed.data()+22*(i%6),stride))return 5;}
    glClearColor(.1f,.2f,.3f,1.f);glClear(GL_COLOR_BUFFER_BIT);glDrawArrays(GL_TRIANGLES,s_upload_first,vertex_count);glReadPixels(0,0,32,32,GL_RGBA,GL_UNSIGNED_BYTE,mode?actual:reference);
   }
   if(std::memcmp(reference,actual,sizeof(reference)))return 6;++checks;
  }
 }
 const auto err=glGetError();
 if(s_geometry_gpu.size()>512 || s_geometry_gpu_bytes>(32u<<20) || s_geometry_gpu_owner_bytes>(32u<<20) || native_geometry::gpu_hits.load()==0)return 7;
 std::cout<<"RETAINED_GPU checks="<<checks<<" hits="<<native_geometry::gpu_hits.load()<<" saved_bytes="<<native_geometry::saved_upload_bytes.load()<<" entries="<<s_geometry_gpu.size()<<" gl_error="<<err<<'\n';
 SDL_GL_DeleteContext(context);SDL_DestroyWindow(window);SDL_Quit();return err?8:0;
}
'''
code=code.replace('glBindVertexArray(', 'native_gl_bind_shadow::BindVertexArray(')
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(code)
cmd=json.loads((p/'reports/gx-depth-mask-tests.json').read_text())[0]['command']
cmd=[str(cpp) if x.endswith(('gx-depth-tests/test.cpp','gx-depth-tests\\test.cpp')) else str(exe) if x.endswith(('gx-depth-tests/test.exe','gx-depth-tests\\test.exe')) else x for x in cmd]
cmd+=['-I',str(rt/'include')];rows=[]
for command in (cmd,[str(exe)]):
 r=subprocess.run(command,capture_output=True,text=True,timeout=180);rows.append(dict(command=command,exit_code=r.returncode,output=r.stdout+r.stderr));print((r.stdout+r.stderr)[-2000:],flush=True)
 (p/'reports/retained-gpu-tests.json').write_text(json.dumps(rows,indent=2)+'\n')
 if r.returncode:raise SystemExit(r.returncode)
