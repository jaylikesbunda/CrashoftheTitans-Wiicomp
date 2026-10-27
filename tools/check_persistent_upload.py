"""Persistent-mapped upload ring vs the legacy orphan + glBufferSubData path, with the production NativeStreamUpload text.

A hidden GL 3.3 core context draws many indexed batches whose vertices, indices and a per-batch uniform block all stream through
NativeStreamUpload (vertex 32 MiB / index 16 MiB / uniform 4 MiB rings). Later draws overwrite earlier ones with no clears, so the
final image depends on every batch being intact when the GPU read it; any ring segment reused before its fence signalled corrupts
it. About 4 ring laps of the vertex ring plus oversized batches (larger than one 8 MiB segment) are drawn. The process runs twice:
persistent (default) and NWII_NO_PERSISTENT_UPLOAD=1; the final pixel hashes and the sampled intermediate hashes must be equal and the
default run must report an active persistent ring. No game launch.
"""
from pathlib import Path
from port_paths import default_storage
import json,os,subprocess,sys
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
w=default_storage()/'persistent-upload-tests';w.mkdir(exist_ok=True)
s=(rt/'src/hle/gx/renderer_gl.cpp').read_text()
a=s.index('// NATIVE_PERSISTENT_UPLOAD_V1');b=s.index('// NATIVE_COMPACT_VERTEX_UPLOAD_V1')
production=s[a:b]
code=r'''
#define SDL_MAIN_HANDLED
#include <SDL.h>
#include <glad/glad.h>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <random>
#include <string>
#include <vector>
namespace native_trace {
 struct Scope{Scope(const char*){}};
 struct GPUScope{GPUScope(const char*,unsigned){}};
 inline void mark(const char*,uint64_t=0,uint64_t=0){}
}
namespace nwii::runtime::gx {
'''+production+r'''
}
using namespace nwii::runtime::gx;
static const char* kVs=R"(#version 330 core
layout(location=0) in vec2 pos; layout(location=1) in vec4 col; layout(location=2) in vec4 pad0; layout(location=3) in vec4 pad1; layout(location=4) in vec4 pad2;
layout(std140) uniform U {vec4 tint;};
out vec4 v;
void main(){gl_Position=vec4(pos,0.0,1.0);v=col*tint+0.0*(pad0+pad1+pad2);}
)";
static const char* kFs=R"(#version 330 core
in vec4 v; out vec4 o; void main(){o=v;}
)";
static GLuint compile(){
  auto build=[&](GLenum t,const char* src){GLuint s=glCreateShader(t);glShaderSource(s,1,&src,nullptr);glCompileShader(s);GLint ok=0;glGetShaderiv(s,GL_COMPILE_STATUS,&ok);if(!ok){char log[1024];glGetShaderInfoLog(s,1024,nullptr,log);std::cout<<log;exit(3);}return s;};
  GLuint p=glCreateProgram();glAttachShader(p,build(GL_VERTEX_SHADER,kVs));glAttachShader(p,build(GL_FRAGMENT_SHADER,kFs));glLinkProgram(p);return p;}
struct V{float x,y,r,g,b,a;float pad[18];}; // 96 bytes
int main(){
  SDL_SetMainReady();if(SDL_Init(SDL_INIT_VIDEO))return 2;
  SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK,SDL_GL_CONTEXT_PROFILE_CORE);
  auto window=SDL_CreateWindow("upload",0,0,16,16,SDL_WINDOW_OPENGL|SDL_WINDOW_HIDDEN);auto context=SDL_GL_CreateContext(window);
  if(!context||!gladLoadGLLoader((GLADloadproc)SDL_GL_GetProcAddress))return 3;
  const int W=256,H=256;GLuint fbo,color;glGenTextures(1,&color);glBindTexture(GL_TEXTURE_2D,color);glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,W,H,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
  glGenFramebuffers(1,&fbo);glBindFramebuffer(GL_FRAMEBUFFER,fbo);glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,color,0);
  glViewport(0,0,W,H);glClearColor(0,0,0,1);glClear(GL_COLOR_BUFFER_BIT);
  GLuint vao,vbo,ebo,ubo;glGenVertexArrays(1,&vao);glBindVertexArray(vao);glGenBuffers(1,&vbo);glGenBuffers(1,&ebo);glGenBuffers(1,&ubo);
  GLuint prog=compile();glUseProgram(prog);glUniformBlockBinding(prog,glGetUniformBlockIndex(prog,"U"),1);
  GLint alignment=0;glGetIntegerv(GL_UNIFORM_BUFFER_OFFSET_ALIGNMENT,&alignment);
  NativeUploadRing vring,iring,uring;
  std::mt19937 rng(4242);uint64_t hash=1469598103934665603ull;std::vector<uint64_t> checkpoints;
  auto readback=[&](){std::vector<uint8_t> px(W*H*4);glReadPixels(0,0,W,H,GL_RGBA,GL_UNSIGNED_BYTE,px.data());uint64_t h=1469598103934665603ull;for(uint8_t c:px)h=(h^c)*1099511628211ull;return h;};
  const unsigned batches=640;uint64_t total_vertex_bytes=0;
  for(unsigned b=0;b<batches;++b){
    unsigned count=(b%97==60)?110000u:600u+(rng()%1800u);           // a few oversized batches (> one 8 MiB segment)
    std::vector<V> vertices(count);std::vector<uint32_t> indices;
    for(unsigned i=0;i<count;++i){V& v=vertices[i];memset(&v,0,sizeof v);v.x=float(int(rng()%2000)-1000)/1000.f;v.y=float(int(rng()%2000)-1000)/1000.f;v.r=(rng()%256)/255.f;v.g=(rng()%256)/255.f;v.b=(rng()%256)/255.f;v.a=1.f;}
    const unsigned triangles=count/3;for(unsigned t=0;t<triangles;++t){indices.push_back(t*3);indices.push_back(t*3+1);indices.push_back(t*3+2);}
    float tint[4]={0.5f+0.5f*float(b%7)/6.f,0.5f+0.5f*float(b%5)/4.f,0.5f+0.5f*float(b%3)/2.f,1.f};
    glBindBuffer(GL_ARRAY_BUFFER,vbo);
    const size_t first=NativeStreamUpload(GL_ARRAY_BUFFER,vring,vertices.data(),vertices.size()*sizeof(V),sizeof(V))/sizeof(V);total_vertex_bytes+=vertices.size()*sizeof(V);
    glEnableVertexAttribArray(0);glVertexAttribPointer(0,2,GL_FLOAT,GL_FALSE,sizeof(V),(void*)offsetof(V,x));
    glEnableVertexAttribArray(1);glVertexAttribPointer(1,4,GL_FLOAT,GL_FALSE,sizeof(V),(void*)offsetof(V,r));
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,ebo);
    const size_t index_offset=NativeStreamUpload(GL_ELEMENT_ARRAY_BUFFER,iring,indices.data(),indices.size()*4,4);
    glBindBuffer(GL_UNIFORM_BUFFER,ubo);
    const size_t uoffset=NativeStreamUpload(GL_UNIFORM_BUFFER,uring,tint,sizeof tint,size_t(std::max(1,alignment)));
    glBindBufferRange(GL_UNIFORM_BUFFER,1,ubo,GLintptr(uoffset),sizeof tint);
    glDrawElementsBaseVertex(GL_TRIANGLES,GLsizei(indices.size()),GL_UNSIGNED_INT,reinterpret_cast<void*>(index_offset),GLint(first));
    if(b%160==159){uint64_t h=readback();checkpoints.push_back(h);hash=(hash^h)*1099511628211ull;}
  }
  uint64_t final_hash=readback();const GLenum error=glGetError();
  std::cout<<"UPLOAD vertex_active="<<vring.active<<" index_active="<<iring.active<<" uniform_active="<<uring.active<<" vertex_mb="<<(total_vertex_bytes>>20)<<" final="<<final_hash<<" chain="<<hash<<" gl_error="<<error<<'\n';
  SDL_GL_DeleteContext(context);SDL_DestroyWindow(window);SDL_Quit();return error?5:0;
}
'''
if os.environ.get('NWII_CHECK_NEGATIVE'): # negative control: remove the fence wait; the images should then be allowed to differ
    wait='const GLenum state=glClientWaitSync(fence,GL_SYNC_FLUSH_COMMANDS_BIT,1000000000ull);';assert wait in code;code=code.replace(wait,'const GLenum state=GL_ALREADY_SIGNALED;')
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(code)
cmd=json.loads((p/'reports/gx-depth-mask-tests.json').read_text())[0]['command']
cmd=[str(cpp) if x.endswith(('gx-depth-tests/test.cpp','gx-depth-tests\\test.cpp')) else str(exe) if x.endswith(('gx-depth-tests/test.exe','gx-depth-tests\\test.exe')) else x for x in cmd]
cmd+=['-I',str(rt/'include')]
def run(command,env=None):
    environment={k:v for k,v in os.environ.items() if not k.startswith('NWII_')}
    if env:environment.update(env)
    r=subprocess.run(command,capture_output=True,text=True,timeout=900,env=environment)
    print((r.stdout+r.stderr)[-900:],flush=True)
    if r.returncode:raise SystemExit(r.returncode)
    return r.stdout
run(cmd)
fast=run([str(exe)]);legacy=run([str(exe)],{'NWII_NO_PERSISTENT_UPLOAD':'1'})
def parse(text):
    line=[l for l in text.splitlines() if l.startswith('UPLOAD')][0];return dict(x.split('=') for x in line.split()[1:])
f,l=parse(fast),parse(legacy)
assert f['vertex_active']=='1' and f['index_active']=='1' and f['uniform_active']=='1','persistent ring not active (extension missing?)'
assert l['vertex_active']=='0',l
assert f['final']==l['final'] and f['chain']==l['chain'],('image mismatch',f,l)
print(f"PERSISTENT_UPLOAD identical images after {f['vertex_mb']} MiB of streamed vertices across all three rings; legacy path agrees")
