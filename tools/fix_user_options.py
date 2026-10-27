"""Launcher-facing runtime options: window mode, anisotropic filtering and a pointer that follows the real window size.

NWII_WINDOW_MODE=borderless   desktop-resolution fullscreen window (F11 toggles it at any time); anything else keeps a normal window.
NWII_ANISO=2|4|8|16           anisotropic filtering on every texture sampler (clamped to what the GPU reports).
Mouse pointer: normalised by the actual window size, so resizing and fullscreen no longer offset the pointer.

Each edit is anchored on one stable line and tagged, so the stage is idempotent on a pristine or an already patched tree.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
src = port / 'build/nwii-full-source/nWiiRuntime/src'


def edit(path, tag, anchor, new, keep_anchor):
    """Replace `anchor` with `new` (keep_anchor=None), or put `new` before/after it ('before' / 'after'). Skipped when `tag` is present."""
    text = path.read_text()
    if tag in text:
        return
    assert text.count(anchor) == 1, (path.name, tag, anchor.strip()[:70], text.count(anchor))
    out = {None: new, 'before': new + anchor, 'after': anchor + new}[keep_anchor]
    path.write_text(text.replace(anchor, out, 1))


# 1. anisotropic filtering, set once when a unit's sampler object is created
edit(src / 'hle/gx/renderer_gl.cpp', 'NATIVE_USER_ANISO_V1',
     '  if(!samplers[unit])glGenSamplers(1,&samplers[unit]);\n',
     '  if(!samplers[unit]) { // NATIVE_USER_ANISO_V1\n'
     '    glGenSamplers(1,&samplers[unit]);\n'
     '    static const float aniso=[](){\n'
     '      const char*e=std::getenv("NWII_ANISO");float v=e?float(std::atof(e)):1.f;if(v<=1.f)return 1.f;\n'
     '      GLfloat hw=1.f;glGetFloatv(0x84FF,&hw);while(glGetError()!=GL_NO_ERROR){} // GL_MAX_TEXTURE_MAX_ANISOTROPY\n'
     '      return hw<1.f?1.f:(v>hw?hw:v);}();\n'
     '    if(aniso>1.f)glSamplerParameterf(samplers[unit],0x84FE,aniso); // GL_TEXTURE_MAX_ANISOTROPY\n'
     '  }\n', None)

# 2. window mode and the F11 toggle
main = src / 'core/main.cpp'
edit(main, 'NATIVE_USER_WINDOW_V1',
     '    window = SDL_CreateWindow("NWiiRecomp",',
     '    { // NATIVE_USER_WINDOW_V1\n'
     '      const char* wm = std::getenv("NWII_WINDOW_MODE");\n'
     '      if (wm && !offscreen && std::string(wm) == "borderless") window_flags |= SDL_WINDOW_FULLSCREEN_DESKTOP;\n'
     '    }\n', 'before')
edit(main, 'NATIVE_USER_F11_V1',
     '          else if (e.type == SDL_KEYDOWN && !e.key.repeat) native_hud_gl::key(e.key.keysym.sym); // NATIVE_DEV_HUD_V1',
     '          else if (e.type == SDL_KEYDOWN && !e.key.repeat && e.key.keysym.sym == SDLK_F11) { // NATIVE_USER_F11_V1\n'
     '            const bool full = (SDL_GetWindowFlags(window) & SDL_WINDOW_FULLSCREEN_DESKTOP) == SDL_WINDOW_FULLSCREEN_DESKTOP;\n'
     '            SDL_SetWindowFullscreen(window, full ? 0 : SDL_WINDOW_FULLSCREEN_DESKTOP);\n'
     '          }\n', 'before')

# 3. pointer normalised by the real window size
edit(src / 'input/sources/mouse_keyboard_source.cpp', 'NATIVE_USER_POINTER_V1',
     '    if (h == 0) h = 480;\n',
     '    if (SDL_Window* win = SDL_GetMouseFocus()) SDL_GetWindowSize(win, &w, &h); // NATIVE_USER_POINTER_V1\n', 'after')
print('User options installed: window mode, F11, anisotropic filtering, pointer by window size.')
