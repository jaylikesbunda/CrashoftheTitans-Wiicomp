"""Actual generated fog/alpha GLSL and production uniform updates: GPU pixel equivalence and source cardinality."""
from pathlib import Path
from port_paths import default_storage
import json,os,re,subprocess
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime';w=default_storage()/'shader-data-tests';w.mkdir(exist_ok=True)
s=(rt/'src/hle/gx/renderer_gl.cpp').read_text()
def function(anchor):
 a=s.index(anchor);b=s.index('{',a)+1;depth=1
 while depth:depth+=(s[b]=='{')-(s[b]=='}');b+=1
 return s[a:b]
cache=function('static bool NativeUniformChanged(')
a=s.index('  if(NativeShaderDataUniformsEnabled()){ // NATIVE_SHADER_DATA_UPLOAD_V1');b=s.index('\n  }',a)+4;upload=s[a:b]
code=r'''#define SDL_MAIN_HANDLED
#include <SDL.h>
#include <glad/glad.h>
#include "runtime/gx/tev_shader_gen.h"
#include "runtime/native_shader_data.h"
#include <bit>
#include <unordered_map>
#include <vector>
#include <cstring>
#include <iostream>
#include <fstream>
using namespace nwii::runtime::gx;
namespace nwii::runtime::gx {GXState g_state;}
struct Locations{GLuint id;GLint uNativeFogData,uNativeFogColor,uNativeAlphaRef;};Locations s_active_shader;
'''+cache+'\nvoid upload(){'+upload+'}\n'+r'''
GLuint compile(GLenum type,const std::string& source){GLuint shader=glCreateShader(type);const char* data=source.c_str();glShaderSource(shader,1,&data,nullptr);glCompileShader(shader);GLint good=0;glGetShaderiv(shader,GL_COMPILE_STATUS,&good);if(!good){char log[8192];glGetShaderInfoLog(shader,sizeof log,nullptr,log);std::cerr<<log;exit(4);}return shader;}
int main(){SDL_SetMainReady();if(SDL_Init(SDL_INIT_VIDEO))return 2;SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK,SDL_GL_CONTEXT_PROFILE_CORE);
 auto window=SDL_CreateWindow("Shader data qualification",0,0,256,16,SDL_WINDOW_OPENGL|SDL_WINDOW_HIDDEN);auto context=SDL_GL_CreateContext(window);if(!context||!gladLoadGLLoader((GLADloadproc)SDL_GL_GetProcAddress))return 3;
 GLuint vao;glGenVertexArrays(1,&vao);glBindVertexArray(vao);glViewport(0,0,256,16);
 std::string vertex="#version 330 core\nout vec4 vColor0,vColor1;out vec3 vTex0,vTex1,vTex2,vTex3,vTex4,vTex5,vTex6,vTex7;void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2)*2.0-1.0;gl_Position=vec4(p,.15*(p.x+p.y),1);vColor0=vColor1=vec4(1);vTex0=vTex1=vTex2=vTex3=vTex4=vTex5=vTex6=vTex7=vec3(0);}";
 std::ofstream images(NativeShaderDataUniformsEnabled()?"uniform.bin":"literal.bin",std::ios::binary);
 GLuint vs=compile(GL_VERTEX_SHADER,vertex);std::unordered_map<std::string,Locations> programs;uint64_t hash=1469598103934665603ull,pixels=0;unsigned cases=0;
 auto draw=[&](){auto source=GenerateTEVShader(g_state,0x90).fragment_source;
  const std::string controlled="    FragColor = vec4(.25,.5,.75,floor(gl_FragCoord.x)/255.0);";
  size_t point=source.find("    FragColor ="),end=source.find(';',point);if(point==std::string::npos || end==std::string::npos)exit(5);source.replace(point,end-point+1,controlled);
  auto it=programs.find(source);if(it==programs.end()){GLuint fs=compile(GL_FRAGMENT_SHADER,source),id=glCreateProgram();glAttachShader(id,vs);glAttachShader(id,fs);glLinkProgram(id);GLint good=0;glGetProgramiv(id,GL_LINK_STATUS,&good);if(!good)exit(6);glDeleteShader(fs);
   it=programs.emplace(source,Locations{id,glGetUniformLocation(id,"uNativeFogData"),glGetUniformLocation(id,"uNativeFogColor"),glGetUniformLocation(id,"uNativeAlphaRef")}).first;}
  s_active_shader=it->second;glUseProgram(s_active_shader.id);upload();glClearColor(.13,.17,.23,.19);glClear(GL_COLOR_BUFFER_BIT);glDrawArrays(GL_TRIANGLES,0,3);
  unsigned char image[256*16*4];glReadPixels(0,0,256,16,GL_RGBA,GL_UNSIGNED_BYTE,image);
  if(cases==184){std::cout<<"DEBUG_ALPHA comp="<<unsigned(g_state.alphaTest.comp0)<<'/'<<unsigned(g_state.alphaTest.comp1)<<" refs="<<unsigned(g_state.alphaTest.ref0)<<'/'<<unsigned(g_state.alphaTest.ref1)<<" loc="<<s_active_shader.uNativeAlphaRef<<" pix="<<unsigned(image[4])<<'/'<<unsigned(image[8])<<'\n'<<source.substr(source.rfind("if (!("))<<'\n';if(s_active_shader.uNativeAlphaRef>=0){float values[2];glGetUniformfv(s_active_shader.id,s_active_shader.uNativeAlphaRef,values);std::cout<<"DEBUG_UNIFORM "<<values[0]<<'/'<<values[1]<<'\n';}}
  images.write(reinterpret_cast<const char*>(image),sizeof image);for(unsigned char value:image)hash=(hash^value)*1099511628211ull;pixels+=256*16;++cases;};
 const unsigned refs[]={0,1,31,63,127,128,254,255};
 for(unsigned logic=0;logic<4;++logic)for(unsigned a=0;a<8;++a)for(unsigned b=0;b<8;++b)for(unsigned r=0;r<8;++r){g_state={};g_state.alphaTest.comp0=a;g_state.alphaTest.comp1=b;g_state.alphaTest.logic=logic;g_state.alphaTest.ref0=refs[r];g_state.alphaTest.ref1=refs[(r*3+1)%8];draw();}
 unsigned alpha_programs=unsigned(programs.size());
 std::cout<<"ALPHA_HASH "<<hash<<'\n';
 for(unsigned type=1;type<8;++type)for(unsigned ortho=0;ortho<2;++ortho)for(unsigned variant=0;variant<48;++variant){g_state={};g_state.alphaTest.comp0=g_state.alphaTest.comp1=7;g_state.alphaTest.logic=0;
  float A=variant%7==0?0.f:(float(int(variant%13)-3)/8.f),C=float(int(variant%11)-4)/8.f;
  g_state.bp[0xee]=(std::bit_cast<uint32_t>(A)>>12)&0xfffffu;
  g_state.bp[0xf1]=(type<<21)|(ortho<<20)|((std::bit_cast<uint32_t>(C)>>12)&0xfffffu);
  g_state.bp[0xef]=0xffffffu-(variant*265443u%0xffffffu);g_state.bp[0xf0]=variant%24;g_state.bp[0xf2]=(variant*1234567u)&0xffffffu;draw();}
 const auto error=glGetError();std::cout<<"SHADER_DATA_PIXELS cases="<<cases<<" pixels="<<pixels<<" hash="<<hash<<" programs="<<programs.size()<<" alpha_programs="<<alpha_programs<<" gl_error="<<error<<'\n';return error?7:0;
}
'''
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(code)
cmd=json.loads((p/'reports/gx-depth-mask-tests.json').read_text())[0]['command']
cmd=[str(cpp) if x.endswith(('gx-depth-tests/test.cpp','gx-depth-tests\\test.cpp')) else str(exe) if x.endswith(('gx-depth-tests/test.exe','gx-depth-tests\\test.exe')) else x for x in cmd]
cmd+=['-I',str(rt/'include'),str(rt/'src/hle/gx/tev_shader_gen.cpp')];rows=[]
def run(command,mode=None):
 env={k:v for k,v in os.environ.items() if not k.startswith('NWII_')}
 if mode=='literal':env['NWII_LITERAL_SHADER_DATA']='1'
 r=subprocess.run(command,capture_output=True,text=True,env=env,timeout=600,cwd=w);rows.append(dict(mode=mode,command=command,exit_code=r.returncode,output=r.stdout+r.stderr));print((r.stdout+r.stderr)[-2500:],flush=True)
 if r.returncode:raise SystemExit(r.returncode)
 return r.stdout
try:
 run(cmd);old=run([str(exe)],'literal');new=run([str(exe)],'uniform')
 def result(s):return dict(re.findall(r'(cases|pixels|hash|programs|alpha_programs)=(\d+)',s))
 a,b=result(old),result(new);assert all(a[k]==b[k] for k in ('cases','pixels','hash')),(a,b);assert int(b['programs'])<int(a['programs'])//2,(a,b)
 print('Actual fog/alpha pixels agree; changing data reuses programs. '+str(dict(literal=a,uniform=b)),flush=True)
finally:(p/'reports/shader-data-uniform-tests.json').write_text(json.dumps(rows,indent=2)+'\n')
