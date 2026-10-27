"""Production source keys and alias sampler history, with hidden GL pixels."""
from pathlib import Path
from port_paths import default_storage
import json,subprocess
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime';w=default_storage()/'shader-source-tests';w.mkdir(exist_ok=True)
s=(rt/'src/hle/gx/renderer_gl.cpp').read_text()
def function(anchor):
 a=s.index(anchor);i=s.index('{',a)+1;depth=1
 while depth:depth+=(s[i]=='{')-(s[i]=='}');i+=1
 return s[a:i]+'\n'
struct=s[s.index('struct GLShader {'):s.index('uint64_t g_stat_hash0')]
uniform=function('static bool NativeUniformChanged(');aliases=function('static void NativeApplyShaderSamplerAliases(')
code=r'''
#define SDL_MAIN_HANDLED
#include <SDL.h>
#include <glad/glad.h>
#include "runtime/gx/tev_shader_gen.h"
#include <unordered_map>
#include <unordered_set>
#include <string>
#include <array>
#include <vector>
#include <cstring>
#include <iostream>
using namespace nwii::runtime::gx;
namespace nwii::runtime::gx {GXState g_state;}
'''+struct+'static GLShader s_active_shader={};\n'+uniform+aliases+r'''
GLuint program(const std::string& vs,const std::string& fs){auto compile=[](GLenum type,const std::string& text){GLuint shader=glCreateShader(type);const char* p=text.c_str();glShaderSource(shader,1,&p,nullptr);glCompileShader(shader);GLint ok=0;glGetShaderiv(shader,GL_COMPILE_STATUS,&ok);if(!ok)exit(4);return shader;};GLuint id=glCreateProgram();GLuint v=compile(GL_VERTEX_SHADER,vs),f=compile(GL_FRAGMENT_SHADER,fs);glAttachShader(id,v);glAttachShader(id,f);glLinkProgram(id);GLint ok=0;glGetProgramiv(id,GL_LINK_STATUS,&ok);glDeleteShader(v);glDeleteShader(f);if(!ok)exit(5);return id;}
int main(){SDL_SetMainReady();if(SDL_Init(SDL_INIT_VIDEO))return 2;SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK,SDL_GL_CONTEXT_PROFILE_CORE);
auto window=SDL_CreateWindow("Offline shader-source alias fixture",0,0,16,16,SDL_WINDOW_OPENGL|SDL_WINDOW_HIDDEN);auto context=SDL_GL_CreateContext(window);if(!context||!gladLoadGLLoader((GLADloadproc)SDL_GL_GetProcAddress))return 3;
std::unordered_set<uint64_t> keys;std::unordered_set<std::string> sources;
for(unsigned family=0;family<8;++family){std::string expected;
for(unsigned variant=0;variant<256;++variant){g_state={};g_state.bp[0]=1;g_state.numTevStages=1;g_state.tevStages[0].texMap=family;g_state.tevStages[0].texCoord=0;
g_state.bp[0x28]=family;g_state.bp[0x40]=variant;g_state.bp[0x41]=variant*123;g_state.bp[0x43]=variant*17;g_state.cp[0x50]=variant*99;g_state.texStages[7].format=variant%15;
auto src=GenerateTEVShader(g_state,0x90);auto key=NativeShaderSourceKey(src.vertex_source,src.fragment_source);if(!variant)expected=key;else if(expected!=key){std::cerr<<"Unexpected source-dependent field\n";return 6;}
keys.insert(g_state.GetShaderHash(0x90));sources.insert(key);}}
std::cout<<"SHADER_SOURCE_VARIANTS old_keys="<<keys.size()<<" exact_sources="<<sources.size()<<'\n';
GLuint vao;glGenVertexArrays(1,&vao);glBindVertexArray(vao);glViewport(0,0,16,16);
for(unsigned unit=0;unit<8;++unit){GLuint tex;glGenTextures(1,&tex);glActiveTexture(GL_TEXTURE0+unit);glBindTexture(GL_TEXTURE_2D,tex);unsigned char pixel[]={uint8_t(unit==0?255:0),uint8_t(unit==3?255:0),uint8_t(unit==5?255:0),255};glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,1,1,0,GL_RGBA,GL_UNSIGNED_BYTE,pixel);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_NEAREST);}
const std::string vs="#version 330 core\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.0-1.0,0,1);}";
std::vector<std::array<unsigned char,4>> reference;unsigned checks=0;unsigned programs[2]={};
for(unsigned mode=0;mode<2;++mode){s_shader_cache.clear();s_shader_sources.clear();g_state={};
for(unsigned frame=0;frame<2048;++frame){const unsigned alias=((frame*17u)^(frame/64))%64,unit=(alias&1)?3:5;const uint64_t hash=1+alias;
const std::string fs="#version 330 core\nuniform sampler2D uTex"+std::to_string(unit)+";out vec4 color;void main(){color=texture(uTex"+std::to_string(unit)+",vec2(.5));}";
auto it=s_shader_cache.find(hash);if(it==s_shader_cache.end()){GLShader sh={};const auto key=NativeShaderSourceKey(vs,fs);auto found=s_shader_sources.find(key);
if(mode && found!=s_shader_sources.end())sh=found->second;else{sh.id=program(vs,fs);for(unsigned i=0;i<8;++i)sh.uTex[i]=glGetUniformLocation(sh.id,("uTex"+std::to_string(i)).c_str());s_shader_sources[key]=sh;++programs[mode];}
it=s_shader_cache.emplace(hash,sh).first;}
s_active_shader=it->second;glUseProgram(s_active_shader.id);
g_state.texStages[unit].base_addr=(frame%7<3)?0:0x80010000;
if(mode)NativeApplyShaderSamplerAliases(hash);else if(g_state.texStages[unit].base_addr && s_active_shader.uTex[unit]>=0){int v=unit;if(NativeUniformChanged(s_active_shader.id,s_active_shader.uTex[unit],&v,sizeof(v)))glUniform1i(s_active_shader.uTex[unit],v);}
glDrawArrays(GL_TRIANGLES,0,3);std::array<unsigned char,4> pixel{};glReadPixels(8,8,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel.data());
if(!mode)reference.push_back(pixel);else{++checks;if(reference[frame]!=pixel){std::cout<<"SHADER_ALIAS_PIXEL_FAIL frame="<<frame<<'\n';return 7;}}}}
auto error=glGetError();std::cout<<"SHADER_ALIAS_PIXELS "<<checks<<"/2048 old_programs="<<programs[0]<<" shared_programs="<<programs[1]<<" gl_error="<<error<<'\n';
SDL_GL_DeleteContext(context);SDL_DestroyWindow(window);SDL_Quit();return error?8:0;}
'''
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(code)
cmd=json.loads((p/'reports/gx-depth-mask-tests.json').read_text())[0]['command'];cmd=[str(cpp) if x.endswith(('gx-depth-tests/test.cpp','gx-depth-tests\\test.cpp')) else str(exe) if x.endswith(('gx-depth-tests/test.exe','gx-depth-tests\\test.exe')) else x for x in cmd];cmd+=['-I',str(rt/'include'),str(rt/'src/hle/gx/tev_shader_gen.cpp')]
rows=[]
for command in (cmd,[str(exe)]):
 r=subprocess.run(command,capture_output=True,text=True,timeout=240);rows.append(dict(command=command,exit_code=r.returncode,output=r.stdout+r.stderr));(p/'reports/shader-source-tests.json').write_text(json.dumps(rows,indent=2)+'\n');print((r.stdout+r.stderr)[-2200:],flush=True)
 if r.returncode:raise SystemExit(r.returncode)
