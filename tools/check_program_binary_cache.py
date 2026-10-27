"""Cold/warm/corrupt runs of the production NativeProgramCache with production GLSL on a hidden GL context.

Each process compiles the same 48 production TEV programs. The cold run stores binaries; the warm run must
load every one (hits==48) with identical active-uniform signatures and identical rendered pixels; the
corrupt run damages two files and must reject exactly those and still match. No game launch.
"""
from pathlib import Path
from port_paths import default_storage
import json,re,shutil,subprocess,sys
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
w=default_storage()/'program-cache-tests';w.mkdir(exist_ok=True)
s=(rt/'src/hle/gx/renderer_gl.cpp').read_text()
a=s.index('#ifndef GL_PROGRAM_BINARY_LENGTH');b=s.index('static GLuint CompileShader(const char *vs, const char *fs) {')
b=s.index('\n}\n',b)+3
production=s[a:b]
code=r'''
#define SDL_MAIN_HANDLED
#include <SDL.h>
#include <glad/glad.h>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include "runtime/gx/tev_shader_gen.h"
using namespace nwii::runtime::gx;
#include "runtime/native_frame_trace.h"
#include "runtime/native_shader_cache_io.h"
namespace nwii::runtime::gx {GXState g_state;
'''+production+r'''
void configure(unsigned variant) {
  g_state={};uint32_t rng=0x9e3779b9u*(variant+1);auto next=[&](){rng=rng*1664525u+1013904223u;return rng>>8;};
  const unsigned stages=1+variant%8,gens=1+variant%4;
  g_state.numTevStages=uint8_t(stages);g_state.bp[0]=gens|((stages-1)<<10)|((variant%3==0?2u:0u)<<16);
  for(unsigned i=0;i<stages;++i){auto& t=g_state.tevStages[i];
    t.colorInA=next()%16;t.colorInB=next()%16;t.colorInC=next()%16;t.colorInD=next()%16;t.colorOp=next()%2;t.colorBias=next()%3;t.colorScale=next()%4;t.colorClamp=next()%2;t.colorRegId=next()%4;
    t.alphaInA=next()%8;t.alphaInB=next()%8;t.alphaInC=next()%8;t.alphaInD=next()%8;t.alphaOp=next()%2;t.alphaBias=next()%3;t.alphaScale=next()%4;t.alphaClamp=next()%2;t.alphaRegId=next()%4;
    t.texMap=(next()%5==0)?0xFF:uint8_t(next()%8);t.texCoord=next()%8;t.colorChan=next()%3;}
  for(unsigned i=0;i<8;++i)g_state.xf[0x40+i]=(next()%4==0?2u:0u)<<4|(next()%3==0?4u:0u);
  g_state.alphaTest.comp0=next()%8;g_state.alphaTest.comp1=next()%8;g_state.alphaTest.ref0=next()&255;g_state.alphaTest.ref1=next()&255;g_state.alphaTest.logic=next()%4;
  if(variant%5==0){g_state.bp[0xf1]=(next()%7)<<21;g_state.bp[0xee]=next();g_state.bp[0xef]=next()&0xffffff;g_state.bp[0xf0]=next()&31;g_state.bp[0xf2]=next()&0xffffff;}
  for(unsigned reg=0xf6;reg<=0xfd;++reg)g_state.bp[reg]=next()&0xffffff;
}
}
int main(int argc,char** argv){
  native_hud::on=true;
  SDL_SetMainReady();if(SDL_Init(SDL_INIT_VIDEO))return 2;
  SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK,SDL_GL_CONTEXT_PROFILE_CORE);
  auto window=SDL_CreateWindow("program cache",0,0,16,16,SDL_WINDOW_OPENGL|SDL_WINDOW_HIDDEN);auto context=SDL_GL_CreateContext(window);
  if(!context||!gladLoadGLLoader((GLADloadproc)SDL_GL_GetProcAddress))return 3;
  GLuint vao,vbo;glGenVertexArrays(1,&vao);glBindVertexArray(vao);glGenBuffers(1,&vbo);glBindBuffer(GL_ARRAY_BUFFER,vbo);
  const float tri[]={-1,-1,0, 3,-1,0, -1,3,0};glBufferData(GL_ARRAY_BUFFER,sizeof tri,tri,GL_STATIC_DRAW);glEnableVertexAttribArray(0);glVertexAttribPointer(0,3,GL_FLOAT,GL_FALSE,12,nullptr);
  for(unsigned unit=0;unit<8;++unit){GLuint tex;glGenTextures(1,&tex);glActiveTexture(GL_TEXTURE0+unit);glBindTexture(GL_TEXTURE_2D,tex);
    unsigned char px[]={uint8_t(40+unit*25),uint8_t(200-unit*20),uint8_t(unit*31),uint8_t(255-unit*9)};glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,1,1,0,GL_RGBA,GL_UNSIGNED_BYTE,px);}
  glViewport(0,0,16,16);
  const unsigned count=48;double total_ms=0,worst_ms=0;auto& cache=nwii::runtime::gx::NativeProgramBinaryCache();
  for(unsigned variant=0;variant<count;++variant){
    nwii::runtime::gx::configure(variant);auto src=GenerateTEVShader(nwii::runtime::gx::g_state,0x90);
    auto t0=std::chrono::steady_clock::now();GLuint program=nwii::runtime::gx::CompileShader(src.vertex_source.c_str(),src.fragment_source.c_str());
    glFinish();double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-t0).count();total_ms+=ms;if(ms>worst_ms)worst_ms=ms;
    GLint linked=0;glGetProgramiv(program,GL_LINK_STATUS,&linked);if(!linked){std::cout<<"LINK_FAIL "<<variant<<'\n';return 4;}
    uint64_t signature=1469598103934665603ull;auto add=[&](uint64_t v){signature=(signature^v)*1099511628211ull;};
    GLint uniforms=0;glGetProgramiv(program,GL_ACTIVE_UNIFORMS,&uniforms);
    for(GLint i=0;i<uniforms;++i){char name[256]={};GLsizei length=0;GLint size=0;GLenum type=0;glGetActiveUniform(program,i,255,&length,&size,&type,name);
      for(const char* c=name;*c;++c)add(uint8_t(*c));add(type);add(size);add(uint64_t(glGetUniformLocation(program,name)+1));}
    const GLuint block=glGetUniformBlockIndex(program,"NativeGpuXF");add(block);
    if(block!=GL_INVALID_INDEX){GLint bytes=0;glGetActiveUniformBlockiv(program,block,GL_UNIFORM_BLOCK_DATA_SIZE,&bytes);add(bytes);glUniformBlockBinding(program,block,1);}
    glUseProgram(program);
    for(unsigned unit=0;unit<8;++unit){GLint loc=glGetUniformLocation(program,("uTex"+std::to_string(unit)).c_str());if(loc>=0)glUniform1i(loc,int(unit));}
    float identity[16]={1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1};GLint mvp=glGetUniformLocation(program,"mvp");if(mvp>=0)glUniformMatrix4fv(mvp,1,GL_FALSE,identity);
    glClearColor(.25f,.5f,.75f,1);glClear(GL_COLOR_BUFFER_BIT);glDrawArrays(GL_TRIANGLES,0,3);
    uint64_t pixels=1469598103934665603ull;unsigned char row[16*4];for(int y=0;y<16;y+=5){glReadPixels(0,y,16,1,GL_RGBA,GL_UNSIGNED_BYTE,row);for(unsigned char c:row)pixels=(pixels^c)*1099511628211ull;}
    std::cout<<"PROGRAM "<<variant<<" uniforms="<<signature<<" pixels="<<pixels<<" ms="<<ms<<'\n';
    glDeleteProgram(program);
  }
  cache.flush();const GLenum error=glGetError();
  std::cout<<"SUMMARY enabled="<<cache.enabled<<" hits="<<cache.hits<<" stores="<<cache.stores<<" rejected="<<cache.rejected<<" total_ms="<<total_ms<<" worst_ms="<<worst_ms<<" gl_error="<<error<<" hud_compiles="<<native_hud::work_shader_compiles.load()<<" hud_binary_hits="<<native_hud::work_shader_binary_hits.load()<<'\n';
  SDL_GL_DeleteContext(context);SDL_DestroyWindow(window);SDL_Quit();return error?5:0;
}
'''
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(code)
cmd=json.loads((p/'reports/gx-depth-mask-tests.json').read_text())[0]['command']
cmd=[str(cpp) if x.endswith(('gx-depth-tests/test.cpp','gx-depth-tests\\test.cpp')) else str(exe) if x.endswith(('gx-depth-tests/test.exe','gx-depth-tests\\test.exe')) else x for x in cmd]
cmd+=['-I',str(rt/'include'),str(rt/'src/hle/gx/tev_shader_gen.cpp')]
rows=[]
def run(command,env=None):
    import os
    environment={k:v for k,v in os.environ.items() if not k.startswith('NWII_')}
    if env:environment.update(env)
    r=subprocess.run(command,capture_output=True,text=True,timeout=900,env=environment)
    rows.append(dict(command=command,exit_code=r.returncode,output=r.stdout+r.stderr));(p/'reports/program-cache-tests.json').write_text(json.dumps(rows,indent=2)+'\n')
    print((r.stdout+r.stderr)[-700:],flush=True)
    if r.returncode:raise SystemExit(r.returncode)
    return r.stdout
run(cmd)
directory=w/'cache';shutil.rmtree(directory,ignore_errors=True)
env={'NWII_PROGRAM_CACHE_DIR':str(directory)}
def parse(output):
    programs={int(m[1]):(m[2],m[3]) for m in re.finditer(r'PROGRAM (\d+) uniforms=(\d+) pixels=(\d+)',output)}
    summary=dict(re.findall(r'(\w+)=([\d.]+)',re.search(r'SUMMARY.*',output)[0]))
    return programs,summary
cold,c=parse(run([str(exe)],env));warm,x=parse(run([str(exe)],env))
assert len(cold)==48 and cold==warm,'binary-loaded programs differ'
files=sorted(directory.glob('*.bin'));assert int(c['stores'])==len(files) and int(c['hits'])+int(c['stores'])==48,(c,len(files))
assert int(x['hits'])==48 and int(x['rejected'])==0 and int(x['stores'])==0,(c,x)
assert int(c['hud_compiles'])==int(c['stores']) and int(x['hud_compiles'])==0 and int(x['hud_binary_hits'])==48,(c,x)
files[0].write_bytes(files[0].read_bytes()[:100]);data=bytearray(files[1].read_bytes());data[len(data)//2]^=0xff;data[0]^=0xff;files[1].write_bytes(data)
bad,d=parse(run([str(exe)],env));assert bad==cold and int(d['rejected'])>=2 and int(d['stores'])==2,(d,)
print(f"PROGRAM_CACHE cold_total_ms={c['total_ms']} warm_total_ms={x['total_ms']} programs={len(cold)} binaries={len(files)} corrupt_rejected={d['rejected']}; production sources, pixels and uniform signatures agree")
(p/'reports/program-cache-speedup.json').write_text(json.dumps(dict(cold=c,warm=x,corrupt=d,programs=len(cold)),indent=2)+'\n')
