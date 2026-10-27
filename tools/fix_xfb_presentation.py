"""Stage copy-then-clear ordering and presentation of VI-selected completed XFBs."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
src=PORT/'build/nwii-full-source/nWiiRuntime/src'
p=src/'hle/gx/renderer_gl.cpp';s=p.read_text()
start='// NATIVE_XFB_PRESENT_BEGIN\n';end='// NATIVE_XFB_PRESENT_END\n'
if start in s:
    a=s.index(start);b=s.index(end,a)+len(end)
    # the block template ends with END\n before the class line; eat the separator the template added
    # so remove+reinsert is a fixpoint (otherwise every pass leaves one blank line).
    if s[b:b+1]=='\n':b+=1
    s=s[:a]+s[b:]
s=s.replace('class RendererGL : public IRenderer {',start+(PORT/'runtime-overlays/xfb_present.inc').read_text()+'\n'+end+'\nclass RendererGL : public IRenderer {')
old='''    if (g_state.pe_clear_pending) {
      g_state.pe_clear_pending = false;
      unsigned char cc[4];
      ::GX_GetClearColor(cc);
      glClearColor(cc[0] / 255.f, cc[1] / 255.f, cc[2] / 255.f, cc[3] / 255.f);
      NativeEfbProbe("pre_clear");
      NativeClearEfb();
    }'''
if old in s:s=s.replace(old,'    // NATIVE_COPY_CLEAR_ORDER: never apply a future copy clear before this stream.',1)
elif '// NATIVE_COPY_CLEAR_ORDER' not in s:raise SystemExit('Early-clear context changed')
old='''            if (bp_value & 0x800) {
              unsigned char cc[4];
              ::GX_GetClearColor(cc);
              glClearColor(cc[0] / 255.f, cc[1] / 255.f, cc[2] / 255.f,
                           cc[3] / 255.f);
              NativeEfbProbe("pre_clear");
      NativeClearEfb();
              g_state.pe_clear_pending = false;
            }'''
if old in s:s=s.replace(old,'',1)
needle='''      } else if (cmd.type == GXCommandType::DrawPrimitive) {'''
hook='''        // NATIVE_XFB_COPY_ORDER: the preceding batch was flushed above.
        if (cmd.reg==0x52) {
          if(bp_value&0x4000)
            NativeLatchXfb(g_state.efbCopyDest,g_state.efbSrcX,g_state.efbSrcY,g_state.efbW,g_state.efbH);
          if(bp_value&0x800) {
            unsigned char cc[4];::GX_GetClearColor(cc);
            glClearColor(cc[0]/255.f,cc[1]/255.f,cc[2]/255.f,cc[3]/255.f);
            // Copy clears affect the copy rectangle, not the current draw scissor.
            GLint box[4];glGetIntegerv(GL_SCISSOR_BOX,box);
            const GLboolean scissor=glIsEnabled(GL_SCISSOR_TEST);
            glEnable(GL_SCISSOR_TEST);
            glScissor(g_state.efbSrcX,kNativeEfbHeight-g_state.efbSrcY-g_state.efbH,g_state.efbW,g_state.efbH);
            NativeEfbProbe("pre_clear");NativeClearEfb();
            glScissor(box[0],box[1],box[2],box[3]);
            if(!scissor)glDisable(GL_SCISSOR_TEST);
          }
          g_state.pe_clear_pending=false;
        }
'''
if '// NATIVE_XFB_COPY_ORDER' not in s:
    if s.count(needle)!=1:raise SystemExit('Draw transition changed')
    s=s.replace(needle,hook+needle)
if s!=p.read_text():p.write_text(s)
p=src/'core/main.cpp';s=p.read_text()
declaration='''namespace nwii::runtime::gx {
bool NativeGetXfbFrame(uint32_t,GLuint*,int*,int*);
uint64_t NativeXfbCopyCount();
} // NATIVE_XFB_MAIN_DECL
'''
# Insert after GL declarations/includes, not before GLuint is declared.
needle='      extern void ProcessGXFifo();'
if '// NATIVE_XFB_MAIN_DECL' not in s:
    anchor='int main('
    pos=s.find(anchor)
    if pos<0:raise SystemExit('main declaration absent')
    s=s[:pos]+declaration+'\n'+s[pos:]
old='''        glClearColor(0, 0, 0, 1);
        glClear(GL_COLOR_BUFFER_BIT);

        glBindFramebuffer(GL_READ_FRAMEBUFFER, efb_fbo);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0);
        glBlitFramebuffer(0, 0, 640, kNativeEfbHeight, 0, 0, draw_w, draw_h,
                          GL_COLOR_BUFFER_BIT, GL_LINEAR);'''
new='''        // NATIVE_XFB_MAIN_PRESENT: retain the VI-selected completed copy while EFB changes.
        const GLboolean present_scissor=glIsEnabled(GL_SCISSOR_TEST);
        GLboolean present_mask[4];glGetBooleanv(GL_COLOR_WRITEMASK,present_mask);
        glDisable(GL_SCISSOR_TEST);glColorMask(GL_TRUE,GL_TRUE,GL_TRUE,GL_TRUE);
        glClearColor(0,0,0,1);glClear(GL_COLOR_BUFFER_BIT);
        GLuint present_fbo=0;int present_w=0,present_h=0;
        if(nwii::runtime::gx::NativeGetXfbFrame(xfb_addr,&present_fbo,&present_w,&present_h)) {
          glBindFramebuffer(GL_READ_FRAMEBUFFER,present_fbo);
          glBindFramebuffer(GL_DRAW_FRAMEBUFFER,0);
          glBlitFramebuffer(0,0,present_w,present_h,0,0,draw_w,draw_h,GL_COLOR_BUFFER_BIT,GL_LINEAR);
        }
        glColorMask(present_mask[0],present_mask[1],present_mask[2],present_mask[3]);
        if(present_scissor)glEnable(GL_SCISSOR_TEST);'''
if old in s:s=s.replace(old,new,1)
elif '// NATIVE_XFB_MAIN_PRESENT' not in s:raise SystemExit('Main presentation changed')
# Screenshots remain EFB observations; archive names and prior comparisons retain their meaning.
if '// NATIVE_XFB_SCREENSHOT' not in s:
    anchor='''              SDL_FreeSurface(s);
            }
            glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);'''
    addition='''              SDL_FreeSurface(s);
            }
            // NATIVE_XFB_SCREENSHOT: capture the completed surface shown to the user too.
            if(present_fbo && present_w>0 && present_h>0) {
              std::vector<unsigned char> complete(size_t(present_w)*present_h*4),top(complete.size());
              glBindFramebuffer(GL_READ_FRAMEBUFFER,present_fbo);
              glReadPixels(0,0,present_w,present_h,GL_RGBA,GL_UNSIGNED_BYTE,complete.data());
              for(int y=0;y<present_h;++y)
                std::memcpy(&top[size_t(y)*present_w*4],&complete[size_t(present_h-1-y)*present_w*4],size_t(present_w)*4);
              SDL_Surface *xfb_surface=SDL_CreateRGBSurfaceFrom(top.data(),present_w,present_h,32,present_w*4,
                  0x000000FF,0x0000FF00,0x00FF0000,0xFF000000);
              if(xfb_surface) {
                const std::string xfb_path=std::string(shot_pfx)+"_xfb_"+std::to_string(shot_frame)+".bmp";
                SDL_SaveBMP(xfb_surface,xfb_path.c_str());SDL_FreeSurface(xfb_surface);
              }
            }
            glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);'''
    if s.count(anchor)!=1:raise SystemExit('Screenshot context changed')
    s=s.replace(anchor,addition)
if '" xfb_copies="' not in s:
    s=s.replace('<< g_stat_frames << " draws=" << g_stat_draws','<< g_stat_frames << " xfb_copies=" << nwii::runtime::gx::NativeXfbCopyCount() << " draws=" << g_stat_draws',1)
if '// NATIVE_OFFSCREEN_CAPTURE' not in s:
    anchor='''    uint32_t window_flags = SDL_WINDOW_SHOWN | SDL_WINDOW_RESIZABLE | SDL_WINDOW_ALLOW_HIGHDPI;'''
    replacement='''    // NATIVE_OFFSCREEN_CAPTURE: rendering/cadence continue without desktop swaps.
    const bool offscreen=std::getenv("NWII_OFFSCREEN")!=nullptr;
    uint32_t window_flags = (offscreen?SDL_WINDOW_HIDDEN:SDL_WINDOW_SHOWN) | SDL_WINDOW_RESIZABLE | SDL_WINDOW_ALLOW_HIGHDPI;'''
    if s.count(anchor)!=1:raise SystemExit('Window flags changed')
    s=s.replace(anchor,replacement,1)
    # Keep visible-mode behavior for comparison; hidden tests never call SwapBuffers.
    s=s.replace('      SDL_GL_SetSwapInterval(1);','      SDL_GL_SetSwapInterval(offscreen?0:1);',1)
    s=s.replace('        SDL_GL_SwapWindow(window);','        if(std::getenv("NWII_OFFSCREEN")==nullptr)SDL_GL_SwapWindow(window);',1)
if '// NATIVE_OFFSCREEN_FBO_ONLY' not in s:
    a=s.index('        // NATIVE_XFB_MAIN_PRESENT:')
    b=s.index('\n\n        if (const char *shot_pfx',a)
    s=s[:a]+'''        // NATIVE_XFB_MAIN_PRESENT: retain the VI-selected completed copy while EFB changes.
        GLuint present_fbo=0;int present_w=0,present_h=0;
        const bool have_present=nwii::runtime::gx::NativeGetXfbFrame(xfb_addr,&present_fbo,&present_w,&present_h);
        // NATIVE_OFFSCREEN_FBO_ONLY: never submit work to a hidden desktop backbuffer.
        static const bool present_offscreen=std::getenv("NWII_OFFSCREEN")!=nullptr;
        if(!present_offscreen) {
          const GLboolean present_scissor=glIsEnabled(GL_SCISSOR_TEST);
          GLboolean present_mask[4];glGetBooleanv(GL_COLOR_WRITEMASK,present_mask);
          glDisable(GL_SCISSOR_TEST);glColorMask(GL_TRUE,GL_TRUE,GL_TRUE,GL_TRUE);
          glClearColor(0,0,0,1);glClear(GL_COLOR_BUFFER_BIT);
          if(have_present) {
            glBindFramebuffer(GL_READ_FRAMEBUFFER,present_fbo);
            glBindFramebuffer(GL_DRAW_FRAMEBUFFER,0);
            glBlitFramebuffer(0,0,present_w,present_h,0,0,draw_w,draw_h,GL_COLOR_BUFFER_BIT,GL_LINEAR);
          }
          glColorMask(present_mask[0],present_mask[1],present_mask[2],present_mask[3]);
          if(present_scissor)glEnable(GL_SCISSOR_TEST);
        }'''+s[b:]
if s!=p.read_text():p.write_text(s)
print('Chronological copy clears and completed XFB presentation staged.')
