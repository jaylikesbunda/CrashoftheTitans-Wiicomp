"""Check NATIVE_RENDER_LOOP_SPIKES_V1 (tools/fix_render_loop_spikes.py) on a scratch copy of main.cpp.

No game, GL context or SDL is used: the stage must apply once, be a no-op on a second pass, restore the exact staged text,
and place its calls where the spike analysis needs them. It also checks that nothing in the staged runtime consumes
joystick or controller device events from the queue, which is what the joystick-thread hint would change.
"""
from pathlib import Path
import argparse, re, shutil, tempfile
import fix_render_loop_spikes as stage

port = Path(__file__).resolve().parents[1]
staged = port / 'build/nwii-full-source/nWiiRuntime'
DEVICE_EVENTS = re.compile(r'SDL_(JOY|CONTROLLER)(DEVICE(ADDED|REMOVED|REMAPPED)|BUTTON(DOWN|UP)|AXISMOTION|HATMOTION|BALLMOTION)')


def require(condition, message):
    if not condition:
        raise SystemExit('CHECK_RENDER_LOOP_SPIKES failed: ' + message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=staged)
    args = parser.parse_args()
    root = args.root
    with tempfile.TemporaryDirectory(prefix='render-loop-spikes-') as temp:
        rt = Path(temp) / 'rt'
        (rt / stage.MAIN).parent.mkdir(parents=True)
        # The staged tree may already carry the stage: the scratch copy starts from the canonical text.
        original = stage.unblock(stage.read(root / stage.MAIN))
        stage.write(rt / stage.MAIN, original)
        originals = (rt / stage.MAIN).read_bytes()

        stage.apply(port, rt)
        first = (rt / stage.MAIN).read_bytes()
        require(first != originals, 'the stage must change main.cpp')
        stage.apply(port, rt)
        require(first == (rt / stage.MAIN).read_bytes(), 'second apply must be a no-op')
        print('STAGE_IDEMPOTENT ok')

        shutil.copytree(rt, Path(temp) / 'rt2')
        stage.restore(port, Path(temp) / 'rt2')
        require((Path(temp) / 'rt2' / stage.MAIN).read_bytes() == originals, 'restore must be exact')
        print('STAGE_RESTORE ok')

    patched = stage.apply_text(original)

    # 1. joystick thread: after the PS5 hint, before SDL_Init, behind the opt-out.
    hint = patched.index('SDL_SetHint(SDL_HINT_JOYSTICK_THREAD, "1")')
    require(patched.index('SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI_PS5, "1")') < hint < patched.index('SDL_Init(SDL_INIT_VIDEO'),
            'joystick thread hint must sit between the PS5 hint and SDL_Init')
    require('getenv("NWII_NO_JOYSTICK_THREAD")' in patched, 'joystick thread opt-out missing')
    print('JOYSTICK_HINT ok')

    # 2. drain before host calls: the helper is defined before its first call and each call sits right before the slow call.
    helper = patched.index('auto service_gx_drain_before_host = [&](const char* scope) {')
    poll_call = patched.index('service_gx_drain_before_host("host.drain_before_poll");')
    present_call = patched.index('service_gx_drain_before_host("host.drain_before_present");')
    require(helper < poll_call and helper < present_call, 'helper must be defined before its calls')
    require(poll_call < patched.index('native_trace::Scope events("host.event_poll");') < patched.index('SDL_PollEvent(&e)'),
            'drain before poll must precede the event_poll scope')
    require(present_call < patched.index('SDL_GL_SwapWindow(window);', present_call) and
            patched.index('SDL_GL_SwapWindow(window);', present_call) - present_call < 400,
            'drain before present must sit right before SwapWindow')
    helper_body = patched[patched.rindex('static const bool drain_before_host', 0, helper):patched.index('\n  };', helper)]
    for needle in ('NWII_NO_DRAIN_BEFORE_HOST', 'NativeConsumeGxDrain()', 'DrainStart()', 'ProcessGXFifo()',
                   'glBindFramebuffer(GL_FRAMEBUFFER, GLuint(saved_fbo))', 'NativeViewport(saved_viewport'):
        require(needle in helper_body, 'helper is missing ' + needle)
    print('DRAIN_BEFORE_HOST ok')

    # 3. the joystick hint changes only SDL's polling: no device events are read from the queue anywhere in the runtime.
    hits = []
    for path in list((root / 'src').rglob('*.cpp')) + list((root / 'src').rglob('*.h')) + list((root / 'include').rglob('*.h')):
        for match in DEVICE_EVENTS.finditer(stage.read(path)):
            hits.append(f'{path.relative_to(root)}: {match.group(0)}')
    require(not hits, 'device event types are read from the queue: ' + '; '.join(hits[:5]))
    print('NO_DEVICE_EVENT_CONSUMERS ok')
    print('CHECK_RENDER_LOOP_SPIKES ok')


if __name__ == '__main__':
    main()
