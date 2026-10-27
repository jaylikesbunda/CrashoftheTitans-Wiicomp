"""Stage full physical EFB height and top-origin texture copies; no guest changes."""
from pathlib import Path
import re
PORT=Path(__file__).resolve().parents[1]
src=PORT/'build/nwii-full-source/nWiiRuntime/src'
definition='static constexpr int kNativeEfbHeight = 528; // NATIVE_EFB_HEIGHT_V1\n'
for relative in ('core/main.cpp','hle/gx/renderer_gl.cpp'):
    p=src/relative;s=p.read_text()
    if definition not in s:s=definition+s
    if relative=='core/main.cpp':
        s=re.sub(r'\b480\b','kNativeEfbHeight',s)
        s=re.sub(r'\b479\b','(kNativeEfbHeight - 1)',s)
        s=s.replace('"/307200"','"/337920"')
    else:
        start='// NATIVE_EFB_COPY_GEOMETRY_BEGIN\n';end='// NATIVE_EFB_COPY_GEOMETRY_END\n'
        if start in s:
            a=s.index(start);b=s.index(end,a)+len(end)
            # the block template ends with END\n before the class line; eat the separator the template
            # added so remove+reinsert is a fixpoint (otherwise every pass leaves one blank line).
            if s[b:b+1]=='\n':b+=1
            s=s[:a]+s[b:]
        overlay=(PORT/'runtime-overlays/efb_copy.inc').read_text()
        s=s.replace('class RendererGL : public IRenderer {',start+overlay+'\n'+end+'\nclass RendererGL : public IRenderer {')
        old='''            glBindTexture(GL_TEXTURE_2D, tex);
            glCopyTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, g_state.efbSrcX,
                             g_state.efbSrcY, w, h, 0);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);'''
        new='''            NativeCopyEfbTexture(tex,g_state.efbSrcX,g_state.efbSrcY,w,h);'''
        if old in s:s=s.replace(old,new,1)
        elif new not in s and 'NativeCopyEfbTexture(tex,g_state.efbSrcX,g_state.efbSrcY,w,h,' not in s:raise SystemExit('EFB copy context changed')  # also accept the form rewritten by the resolution-scale stage
    if s!=p.read_text():p.write_text(s)
print('Physical EFB 528 rows and top-origin copy geometry staged.')
