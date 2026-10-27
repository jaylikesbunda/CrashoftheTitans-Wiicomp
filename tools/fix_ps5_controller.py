"""Stage SDL controller ownership and a thread-safe retail Nunchuk bridge."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
stage = PORT / 'build/nwii-full-source/nWiiRuntime'
# The later mapped-input overlay subsumes this bridge. Replaying the base
# patch over that final layout would reject valid added accelerometer fields.
if 'NATIVE_INPUT_MAP_V1' in (stage/'include/input/input_source.h').read_text():
    print('Controller bridge already supplied by mapped-input overlay.');raise SystemExit(0)

def patch(relative, old, new):
    p = stage / relative
    s = p.read_text()
    if new in s:
        return
    assert s.count(old) == 1, (relative, old)
    p.write_text(s.replace(old, new, 1))

patch('include/input/input_source.h', '    int8_t err;\n};\n\nclass IInputSource',
      '    int8_t err;\n    int8_t nunchuk_x = 0, nunchuk_y = 0;\n    bool controller_connected = false;\n};\n\nclass IInputSource')
patch('include/input/input_manager.h', '#include <memory>', '#include <memory>\n#include <mutex>')
patch('include/input/input_manager.h', '    GameCubePadState gc_pads[4];',
      '    std::mutex state_mutex;\n    GameCubePadState gc_pads[4];')
for signature in ('void InputManager::update() {',
                  'GameCubePadState InputManager::get_gcpad_state(int index) {',
                  'WiimoteState InputManager::get_wiimote_state(int index) {'):
    patch('src/input/input_manager.cpp', signature, signature+'\n    std::lock_guard<std::mutex> lock(state_mutex);')
patch('src/input/input_manager.cpp', '        wii_motes[i].buttons = 0;',
      '        wii_motes[i].nunchuk_x = wii_motes[i].nunchuk_y = 0;\n'
      '        wii_motes[i].controller_connected = false;\n        wii_motes[i].buttons = 0;')
patch('src/input/sources/gamepad_source.h', '#include "input/input_source.h"',
      '#include "input/input_source.h"\n#include <SDL.h>')
patch('src/input/sources/gamepad_source.h', '    ~GamepadSource() override = default;',
      '    ~GamepadSource() override;')
patch('src/input/sources/gamepad_source.h', '    void update(GameCubePadState pads[4], WiimoteState motes[4]) override;',
      '    void update(GameCubePadState pads[4], WiimoteState motes[4]) override;\n'
      'private:\n    SDL_GameController* controllers[4]{};')
patch('src/input/sources/gamepad_source.cpp', 'void GamepadSource::update(',
      'GamepadSource::~GamepadSource() {\n'
      '    for (auto* controller : controllers) if (controller) SDL_GameControllerClose(controller);\n'
      '}\n\nvoid GamepadSource::update(')
old = '''    for (int i = 0; i < 4; ++i) {
        if (!SDL_IsGameController(i))
            continue;
            
        SDL_GameController* controller = SDL_GameControllerOpen(i);
        if (!controller) continue;'''
new = '''    // All SDL access stays on the host thread; retain one handle per device.
    for (auto& controller : controllers) {
        if (controller && !SDL_GameControllerGetAttached(controller)) {
            SDL_GameControllerClose(controller); controller = nullptr;
        }
    }
    for (int device = 0; device < SDL_NumJoysticks(); ++device) {
        if (!SDL_IsGameController(device)) continue;
        const auto id = SDL_JoystickGetDeviceInstanceID(device);
        bool owned = false;
        for (auto* controller : controllers)
            if (controller && SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(controller)) == id) owned = true;
        if (owned) continue;
        for (auto& controller : controllers) if (!controller) {
            controller = SDL_GameControllerOpen(device); break;
        }
    }
    SDL_GameControllerUpdate();
    for (int i = 0; i < 4; ++i) {
        auto* controller = controllers[i];
        if (!controller) continue;
        motes[i].controller_connected = true;'''
patch('src/input/sources/gamepad_source.cpp', old, new)
patch('src/input/sources/gamepad_source.cpp',
      'SDL_CONTROLLER_BUTTON_X)) motes[i].buttons |= 0x0002;',
      'SDL_CONTROLLER_BUTTON_X)) motes[i].buttons |= 0x0200; // Square: Wii 1')
patch('src/input/sources/gamepad_source.cpp',
      'SDL_CONTROLLER_BUTTON_Y)) motes[i].buttons |= 0x0001;',
      'SDL_CONTROLLER_BUTTON_Y)) motes[i].buttons |= 0x0100; // Triangle: Wii 2')
patch('src/input/sources/gamepad_source.cpp',
      'SDL_CONTROLLER_BUTTON_GUIDE)) motes[i].buttons |= 0x0080;',
      'SDL_CONTROLLER_BUTTON_GUIDE)) motes[i].buttons |= 0x8000; // retail WPAD Home')
patch('src/input/sources/gamepad_source.cpp', '        int lx = SDL_GameControllerGetAxis',
      '''        // Retail WPAD NC bits (original 803F2B14): Z=bit13, C=bit14.
        if (SDL_GameControllerGetButton(controller, SDL_CONTROLLER_BUTTON_LEFTSHOULDER)) motes[i].buttons |= 0x4000;
        if (SDL_GameControllerGetButton(controller, SDL_CONTROLLER_BUTTON_RIGHTSHOULDER) ||
            SDL_GameControllerGetAxis(controller, SDL_CONTROLLER_AXIS_TRIGGERLEFT) > 16384) motes[i].buttons |= 0x2000;
        if (SDL_GameControllerGetAxis(controller, SDL_CONTROLLER_AXIS_TRIGGERRIGHT) > 16384) motes[i].buttons |= 0x0400;
        int lx = SDL_GameControllerGetAxis''')
patch('src/input/sources/gamepad_source.cpp', '        if (!want_pointer) {',
      '        motes[i].nunchuk_x = pads[i].stick_x;\n'
      '        motes[i].nunchuk_y = pads[i].stick_y;\n\n        if (!want_pointer) {')
patch('src/core/main.cpp', '    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_AUDIO | SDL_INIT_GAMECONTROLLER) < 0) {',
      '    SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI_PS5, "1");\n'
      '    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_AUDIO | SDL_INIT_GAMECONTROLLER) < 0) {')
print('Controller handles, hotplug, NC mapping and locked snapshots staged.')
