// Bounded, opt-in read-only observation of the actual GL draw framebuffer.
static void NativeEfbProbe(const char *phase) {
  static const char *prefix = std::getenv("NWII_EFB_PROBE");
  static unsigned count = 0;
  static std::string batch;
  std::string output_prefix = prefix ? prefix : "";
  unsigned mode = 0;
  // Optional targeted collection: unlit effects and shadow passes must not
  // spend the budget before a real illuminated actor draw arrives.
  static const bool lit_only = std::getenv("NWII_EFB_PROBE_LIT") != nullptr;
  // Optional file gate lets a live run reach the level before spending its
  // bounded probe budget. This observes host state and never changes the guest.
  static const char *arm = std::getenv("NWII_EFB_PROBE_ARM_FILE");
  if (arm) {
    FILE *file = std::fopen(arm, "rb");
    if (!file) return;
    // A host-only batch token permits another bounded capture in the same run.
    // Mode 0: all draws, 1: lit draws, 2: unlit draws. No GPU/guest state changes.
    char token[49]={};
    if (std::fscanf(file,"%48s %u",token,&mode)>=1) {
      for(char &c:token) if(c && !std::isalnum(static_cast<unsigned char>(c)) && c!='_' && c!='-') c='_';
      if (batch != token) {batch=token;count=0;}
      output_prefix += "_batch_"+batch;
    }
    std::fclose(file);
  }
  const bool lit = ((g_state.xf[14] | g_state.xf[15] | g_state.xf[16] | g_state.xf[17]) & 2u)!=0;
  if ((lit_only || mode==1 || mode==2) &&
      (std::strcmp(phase,"post_draw")!=0 || ((lit_only || mode==1) && !lit) || (mode==2 && lit))) return;
  static const unsigned probe_max = [](){const char *e=std::getenv("NWII_EFB_PROBE_MAX");return e?unsigned(std::strtoul(e,nullptr,10)):100u;}(); if (!prefix || count >= probe_max) return; static const bool persp_only = std::getenv("NWII_EFB_PROBE_PERSP") != nullptr; if (persp_only && g_state.projType == 1) return;
  static const double after = [](){const char *e=std::getenv("NWII_EFB_PROBE_AFTER");return e?std::strtod(e,nullptr):0.0;}();
  static const auto first = std::chrono::steady_clock::now();
  if (after>0 && std::chrono::duration<double>(std::chrono::steady_clock::now()-first).count()<after) return;
  const unsigned sample = ++count;
  GLint draw = 0, read = 0, prog = 0, viewport[4] = {};
  glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw);
  glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read);
  glGetIntegerv(GL_CURRENT_PROGRAM, &prog);
  glGetIntegerv(GL_VIEWPORT, viewport);
  glBindFramebuffer(GL_READ_FRAMEBUFFER, draw);
  std::vector<unsigned char> pixels(640 * kNativeEfbHeight * 4);
  glReadPixels(0, 0, 640, kNativeEfbHeight, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data());
  glBindFramebuffer(GL_READ_FRAMEBUFFER, read);
  size_t nonblack = 0;
  for (size_t i = 0; i < pixels.size(); i += 4)
    nonblack += (pixels[i] | pixels[i+1] | pixels[i+2]) != 0;
  GLint linked = 0;
  if (prog) glGetProgramiv(prog, GL_LINK_STATUS, &linked);
  GLint depth_func = 0, scissor[4] = {};
  GLboolean depth_write = 0, color_write[4] = {};
  GLdouble depth_clear = 0;
  glGetIntegerv(GL_DEPTH_FUNC, &depth_func);
  glGetBooleanv(GL_DEPTH_WRITEMASK, &depth_write);
  glGetBooleanv(GL_COLOR_WRITEMASK, color_write);
  glGetDoublev(GL_DEPTH_CLEAR_VALUE, &depth_clear);
  glGetIntegerv(GL_SCISSOR_BOX, scissor);
  printf("[EFB masks] bpZ=%06x bpBlend=%06x bpClearZ=%06x func=%x zwrite=%u rgba=%u,%u,%u,%u clearZ=%g scissor=%u/%d,%d,%d,%d\n",
         g_state.bp[0x40],g_state.bp[0x41],g_state.bp[0x51],depth_func,
         unsigned(depth_write),unsigned(color_write[0]),unsigned(color_write[1]),
         unsigned(color_write[2]),unsigned(color_write[3]),depth_clear,
         unsigned(glIsEnabled(GL_SCISSOR_TEST)),scissor[0],scissor[1],scissor[2],scissor[3]);
  if (linked) {
    if(std::strcmp(phase,"post_draw")==0)NativeDrawArchive(output_prefix.c_str(),sample,GLuint(prog));
    GLfloat regular[8]={},konst[8]={};
    for (int i=0;i<2;++i) {
      const std::string c="uTevColor["+std::to_string(i)+"]";
      const std::string k="uTevKColor["+std::to_string(i)+"]";
      GLint cl=glGetUniformLocation(prog,c.c_str()),kl=glGetUniformLocation(prog,k.c_str());
      if(cl>=0)glGetUniformfv(prog,cl,regular+i*4);
      if(kl>=0)glGetUniformfv(prog,kl,konst+i*4);
    }
    printf("[EFB tev] gen=%06x C0=%06x C1=%06x C2=%06x C3=%06x F3=%06x KSEL=%06x,%06x color0=%g,%g,%g,%g color1=%g,%g,%g,%g konst0=%g,%g,%g,%g konst1=%g,%g,%g,%g\n",
           g_state.bp[0],g_state.bp[0xc0],g_state.bp[0xc1],g_state.bp[0xc2],g_state.bp[0xc3],g_state.bp[0xf3],g_state.bp[0xf6],g_state.bp[0xf7],
           regular[0],regular[1],regular[2],regular[3],regular[4],regular[5],regular[6],regular[7],
           konst[0],konst[1],konst[2],konst[3],konst[4],konst[5],konst[6],konst[7]);
    GLint loc=glGetUniformLocation(prog,"mvp");
    GLfloat matrix[16] = {};
    if(loc>=0)glGetUniformfv(prog,loc,matrix);
    printf("[EFB state] proj=%u/%d mvp=%g,%g,%g,%g,%g,%g xfMat=%08x,%08x xfCtrl=%08x,%08x,%08x,%08x bpBlend=%06x\n",
           g_state.projType,int(g_state.projSet),matrix[0],matrix[5],matrix[10],matrix[14],matrix[11],matrix[15],
           g_state.xf[12],g_state.xf[13],g_state.xf[14],g_state.xf[15],g_state.xf[16],g_state.xf[17],g_state.bp[0x41]);
    if(!s_batch.empty()) {
      const auto &v=s_batch.front();
      float clip[4]={};
      for(int i=0;i<4;++i)clip[i]=matrix[i]*v.x+matrix[i+4]*v.y+matrix[i+8]*v.z+matrix[i+12];
      printf("[EFB vertex] xyz=%g,%g,%g rgba=%g,%g,%g,%g clip=%g,%g,%g,%g\n",
             v.x,v.y,v.z,v.r,v.g,v.b,v.a,clip[0],clip[1],clip[2],clip[3]);
    }
    if(std::strcmp(phase,"post_draw")==0 && sample<=50) {
      GLint sampler=glGetUniformLocation(prog,"uTex0"),unit=0,active=0,texture=0,width=0,height=0;
      if(sampler>=0)glGetUniformiv(prog,sampler,&unit);
      glGetIntegerv(GL_ACTIVE_TEXTURE,&active);
      glActiveTexture(GL_TEXTURE0+unit);
      glGetIntegerv(GL_TEXTURE_BINDING_2D,&texture);
      glGetTexLevelParameteriv(GL_TEXTURE_2D,0,GL_TEXTURE_WIDTH,&width);
      glGetTexLevelParameteriv(GL_TEXTURE_2D,0,GL_TEXTURE_HEIGHT,&height);
      printf("[EFB texture] sampler=%d unit=%d texture=%d size=%dx%d guest=%x/%ux%u texgens=%u tev=%u\n",
             sampler,unit,texture,width,height,g_state.texStages[0].base_addr,
             g_state.texStages[0].width,g_state.texStages[0].height,g_state.numTexGens,g_state.numTevStages);
      if(texture && width>0 && height>0 && width<=1024 && height<=1024) {
        std::vector<unsigned char> texels(size_t(width)*height*4);
        glGetTexImage(GL_TEXTURE_2D,0,GL_RGBA,GL_UNSIGNED_BYTE,texels.data());
        const std::string path=output_prefix+"_tex0_"+std::to_string(sample)+".ppm";
        if(FILE *file=std::fopen(path.c_str(),"wb")) {
          std::fprintf(file,"P6\n%d %d\n255\n",width,height);
          for(size_t i=0;i<texels.size();i+=4)std::fwrite(&texels[i],1,3,file);
          std::fclose(file);
        }
      }
      glActiveTexture(active);
    }
  }
  printf("[EFB probe] n=%u phase=%s fbo=%d prog=%d linked=%d viewport=%d,%d,%d,%d nonblack=%zu depth=%d blend=%d cull=%d glErr=0x%x\n",
         sample, phase, draw, prog, linked, viewport[0], viewport[1],
         viewport[2], viewport[3], nonblack, glIsEnabled(GL_DEPTH_TEST),
         glIsEnabled(GL_BLEND), glIsEnabled(GL_CULL_FACE), glGetError());
  if (nonblack) {
    const std::string path = output_prefix + "_" + phase + "_" +
                             std::to_string(sample) + ".ppm";
    if (FILE *file = std::fopen(path.c_str(), "wb")) {
      std::fprintf(file, "P6\n640 %d\n255\n",kNativeEfbHeight);
      for (int y = kNativeEfbHeight - 1; y >= 0; --y)
        for (int x = 0; x < 640; ++x)
          std::fwrite(&pixels[(size_t(y)*640+x)*4], 1, 3, file);
      std::fclose(file);
    }
  }
}
