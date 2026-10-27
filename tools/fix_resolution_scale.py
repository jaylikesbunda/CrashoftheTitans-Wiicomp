"""Scale host EFB/XFB surfaces while retaining guest texture/copy coordinates."""
from pathlib import Path
import shutil
PORT=Path(__file__).resolve().parents[1]
stage=PORT/'build/nwii-full-source/nWiiRuntime'
header=stage/'include/runtime/native_resolution.h'
overlay=PORT/'runtime-overlays/resolution_scale.h'
if not header.exists() or header.read_bytes()!=overlay.read_bytes():
    shutil.copyfile(overlay,header)
def edit(path,fn):
    s=path.read_text();t=fn(s)
    if t!=s:path.write_text(t)
def include(s):
    needle='#include "runtime/native_resolution.h"\n'
    if needle not in s:
        at=s.index('#include ');s=s[:at]+needle+s[at:]
    return s
def config_header(s):
    if 'int resolution_scale = 1;' not in s:
        s=s.replace('    int window_width = 640;','    int resolution_scale = 1;\n    int window_width = 640;',1)
    return s
edit(stage/'include/runtime/config.h',config_header)
def config_source(s):
    marker='        // NATIVE_RESOLUTION_CONFIG_V1\n'
    if marker not in s:
        needle='        auto w = tbl["graphics"]["window_width"].value<int>();'
        assert s.count(needle)==1
        s=s.replace(needle,marker+'        auto scale = tbl["graphics"]["resolution_scale"].value<int>();\n        if (scale) resolution_scale = *scale;\n\n'+needle,1)
    return s
edit(stage/'src/core/config.cpp',config_source)
def main(s):
    s=include(s)
    marker='    // NATIVE_RESOLUTION_INIT_V1\n'
    if marker not in s:
        needle='    glGenFramebuffers(1, &efb_fbo);'
        assert s.count(needle)==1
        init=marker+'    int requested_scale=nwii::runtime::Config::get().resolution_scale;\n    if(const char* scale_env=std::getenv("NWII_RENDER_SCALE")) requested_scale=std::atoi(scale_env);\n    NativeSetResolutionScale(requested_scale);\n'
        s=s.replace(needle,init+needle,1)
    s=s.replace('glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 640, kNativeEfbHeight,',
                'glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, NativeEfbWidth(), NativeEfbHeight(),')
    s=s.replace('glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, 640, kNativeEfbHeight);',
                'glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, NativeEfbWidth(), NativeEfbHeight());')
    s=s.replace('glViewport(0, 0, 640, kNativeEfbHeight);','glViewport(0, 0, NativeEfbWidth(), NativeEfbHeight());')
    s=s.replace('glReadPixels(0, 0, 640, kNativeEfbHeight, GL_RGBA, GL_UNSIGNED_BYTE, p.data());',
                'NativeReadEfbPixels(0, 0, 640, kNativeEfbHeight, GL_RGBA, GL_UNSIGNED_BYTE, p.data());')
    # Full-resolution EFB screenshots, separate from native-coordinate stats.
    start=s.index('          if (shot_now) {');end=s.index('// NATIVE_XFB_SCREENSHOT',start)
    shot=s[start:end]
    shot=shot.replace('640 * kNativeEfbHeight','NativeEfbWidth() * NativeEfbHeight()')
    shot=shot.replace('640, kNativeEfbHeight','NativeEfbWidth(), NativeEfbHeight()')
    shot=shot.replace('kNativeEfbHeight','NativeEfbHeight()').replace('640 * 4','NativeEfbWidth() * 4')
    s=s[:start]+shot+s[end:]
    return s
edit(stage/'src/core/main.cpp',main)
def renderer(s):
    s=include(s)
    # Refresh canonical overlay blocks, including optional scale argument.
    for begin,end,file in [('// NATIVE_EFB_COPY_GEOMETRY_BEGIN','// NATIVE_EFB_COPY_GEOMETRY_END','efb_copy.inc'),
                           ('// NATIVE_XFB_PRESENT_BEGIN','// NATIVE_XFB_PRESENT_END','xfb_present.inc')]:
        a=s.index(begin);b=s.index(end,a)
        s=s[:a]+begin+'\n'+(PORT/'runtime-overlays'/file).read_text()+'\n'+s[b:]
    s=s.replace('NativeApplyViewport(g_state.xf);','NativeApplyViewport(g_state.xf,NativeResolutionScale());')
    s=s.replace('NativeCorrectPixelCenter(p, float(viewport.width), float(viewport.height));',
                'NativeCorrectPixelCenter(p, float(viewport.width*NativeResolutionScale()), float(viewport.height*NativeResolutionScale()));')
    s=s.replace('glReadPixels(0, 0, 640, kNativeEfbHeight, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data());',
                'NativeReadEfbPixels(0, 0, 640, kNativeEfbHeight, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data());')
    s=s.replace('NativeCopyEfbTexture(tex,g_state.efbSrcX,g_state.efbSrcY,w,h);',
                'NativeCopyEfbTexture(tex,g_state.efbSrcX,g_state.efbSrcY,w,h,NativeResolutionScale());')
    # Call formatting is stable in the canonical copy hook.
    s=s.replace('NativeLatchXfb(g_state.efbCopyDest,g_state.efbSrcX,g_state.efbSrcY,g_state.efbW,g_state.efbH);',
                'NativeLatchXfb(g_state.efbCopyDest,g_state.efbSrcX,g_state.efbSrcY,g_state.efbW,g_state.efbH,NativeResolutionScale());')
    old='glScissor(g_state.efbSrcX,kNativeEfbHeight-g_state.efbSrcY-g_state.efbH,g_state.efbW,g_state.efbH);'
    new='glScissor(g_state.efbSrcX*NativeResolutionScale(),(kNativeEfbHeight-g_state.efbSrcY-g_state.efbH)*NativeResolutionScale(),g_state.efbW*NativeResolutionScale(),g_state.efbH*NativeResolutionScale());'
    s=s.replace(old,new)
    return s
edit(stage/'src/hle/gx/renderer_gl.cpp',renderer)
print('Host EFB/XFB resolution scaling staged; game EFB texture copies remain native-sized.')
