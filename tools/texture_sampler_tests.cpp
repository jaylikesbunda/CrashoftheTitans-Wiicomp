#define SDL_MAIN_HANDLED
#include <SDL.h>
#include <glad/glad.h>
#include <cstdint>
#include <cstdio>
#include <cmath>
#include "../runtime-overlays/texture_sampler.inc"
int main(){
 SDL_SetMainReady();if(SDL_Init(SDL_INIT_VIDEO))return 2;
 SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION,3);
 SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK,SDL_GL_CONTEXT_PROFILE_CORE);
 auto window=SDL_CreateWindow("Sampler tests",0,0,16,16,SDL_WINDOW_OPENGL|SDL_WINDOW_HIDDEN);
 auto context=window?SDL_GL_CreateContext(window):nullptr;
 if(!context||!gladLoadGLLoader((GLADloadproc)SDL_GL_GetProcAddress))return 3;
 unsigned passed=0,total=0;auto check=[&](bool ok,const char*what){++total;passed+=ok;if(!ok)std::printf("FAIL %s\n",what);};
 for(unsigned unit=0;unit<8;++unit)for(unsigned mode=0;mode<256;++mode){
   NativeApplyTextureSampler(unit,mode);GLint sampler=0,value=0;glGetIntegeri_v(GL_SAMPLER_BINDING,unit,&sampler);
   glGetSamplerParameteriv(sampler,GL_TEXTURE_MAG_FILTER,&value);check(value==((mode&16)?GL_LINEAR:GL_NEAREST),"mag");
   glGetSamplerParameteriv(sampler,GL_TEXTURE_MIN_FILTER,&value);check(value==((mode&128)?GL_LINEAR:GL_NEAREST),"min");
   const GLint wraps[]={GL_CLAMP_TO_EDGE,GL_REPEAT,GL_MIRRORED_REPEAT,GL_CLAMP_TO_EDGE};
   glGetSamplerParameteriv(sampler,GL_TEXTURE_WRAP_S,&value);check(value==wraps[mode&3],"wrapS");
   glGetSamplerParameteriv(sampler,GL_TEXTURE_WRAP_T,&value);check(value==wraps[(mode>>2)&3],"wrapT");
 }
 GLuint texture;glGenTextures(1,&texture);const unsigned char pixels[]={255,0,0,255,0,255,0,255,0,0,255,255,255,255,255,255};
 for(unsigned unit=0;unit<2;++unit){glActiveTexture(GL_TEXTURE0+unit);glBindTexture(GL_TEXTURE_2D,texture);}
 glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,2,2,0,GL_RGBA,GL_UNSIGNED_BYTE,pixels);
 NativeApplyTextureSampler(0,0x95);NativeApplyTextureSampler(1,0x05);
 auto shader=[](GLenum kind,const char*text){GLuint s=glCreateShader(kind);glShaderSource(s,1,&text,nullptr);glCompileShader(s);return s;};
 GLuint v=shader(GL_VERTEX_SHADER,"#version 330 core\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.0-1.0,0,1);}");
 GLuint f=shader(GL_FRAGMENT_SHADER,"#version 330 core\nuniform sampler2D image;out vec4 color;void main(){color=texture(image,vec2(0.5));}");
 GLuint program=glCreateProgram();glAttachShader(program,v);glAttachShader(program,f);glLinkProgram(program);GLint ok=0;glGetProgramiv(program,GL_LINK_STATUS,&ok);check(ok,"link");glUseProgram(program);
 GLuint vao;glGenVertexArrays(1,&vao);glBindVertexArray(vao);glViewport(0,0,16,16);
 for(unsigned unit=0;unit<2;++unit){glUniform1i(glGetUniformLocation(program,"image"),unit);glDrawArrays(GL_TRIANGLES,0,3);unsigned char pixel[4]={};glReadPixels(8,8,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel);
   check(unit?pixel[0]>250&&pixel[1]>250&&pixel[2]>250:std::abs(int(pixel[0])-128)<=1&&std::abs(int(pixel[1])-128)<=1&&std::abs(int(pixel[2])-128)<=1,"shared image independent sampling");
 }
 check(glGetError()==GL_NO_ERROR,"GL error");std::printf("RESULT %u/%u\n",passed,total);
 SDL_GL_DeleteContext(context);SDL_DestroyWindow(window);SDL_Quit();return passed==total?0:1;
}
