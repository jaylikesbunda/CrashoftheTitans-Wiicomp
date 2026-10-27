"""Restore GX non-AA raster sample position, without movie-specific paths."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
s=p.read_text(); helper=(PORT/'runtime-overlays/pixel_center.inc').read_text()
marker='// NATIVE_PIXEL_CENTER_V1'
if marker not in s:
    needle='static void ApplyProjection('
    assert s.count(needle)==1
    s=s.replace(needle,helper+'\n'+needle,1)
needle='  std::memcpy(s_proj, p, sizeof(s_proj));'
if 'NativeCorrectPixelCenter(p,' not in s:
    assert s.count(needle)==1
    s=s.replace(needle,'  NativeCorrectPixelCenter(p, 640.0f, float(kNativeEfbHeight));\n'+needle,1)
if s!=p.read_text():p.write_text(s)
print('GX 7/12 pixel centre applied to native 640x528 EFB projection.')
