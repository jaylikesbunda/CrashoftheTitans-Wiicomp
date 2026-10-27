"""Do not block the GX thread on glFinish at every PE finish and GPU token.

NativeGpuCompletion called glFinish() for each BP finish (0x45) and each token
(0x47/0x48). A live profile of the running game in a heavy cutscene showed about
61% of the graphics thread's samples in OS wait functions and only ~5% in the
GL driver; the game issues roughly 200 finishes/tokens per second, and each
glFinish serialised the CPU thread and the GPU.

The signals tell the guest that the GPU has consumed its command stream. The
parser has already copied all vertex data, texture uploads copy guest memory at
use, XFB copies stay on the GPU side and pixel read-backs synchronise
themselves, so waiting for GL completion is not required for guest-visible
correctness. glFlush submits the work without waiting. NWII_GPU_FINISH=1
restores the old blocking behaviour.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
path = port / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
text = path.read_text()
marker = 'NATIVE_GPU_COMPLETION_NOBLOCK_V1'
if marker not in text:
    old = '    if(have_gl)glFinish();'
    assert text.count(old) == 2
    new = '    if(have_gl){ static const bool block = std::getenv("NWII_GPU_FINISH") != nullptr; if(block) glFinish(); else glFlush(); } // NATIVE_GPU_COMPLETION_NOBLOCK_V1'
    text = text.replace(old, new)
    path.write_text(text)
print('PE finish/token no longer block on glFinish (NWII_GPU_FINISH=1 restores).')
