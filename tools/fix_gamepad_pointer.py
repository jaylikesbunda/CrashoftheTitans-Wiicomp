"""Right stick moves the Wii-remote pointer in every gamepad scheme (Chapter 2 pointer targeting).

Before: only schemes 3/4 moved the pointer from a controller (scheme 3/4 also let a gyro drive it), the Classic scheme sent the right stick
as a GameCube C-stick (which a Wii game never reads), and MouseKeyboardSource rewrote the pointer from the OS mouse position on every
update, so any controller movement was erased one frame later in schemes 2-4.
Now: the gamepad source always applies the right stick to the pointer (gyro only in scheme 4, as documented), and the mouse source writes
the pointer only when the mouse actually moved, so both devices can steer the same cursor. Idempotent.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
src = p / 'build/nwii-full-source/nWiiRuntime/src/input/sources'
MARK = 'NATIVE_GAMEPAD_POINTER_V1'


def apply(path, old, new, probe):
    s = path.read_text(errors='surrogateescape')
    if probe in s or (path.name=='gamepad_source.cpp' and 'NATIVE_INPUT_MAP_V1' in s):
        return
    assert s.count(old) == 1, (path.name, old[:70], s.count(old))
    path.write_text(s.replace(old, new, 1), errors='surrogateescape')


g = src / 'gamepad_source.cpp'
apply(g, 'bool want_pointer = (mode == 3 || mode == 4);',
      'bool want_pointer = true; // ' + MARK + ': the right stick drives the Wii-remote pointer in every gamepad scheme', MARK)
apply(g, 'if (SDL_GameControllerHasSensor(controller, SDL_SENSOR_GYRO)) {',
      'if (mode == 4 && SDL_GameControllerHasSensor(controller, SDL_SENSOR_GYRO)) { // ' + MARK + ': gyro only in the full-tilt scheme',
      'mode == 4 && SDL_GameControllerHasSensor')
# Earlier stages (fix_ps5_controller.py) test for the original 'if (!want_pointer) {' line, so it must stay: with want_pointer always
# true the GameCube C-stick branch simply never runs (a Wii game does not read it). Undo the version of this stage that edited it.
_t = g.read_text(errors='surrogateescape')
_edited = '        if (mode == 2) { // ' + MARK + ': Classic scheme keeps the C-stick too\n            int rx'
if _edited in _t:
    g.write_text(_t.replace(_edited, '        if (!want_pointer) {\n            int rx', 1), errors='surrogateescape')
m = src / 'mouse_keyboard_source.cpp'
apply(m, '    motes[0].ir_x = (float)mx / (float)w;\n    motes[0].ir_y = (float)my / (float)h;',
      '    // ' + MARK + ': the OS mouse only steers the pointer when it moves, so a controller can steer it too.\n'
      '    static int last_mx = -1, last_my = -1;\n'
      '    if (last_mx < 0 || mx != last_mx || my != last_my) {\n'
      '        motes[0].ir_x = (float)mx / (float)w;\n'
      '        motes[0].ir_y = (float)my / (float)h;\n'
      '        last_mx = mx; last_my = my;\n'
      '    }', 'static int last_mx = -1')
print('gamepad pointer installed')
