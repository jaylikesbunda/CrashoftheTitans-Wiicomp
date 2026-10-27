"""Apply ordinary XF viewport/depth ranges; retain defaults before initialization."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
p = PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
s = p.read_text()
marker = '// NATIVE_GX_VIEWPORT_V1'
helper = (PORT/'runtime-overlays/viewport.inc').read_text()
if marker in s:
    start = s.index(marker)
    end = s.index('static void ApplyProjection(', start)
    s = s[:start] + helper + '\n' + s[end:]
else:
    s = s.replace('static void ApplyProjection(', helper+'\nstatic void ApplyProjection(', 1)
needle = 'static void ApplyProjection(GLint uProj) {\n'
if '  const auto viewport=NativeApplyViewport(' not in s:
    assert s.count(needle) == 1
    s = s.replace(needle, needle+'  const auto viewport=NativeApplyViewport(g_state.xf);\n', 1)
s = s.replace('NativeCorrectPixelCenter(p, 640.0f, float(kNativeEfbHeight));',
              'NativeCorrectPixelCenter(p, float(viewport.width), float(viewport.height));')
if s != p.read_text():
    p.write_text(s)
print('XF ordinary viewport, depth range and viewport-relative pixel centre staged.')
