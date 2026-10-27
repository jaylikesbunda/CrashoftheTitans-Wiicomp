// Keep completed XFB copies separate from the EFB being drawn or cleared.
// Native RGB copies omit YUV conversion/filtering; this preserves frame order.
struct NativeXfbFrame { GLuint texture=0,fbo=0; int width=0,height=0; };
static std::unordered_map<uint32_t,NativeXfbFrame> s_native_xfb;
static uint64_t s_native_xfb_copies=0;

static void NativeLatchXfb(uint32_t address,int x,int y,int width,int height,int scale=1) {
  if(!address || width<=0 || height<=0 || x<0 || y<0 ||
     x+width>640 || y+height>kNativeEfbHeight) return;
  GLint draw=0,read=0,binding=0;
  glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING,&draw);
  glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING,&read);
  glGetIntegerv(GL_TEXTURE_BINDING_2D,&binding);
  const GLboolean scissor=glIsEnabled(GL_SCISSOR_TEST);
  auto &frame=s_native_xfb[address&0x3fffffff];
  if(!frame.texture)glGenTextures(1,&frame.texture);
  if(!frame.fbo)glGenFramebuffers(1,&frame.fbo);
  glBindTexture(GL_TEXTURE_2D,frame.texture);
  const int output_width=width*scale,output_height=height*scale;
  if(frame.width!=output_width || frame.height!=output_height) {
    glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,output_width,output_height,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
    glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
    frame.width=output_width;frame.height=output_height;
  }
  glBindFramebuffer(GL_READ_FRAMEBUFFER,GLuint(draw));
  glBindFramebuffer(GL_DRAW_FRAMEBUFFER,frame.fbo);
  glFramebufferTexture2D(GL_DRAW_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,frame.texture,0);
  glDisable(GL_SCISSOR_TEST);
  // Both framebuffers use GL bottom-origin rows. GX source coordinates are top-origin.
  glBlitFramebuffer(x*scale,(kNativeEfbHeight-y-height)*scale,(x+width)*scale,(kNativeEfbHeight-y)*scale,
                    0,0,output_width,output_height,GL_COLOR_BUFFER_BIT,GL_NEAREST);
  glBindFramebuffer(GL_DRAW_FRAMEBUFFER,GLuint(draw));
  glBindFramebuffer(GL_READ_FRAMEBUFFER,GLuint(read));
  glBindTexture(GL_TEXTURE_2D,GLuint(binding));
  if(scissor)glEnable(GL_SCISSOR_TEST);
  ++s_native_xfb_copies;
}

bool NativeGetXfbFrame(uint32_t address,GLuint *fbo,int *width,int *height) {
  auto it=s_native_xfb.find(address&0x3fffffff);
  if(it==s_native_xfb.end())return false;
  *fbo=it->second.fbo;*width=it->second.width;*height=it->second.height;
  return true;
}
uint64_t NativeXfbCopyCount(){return s_native_xfb_copies;}
