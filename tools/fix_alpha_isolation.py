"""Keep EFB alpha out of anything the player sees.

The console EFB is normally RGB8: alpha written by draws is not stored, copies to
RGB formats read alpha as 1, and the XFB has no alpha at all. Here the EFB is
RGBA8, so TEV alpha from alpha-tested/blended objects (leaves, fruit, characters)
leaked into
  * EFB-to-texture copies (later sampled with that alpha), and
  * the window back buffer via the XFB blit, where a surface with alpha bits can
    be composited translucently by the desktop.
Present the XFB with alpha writes masked (back buffer alpha stays 1 from the
clear), and force alpha to 1 in EFB texture copies when the guest pixel format
(BP 0x43 low bits) is not RGBA6.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
src = port / 'build/nwii-full-source/nWiiRuntime/src'
marker = 'NATIVE_ALPHA_ISOLATION_V1'

p = src / 'core/main.cpp'
t = p.read_text()
if marker not in t:
    old = '          if(have_present) {\n            glBindFramebuffer(GL_READ_FRAMEBUFFER,present_fbo);'
    assert t.count(old) == 1
    t = t.replace(old, '          glColorMask(GL_TRUE,GL_TRUE,GL_TRUE,GL_FALSE); // NATIVE_ALPHA_ISOLATION_V1\n' + old)
    p.write_text(t)

p = src / 'hle/gx/renderer_gl.cpp'
t = p.read_text()
if marker not in t:
    a = t.index('static void NativeCopyEfbTexture')
    b = t.index('NATIVE_EFB_COPY_GEOMETRY_END')
    seg = t[a:b]
    old = '  glBindFramebuffer(GL_DRAW_FRAMEBUFFER,GLuint(draw));\n  glBindFramebuffer(GL_READ_FRAMEBUFFER,GLuint(read));'
    assert seg.count(old) == 1
    new = '''  if ((g_state.bp[0x43] & 7) != 1) { // NATIVE_ALPHA_ISOLATION_V1: no EFB alpha -> copies read alpha 1
    GLboolean saved_mask[4];
    glGetBooleanv(GL_COLOR_WRITEMASK, saved_mask);
    glColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_TRUE);
    glClearColor(0.f, 0.f, 0.f, 1.f);
    glClear(GL_COLOR_BUFFER_BIT);
    glColorMask(saved_mask[0], saved_mask[1], saved_mask[2], saved_mask[3]);
  }
''' + old
    seg = seg.replace(old, new)
    t = t[:a] + seg + t[b:]
    p.write_text(t)
print('EFB alpha isolated from presentation and texture copies.')
