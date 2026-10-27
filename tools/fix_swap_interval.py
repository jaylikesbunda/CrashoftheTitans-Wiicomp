"""Do not park the GX service thread on vsync.

The main loop services the guest's GPU-drain requests (GXDrawDone, copies,
token waits) and also presents. SDL_GL_SwapWindow with swap interval 1 blocks
that thread for up to a refresh period every iteration, during which no drain
request is serviced. A live profile of the render thread in a heavy scene
showed ~45% of samples in SwapBuffers and the game running at 3-4 frames per
second, because the guest waits on the GPU many times per frame. The loop is
already paced to the console field rate by pace_video_field(), so vsync adds
only stalls. NWII_VSYNC=1 restores the old behaviour.
"""
from pathlib import Path
port=Path(__file__).resolve().parents[1]
path=port/'build/nwii-full-source/nWiiRuntime/src/core/main.cpp'
text=path.read_text()
marker='NATIVE_NO_BLOCKING_SWAP_V1'
if marker not in text:
    old='      SDL_GL_SetSwapInterval(offscreen?0:1);'
    new='      SDL_GL_SetSwapInterval((offscreen||!std::getenv("NWII_VSYNC"))?0:1); // NATIVE_NO_BLOCKING_SWAP_V1'
    assert text.count(old)==1
    path.write_text(text.replace(old,new,1))
print('Swap interval 0 by default (NWII_VSYNC=1 re-enables vsync).')
