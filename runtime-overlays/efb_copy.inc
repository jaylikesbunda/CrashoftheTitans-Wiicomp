// The GX copy rectangle is top-origin; decoded guest textures also have row
// zero at their top. Reverse the source Y endpoints when transferring to GL.
// Bind the actual draw EFB explicitly, preserving unrelated texture/FBO state.
static void NativeCopyEfbTexture(GLuint texture, int x, int y, int width, int height,int scale=1) {
  GLint draw=0,read=0,binding=0;
  glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING,&draw);
  glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING,&read);
  glGetIntegerv(GL_TEXTURE_BINDING_2D,&binding);
  const GLboolean scissor=glIsEnabled(GL_SCISSOR_TEST);
  static GLuint copy_fbo=0;
  if(!copy_fbo)glGenFramebuffers(1,&copy_fbo);
  glBindTexture(GL_TEXTURE_2D,texture);
  glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,width,height,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
  glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);
  glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
  glBindFramebuffer(GL_READ_FRAMEBUFFER,GLuint(draw));
  glBindFramebuffer(GL_DRAW_FRAMEBUFFER,copy_fbo);
  glFramebufferTexture2D(GL_DRAW_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,texture,0);
  glDisable(GL_SCISSOR_TEST);
  glBlitFramebuffer(x*scale,(kNativeEfbHeight-y)*scale,(x+width)*scale,(kNativeEfbHeight-y-height)*scale,
                    0,0,width,height,GL_COLOR_BUFFER_BIT,GL_NEAREST);
  glBindFramebuffer(GL_DRAW_FRAMEBUFFER,GLuint(draw));
  glBindFramebuffer(GL_READ_FRAMEBUFFER,GLuint(read));
  glBindTexture(GL_TEXTURE_2D,GLuint(binding));
  if(scissor)glEnable(GL_SCISSOR_TEST);
}
