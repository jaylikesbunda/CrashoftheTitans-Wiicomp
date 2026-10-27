"""Opt-in (NWII_GL_ERRTRACE=1): attribute GL errors around FlushBatch (stale error on entry, after buffer upload, after the
draw), reporting program, bound draw framebuffer and its completeness status. Read-only."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
BS = chr(92)
p = PORT / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
s = p.read_bytes().decode('utf-8')
nl = '\r\n' if '\r\n' in s else '\n'
t = s.replace('\r\n', '\n')
if 'NWII_GL_ERRTRACE' in t:
    print('already staged')
else:
    a1 = 'static void FlushBatch() {\n  if (s_batch.empty())\n    return;\n'
    n1 = a1 + '''  static const bool s_glerr = std::getenv("NWII_GL_ERRTRACE") != nullptr;
  GLenum glerr_entry = s_glerr ? glGetError() : 0;
'''
    a2 = '  glDrawArrays(GL_TRIANGLES, 0, s_batch.size());\n  NativeEfbProbe("post_draw");'
    n2 = '''  GLenum glerr_buf = s_glerr ? glGetError() : 0;
  glDrawArrays(GL_TRIANGLES, 0, s_batch.size());
  if (s_glerr) {
    static int shown = 0;
    static uint64_t draws = 0, bad_entry = 0, bad_buf = 0, bad_draw = 0;
    ++draws;
    GLenum glerr_draw = glGetError();
    if (glerr_entry) ++bad_entry;
    if (glerr_buf) ++bad_buf;
    if (glerr_draw) ++bad_draw;
    if ((glerr_entry || glerr_buf || glerr_draw) && shown < 40) {
      ++shown;
      GLint prog = 0, drawfbo = 0, vao = 0, arraybuf = 0;
      glGetIntegerv(GL_CURRENT_PROGRAM, &prog);
      glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &drawfbo);
      glGetIntegerv(GL_VERTEX_ARRAY_BINDING, &vao);
      glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &arraybuf);
      GLenum fbstatus = glCheckFramebufferStatus(GL_DRAW_FRAMEBUFFER);
      printf("[GLERR] entry=0x%x buf=0x%x draw=0x%x verts=%zu prog=%d drawfbo=%d fbstatus=0x%x vao=%d arraybuf=%d@@n",
             glerr_entry, glerr_buf, glerr_draw, s_batch.size(), prog, drawfbo, fbstatus, vao, arraybuf);
      fflush(stdout);
    }
    if ((draws % 20000) == 0) {
      printf("[GLERR] summary draws=%llu bad_entry=%llu bad_buf=%llu bad_draw=%llu@@n", (unsigned long long)draws,
             (unsigned long long)bad_entry, (unsigned long long)bad_buf, (unsigned long long)bad_draw);
      fflush(stdout);
    }
  }
  NativeEfbProbe("post_draw");'''.replace('@@n', BS + 'n')
    if a1 not in t or a2 not in t:
        raise SystemExit('renderer_gl FlushBatch context changed')
    t = t.replace(a1, n1, 1).replace(a2, n2, 1)
    p.write_bytes(t.replace('\n', nl).encode('utf-8'))
    print('GL error trace staged')
