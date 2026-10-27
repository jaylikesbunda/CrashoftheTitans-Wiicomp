"""NATIVE_RENDER_LOOP_SPIKES_V1 -- keep the render thread's slow host calls from parking the guest.

Three runtime behaviours, all in src/core/main.cpp:

1. SDL_HINT_JOYSTICK_THREAD=1 before SDL_Init, so SDL polls and hot-plugs joysticks on its own thread (Windows).
   Every controller read in the staged runtime stays on the host thread (gamepad_source.cpp calls SDL_NumJoysticks,
   SDL_IsGameController, SDL_GameControllerOpen/GetAttached and SDL_GameControllerUpdate itself and reads state through the
   GameController getters), and nothing consumes joystick or controller device events from the queue, so the hint changes
   only where SDL polls. NWII_NO_JOYSTICK_THREAD=1 leaves SDL's default.

2. A guest GX drain request that lands while the render thread is inside host.event_poll or SDL_GL_SwapWindow is served just
   before that call, with the same framebuffer/viewport setup and ProcessGXFifo() that pace_video_field uses. The bound
   framebuffer and viewport are saved and restored, so the guest-observable command order is unchanged; the drain is only
   taken at loop points where GL state is complete (top of the loop, after the HUD, before the swap).
   Scopes host.drain_before_poll and host.drain_before_present keep this time apart from host.event_poll and host.present.
   NWII_NO_DRAIN_BEFORE_HOST=1 turns it off.

Each edit stores its original as base64 so `--restore` returns the exact staged text. Idempotent. Pass --root to work on
another copy of nWiiRuntime.
"""
from pathlib import Path
import argparse, base64, re

MARK = 'NATIVE_RENDER_LOOP_SPIKES_V1'
BLOCK = re.compile(r'/\* ' + MARK + r':(\w+) ([A-Za-z0-9+/=]*) \*/(.*?)/\* END_' + MARK + r':\1 \*/', re.S)
MAIN = 'src/core/main.cpp'


def read(path):
    return path.read_bytes().decode('utf-8', 'surrogateescape')


def write(path, text):
    path.write_bytes(text.encode('utf-8', 'surrogateescape'))


def edit(text, tag, old, new):
    """Replace `old` (written with \\n) by a restorable block holding `new`; skip when already applied."""
    if f'/* {MARK}:{tag} ' in text:
        return text
    eol = '\r\n' if '\r\n' in text else '\n'
    old = old.replace('\n', eol)
    new = new.replace('\n', eol)
    assert text.count(old) == 1, (tag, text.count(old))
    packed = base64.b64encode(old.encode('utf-8', 'surrogateescape')).decode()
    return text.replace(old, f'/* {MARK}:{tag} {packed} */{new}/* END_{MARK}:{tag} */', 1)


def unblock(text):
    return BLOCK.sub(lambda m: base64.b64decode(m.group(2)).decode('utf-8', 'surrogateescape'), text)


JOYSTICK = (
    '    SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI_PS5, "1");\n',
    '    SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI_PS5, "1");\n'
    '    // NWII_NO_JOYSTICK_THREAD=1 keeps SDL\'s default polling.\n'
    '    if (!std::getenv("NWII_NO_JOYSTICK_THREAD")) SDL_SetHint(SDL_HINT_JOYSTICK_THREAD, "1");\n',
)

DRAIN_HELPER = '''  // NATIVE_RENDER_LOOP_SPIKES_V1: a guest drain request that lands during a slow host call (SDL event pumping,
  // SDL_GL_SwapWindow) is served just before that call. Same framebuffer/viewport setup and ProcessGXFifo() as
  // pace_video_field; the bound framebuffer and viewport are restored, so command order is unchanged.
  // NWII_NO_DRAIN_BEFORE_HOST=1 turns it off.
  static const bool drain_before_host = std::getenv("NWII_NO_DRAIN_BEFORE_HOST") == nullptr;
  auto service_gx_drain_before_host = [&](const char* scope) {
    if (!drain_before_host) return;
    native_trace::Scope trace_drain(scope);
    if (!nwii::runtime::NativeConsumeGxDrain()) return;
    extern void ProcessGXFifo();
    GLint saved_fbo = 0, saved_viewport[4] = {0, 0, 0, 0};
    glGetIntegerv(GL_FRAMEBUFFER_BINDING, &saved_fbo);
    glGetIntegerv(GL_VIEWPORT, saved_viewport);
    glBindFramebuffer(GL_FRAMEBUFFER, efb_fbo);
    NativeViewport(0, 0, NativeEfbWidth(), NativeEfbHeight());
    nwii::runtime::native_render_wait::DrainStart();
    ProcessGXFifo();
    glBindFramebuffer(GL_FRAMEBUFFER, GLuint(saved_fbo));
    NativeViewport(saved_viewport[0], saved_viewport[1], saved_viewport[2], saved_viewport[3]);
  };
'''
VI_CADENCE = '  std::cout << "[VI cadence] nominal field_ns=" << field_period.count() << std::endl;\n'
POLL = '        native_trace::Scope events("host.event_poll");\n'
PRESENT = '        if(std::getenv("NWII_OFFSCREEN")==nullptr) { native_trace::Scope trace_present("host.present"); SDL_GL_SwapWindow(window); }'


def apply_text(text):
    text = edit(text, 'joystick', *JOYSTICK)
    text = edit(text, 'helper', VI_CADENCE, DRAIN_HELPER + VI_CADENCE)
    text = edit(text, 'poll', POLL, '        service_gx_drain_before_host("host.drain_before_poll");\n' + POLL)
    text = edit(text, 'present', PRESENT,
                '        if(std::getenv("NWII_OFFSCREEN")==nullptr) service_gx_drain_before_host("host.drain_before_present");\n' + PRESENT)
    return text


def apply(port, rt):
    path = rt / MAIN
    old = read(path)
    new = apply_text(old)
    if new != old:
        write(path, new)
    print('Render loop spikes: joystick thread hint, drain before poll and present.')


def restore(port, rt):
    path = rt / MAIN
    old = read(path)
    new = unblock(old)
    if new != old:
        write(path, new)
    print('Render loop spikes removed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    port = Path(__file__).resolve().parents[1]
    rt = args.root or port / 'build/nwii-full-source/nWiiRuntime'
    (restore if args.restore else apply)(port, rt)
