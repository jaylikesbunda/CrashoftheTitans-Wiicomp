#pragma once
#include <glad/glad.h>
#include <algorithm>
#include <cstdio>

// Shared host-only state. All guest GX addresses, rectangles and texture sizes
// remain in native coordinates; scale is chosen once after GL initialization.
inline int& NativeResolutionScaleStorage() { static int scale=1;return scale; }
inline int NativeResolutionScale() { return NativeResolutionScaleStorage(); }
inline int NativeEfbWidth() { return 640*NativeResolutionScale(); }
inline int NativeEfbHeight() { return 528*NativeResolutionScale(); }
inline void NativeSetResolutionScale(int requested) {
  GLint texture=0,renderbuffer=0;
  glGetIntegerv(GL_MAX_TEXTURE_SIZE,&texture);
  glGetIntegerv(GL_MAX_RENDERBUFFER_SIZE,&renderbuffer);
  const int supported=std::max(1,std::min({4,texture/640,renderbuffer/640}));
  NativeResolutionScaleStorage()=std::clamp(requested,1,supported);
  std::printf("[Resolution] requested=%d scale=%d EFB=%dx%d\n",requested,
      NativeResolutionScale(),NativeEfbWidth(),NativeEfbHeight());
}

// Diagnostic reads retain native-sized arrays even when the EFB is larger.
// Game presentation and screenshots read full-sized completed XFBs directly.
inline void NativeReadEfbPixels(int x,int y,int width,int height,GLenum format,GLenum type,void* pixels) {
  const int scale=NativeResolutionScale();
  if(scale==1) {glReadPixels(x,y,width,height,format,type,pixels);return;}
  GLint draw=0,read=0,binding=0;
  glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING,&draw);
  glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING,&read);
  glGetIntegerv(GL_TEXTURE_BINDING_2D,&binding);
  const GLboolean scissor=glIsEnabled(GL_SCISSOR_TEST);
  static GLuint fbo=0,texture=0;static int old_width=0,old_height=0;
  if(!fbo) {glGenFramebuffers(1,&fbo);glGenTextures(1,&texture);}
  glBindTexture(GL_TEXTURE_2D,texture);
  if(width!=old_width || height!=old_height) {
    glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,width,height,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
    old_width=width;old_height=height;
  }
  glBindFramebuffer(GL_DRAW_FRAMEBUFFER,fbo);
  glFramebufferTexture2D(GL_DRAW_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,texture,0);
  glDisable(GL_SCISSOR_TEST);
  glBlitFramebuffer(x*scale,y*scale,(x+width)*scale,(y+height)*scale,
                    0,0,width,height,GL_COLOR_BUFFER_BIT,GL_NEAREST);
  glBindFramebuffer(GL_READ_FRAMEBUFFER,fbo);
  glReadPixels(0,0,width,height,format,type,pixels);
  glBindFramebuffer(GL_DRAW_FRAMEBUFFER,GLuint(draw));
  glBindFramebuffer(GL_READ_FRAMEBUFFER,GLuint(read));
  glBindTexture(GL_TEXTURE_2D,GLuint(binding));
  if(scissor)glEnable(GL_SCISSOR_TEST);
}
