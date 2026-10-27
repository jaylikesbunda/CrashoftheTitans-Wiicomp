// NATIVE_INPUT_MAP_V1: SDL game controller -> retail Wii remote + Nunchuk (and GameCube pad), with the launcher's mapping.
// Replaces src/input/sources/gamepad_source.cpp; the mapping logic itself is in input_mapping.h (unit-tested without SDL).
// NATIVE_INPUT_MAP_V2: one layout per controller slot (NWII_INPUT_MAP_DIR/controller-<slot>.txt, written by the launcher),
// reloaded when the launcher rewrites a file so remaps apply while the game runs. Without the folder, NWII_INPUT_MAP applies
// to every slot as before.
// NATIVE_INPUT_MAP_V3: player 1 can bind keys and mouse buttons too (Key:<SDL name>, Mouse:<button>); they are read here every poll
// and work with or without a controller in slot 1.
#include "gamepad_source.h"
#include "runtime/config.h"
#include "input_mapping.h"
#include "runtime/native_prompt_device.h"
#include <SDL.h>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

namespace nwii::runtime::input {

GamepadSource::~GamepadSource() {
    for (auto* controller : controllers) if (controller) SDL_GameControllerClose(controller);
}

static bool button(SDL_GameController* c, SDL_GameControllerButton b) { return SDL_GameControllerGetButton(c, b) != 0; }
static float axis(SDL_GameController* c, SDL_GameControllerAxis a) { return SDL_GameControllerGetAxis(c, a) / 32767.0f; }

static RawPad read_raw(SDL_GameController* c, bool want_gyro, bool want_accel) {
    RawPad r;
    r.down[P_SOUTH] = button(c, SDL_CONTROLLER_BUTTON_A);
    r.down[P_EAST] = button(c, SDL_CONTROLLER_BUTTON_B);
    r.down[P_WEST] = button(c, SDL_CONTROLLER_BUTTON_X);
    r.down[P_NORTH] = button(c, SDL_CONTROLLER_BUTTON_Y);
    r.down[P_LSHOULDER] = button(c, SDL_CONTROLLER_BUTTON_LEFTSHOULDER);
    r.down[P_RSHOULDER] = button(c, SDL_CONTROLLER_BUTTON_RIGHTSHOULDER);
    r.down[P_LTRIGGER] = SDL_GameControllerGetAxis(c, SDL_CONTROLLER_AXIS_TRIGGERLEFT) > 16384;
    r.down[P_RTRIGGER] = SDL_GameControllerGetAxis(c, SDL_CONTROLLER_AXIS_TRIGGERRIGHT) > 16384;
    r.down[P_BACK] = button(c, SDL_CONTROLLER_BUTTON_BACK);
    r.down[P_START] = button(c, SDL_CONTROLLER_BUTTON_START);
    r.down[P_GUIDE] = button(c, SDL_CONTROLLER_BUTTON_GUIDE);
    r.down[P_LSTICK] = button(c, SDL_CONTROLLER_BUTTON_LEFTSTICK);
    r.down[P_RSTICK] = button(c, SDL_CONTROLLER_BUTTON_RIGHTSTICK);
    r.down[P_DUP] = button(c, SDL_CONTROLLER_BUTTON_DPAD_UP);
    r.down[P_DDOWN] = button(c, SDL_CONTROLLER_BUTTON_DPAD_DOWN);
    r.down[P_DLEFT] = button(c, SDL_CONTROLLER_BUTTON_DPAD_LEFT);
    r.down[P_DRIGHT] = button(c, SDL_CONTROLLER_BUTTON_DPAD_RIGHT);
    r.down[P_TOUCHPAD] = button(c, SDL_CONTROLLER_BUTTON_TOUCHPAD);
    r.down[P_MISC1] = button(c, SDL_CONTROLLER_BUTTON_MISC1);
    r.down[P_PADDLE1] = button(c, SDL_CONTROLLER_BUTTON_PADDLE1);
    r.down[P_PADDLE2] = button(c, SDL_CONTROLLER_BUTTON_PADDLE2);
    r.down[P_PADDLE3] = button(c, SDL_CONTROLLER_BUTTON_PADDLE3);
    r.down[P_PADDLE4] = button(c, SDL_CONTROLLER_BUTTON_PADDLE4);
    r.lx = axis(c, SDL_CONTROLLER_AXIS_LEFTX); r.ly = axis(c, SDL_CONTROLLER_AXIS_LEFTY);
    r.rx = axis(c, SDL_CONTROLLER_AXIS_RIGHTX); r.ry = axis(c, SDL_CONTROLLER_AXIS_RIGHTY);
    if (want_gyro && SDL_GameControllerHasSensor(c, SDL_SENSOR_GYRO)) {
        if(!SDL_GameControllerIsSensorEnabled(c,SDL_SENSOR_GYRO))SDL_GameControllerSetSensorEnabled(c, SDL_SENSOR_GYRO, SDL_TRUE);
        r.has_gyro = SDL_GameControllerGetSensorData(c, SDL_SENSOR_GYRO, r.gyro, 3) == 0;
    }
    if (want_accel && SDL_GameControllerHasSensor(c, SDL_SENSOR_ACCEL)) {
        if(!SDL_GameControllerIsSensorEnabled(c,SDL_SENSOR_ACCEL))SDL_GameControllerSetSensorEnabled(c, SDL_SENSOR_ACCEL, SDL_TRUE);
        r.has_accel = SDL_GameControllerGetSensorData(c, SDL_SENSOR_ACCEL, r.accel, 3) == 0;
    }
    return r;
}

namespace {
// Activity is edge based: a held key, held stick or sensor drift cannot steal the prompts back.
void prompt_activity(SDL_GameController* controller, const RawPad& raw) {
    static const bool enabled = std::getenv("NWII_PROMPT_PACK") != nullptr;
    if (!enabled) return;
    static native_prompts::Activity activity;
    static std::vector<Uint8> previous_keys;
    static Uint32 previous_mouse=0;
    static int previous_x=0, previous_y=0;
    static RawPad previous;
    static SDL_JoystickID previous_id=-1;
    int count=0,x=0,y=0;
    const Uint8* keys=SDL_GetKeyboardState(&count);
    const Uint32 mouse=SDL_GetMouseState(&x,&y);
    bool keyboard=false, pad=false;
    if (previous_keys.size()==static_cast<size_t>(count)) {
        for(int k=0;k<count;++k) keyboard=keyboard || (keys[k] && !previous_keys[k]);
        keyboard=keyboard || (mouse & ~previous_mouse) || x!=previous_x || y!=previous_y;
    }
    previous_keys.assign(keys,keys+count); previous_mouse=mouse; previous_x=x; previous_y=y;
    const auto id=controller?SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(controller)):-1;
    const bool connection_changed=id!=previous_id;
    if(connection_changed) previous=RawPad{};
    for(int b=0;b<P_COUNT;++b) pad=pad || (raw.down[b] && !previous.down[b]);
    const float current_axes[]={raw.lx,raw.ly,raw.rx,raw.ry};
    const float old_axes[]={previous.lx,previous.ly,previous.rx,previous.ry};
    constexpr float stick_dead_zone=8000.f/32767.f; // the same threshold as map_pad
    for(int a=0;a<4;++a) pad=pad || (std::abs(current_axes[a])>stick_dead_zone &&
        (std::abs(old_axes[a])<=stick_dead_zone || std::abs(current_axes[a]-old_axes[a])>0.08f));
    if(raw.has_gyro) for(int a=0;a<3;++a) pad=pad ||
        (std::abs(raw.gyro[a])>0.35f && std::abs(previous.gyro[a])<=0.35f);
    previous=raw; previous_id=id;
    const auto now=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
    const auto type=controller?SDL_GameControllerGetType(controller):SDL_CONTROLLER_TYPE_UNKNOWN;
    const unsigned pad_family=(type==SDL_CONTROLLER_TYPE_PS3 || type==SDL_CONTROLLER_TYPE_PS4 || type==SDL_CONTROLLER_TYPE_PS5)?1:0;
    // The launcher writes this beside the mapping files on every settings save.
    // Physical connections and explicit mode changes apply even while focus is
    // in the launcher; subsequent input resumes ordinary last-device selection.
    static int mode=-1;static std::string policy="auto";static uint64_t next_policy=0;
    bool mode_changed=false;
    if(static_cast<uint64_t>(now)>=next_policy){
        next_policy=static_cast<uint64_t>(now)+200;
        const char* dir=std::getenv("NWII_INPUT_MAP_DIR");
        if(dir){std::ifstream file(std::filesystem::path(dir)/"prompt-mode.txt");int next;std::string prompt;
          if(file>>next>>prompt){mode_changed=next!=mode || prompt!=policy;mode=next;policy=prompt;}}
    }
    unsigned forced=3;
    if(policy=="keyboard")forced=2;else if(policy=="xbox")forced=0;else if(policy=="playstation")forced=1;
    else if(connection_changed || mode_changed)forced=(mode_changed && mode==6)?2:controller?pad_family:2;
    if(forced<3){activity.select(forced,static_cast<uint64_t>(now));native_prompts::device.store(forced,std::memory_order_relaxed);}
    if(policy=="wii"){native_prompts::device.store(3,std::memory_order_relaxed);return;}
    if(policy=="keyboard" || policy=="xbox" || policy=="playstation" || !SDL_GetKeyboardFocus())return;
    unsigned family=keyboard?2:3;
    if (pad && controller) {
        family=pad_family;
    }
    if(activity.update(family,static_cast<uint64_t>(now))) {
        native_prompts::device.store(activity.selected,std::memory_order_relaxed);
        std::cout << "[Prompts] last device: " << (activity.selected==0?"Xbox":activity.selected==1?"PlayStation":"keyboard/mouse") << std::endl;
    }
}

// One slot's mapping and the file it came from, reloaded when the file's modification time changes.
struct SlotMap {
    MapConfig map;
    std::vector<SDL_Scancode> scancodes[W_COUNT];   // map.keys resolved by name
    std::string path;
    std::filesystem::file_time_type stamp{};
    bool have_stamp = false;
};

std::string slot_path(int slot) {
    const char* dir = std::getenv("NWII_INPUT_MAP_DIR");
    if (dir && *dir) {
        const auto p = std::filesystem::path(dir) / ("controller-" + std::to_string(slot) + ".txt");
        std::error_code ec;
        if (std::filesystem::is_regular_file(p, ec)) return p.string();
        if (slot != 0) return {};   // a slot the launcher has no layout for uses the built-in defaults
    }
    const char* single = std::getenv("NWII_INPUT_MAP");
    return single && *single ? std::string(single) : std::string();
}

void load_slot(SlotMap& s, int slot, bool initial) {
    auto& cfg = nwii::runtime::Config::get();
    MapConfig m = default_map(cfg.input_mode);
    m.pointer_speed = cfg.pointer_speed; m.gyro_sens = cfg.gyro_sensitivity;
    s.path = slot_path(slot);
    s.have_stamp = false;
    LoadReport rep;
    if (!s.path.empty() && load_map_into(m, s.path.c_str(), &rep)) {
        std::error_code ec;
        s.stamp = std::filesystem::last_write_time(s.path, ec);
        s.have_stamp = !ec;
        const char* pointer = m.pointer == Pointer::Gyro ? "gyro" : m.pointer == Pointer::Both ? "both" : m.pointer == Pointer::None ? "none" : "stick";
        std::cout << "[Input] " << (initial ? "mapping" : "remap") << " for controller " << slot + 1 << " from " << s.path << ": "
                  << rep.applied << " of " << rep.lines << " lines applied, pointer=" << pointer << " motion=" << (m.motion ? 1 : 0) << std::endl;
        if (!rep.unknown.empty()) std::cout << "[Input] ignored unknown keys: " << rep.unknown << std::endl;
    } else if (initial && slot == 0) {
        std::cout << "[Input] no mapping file; using built-in defaults" << std::endl;
    }
    s.map = m;
    for (int w = 0; w < W_COUNT; ++w) {
        s.scancodes[w].clear();
        for (const auto& name : m.keys[w]) {
            const SDL_Scancode code = SDL_GetScancodeFromName(name.c_str());
            if (code != SDL_SCANCODE_UNKNOWN) s.scancodes[w].push_back(code);
            else if (initial || slot == 0) std::cout << "[Input] unknown key name: " << name << std::endl;
        }
    }
}

// Player 1's keyboard and mouse bindings that are held right now.
void read_keys(const SlotMap& s, RawPad& r) {
    const Uint8* keys = SDL_GetKeyboardState(nullptr);
    const Uint32 mouse = SDL_GetMouseState(nullptr, nullptr);
    uint8_t held = 0;
    if (mouse & SDL_BUTTON(SDL_BUTTON_LEFT)) held |= M_LEFT;
    if (mouse & SDL_BUTTON(SDL_BUTTON_MIDDLE)) held |= M_MIDDLE;
    if (mouse & SDL_BUTTON(SDL_BUTTON_RIGHT)) held |= M_RIGHT;
    if (mouse & SDL_BUTTON(SDL_BUTTON_X1)) held |= M_X1;
    if (mouse & SDL_BUTTON(SDL_BUTTON_X2)) held |= M_X2;
    for (int w = 0; w < W_COUNT; ++w) {
        bool down = (s.map.mouse[w] & held) != 0;
        for (const SDL_Scancode code : s.scancodes[w]) down = down || (keys && keys[code]);
        r.keys[w] = down;
    }
}

// Picks up launcher edits: a changed, new or removed file for the slot reloads it (checked twice a second).
void refresh_slots(SlotMap (&slots)[4]) {
    static auto last = std::chrono::steady_clock::time_point{};
    const auto now = std::chrono::steady_clock::now();
    if (now - last < std::chrono::milliseconds(500)) return;
    last = now;
    for (int i = 0; i < 4; ++i) {
        const std::string path = slot_path(i);
        std::error_code ec;
        const auto stamp = path.empty() ? std::filesystem::file_time_type{} : std::filesystem::last_write_time(path, ec);
        const bool have = !path.empty() && !ec;
        if (path != slots[i].path || have != slots[i].have_stamp || (have && stamp != slots[i].stamp)) load_slot(slots[i], i, false);
    }
}
}  // namespace

void GamepadSource::update(GameCubePadState pads[4], WiimoteState motes[4]) {
    static SlotMap slots[4];
    static const bool loaded = [] { for (int i = 0; i < 4; ++i) load_slot(slots[i], i, true); return true; }();
    (void)loaded;
    refresh_slots(slots);
    static MapperState mapper[4];
    static auto last = std::chrono::steady_clock::now();
    const auto now = std::chrono::steady_clock::now();
    const float dt = std::chrono::duration<float>(now - last).count();
    last = now;

    // All SDL access stays on the host thread; retain one handle per device. A controller plugged in while the game runs takes
    // the first free slot, which is the next player.
    for (int i = 0; i < 4; ++i) {
        auto& controller = controllers[i];
        if (controller && !SDL_GameControllerGetAttached(controller)) {
            std::cout << "[Input] controller " << i + 1 << " disconnected" << std::endl;
            SDL_GameControllerClose(controller); controller = nullptr; mapper[i] = MapperState{};
        }
    }
    for (int device = 0; device < SDL_NumJoysticks(); ++device) {
        if (!SDL_IsGameController(device)) continue;
        const auto id = SDL_JoystickGetDeviceInstanceID(device);
        bool owned = false;
        for (auto* controller : controllers)
            if (controller && SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(controller)) == id) owned = true;
        if (owned) continue;
        for (int i = 0; i < 4; ++i) if (!controllers[i]) {
            controllers[i] = SDL_GameControllerOpen(device);
            if (controllers[i]) std::cout << "[Input] controller " << i + 1 << " connected: " << SDL_GameControllerName(controllers[i]) << std::endl;
            break;
        }
    }
    SDL_GameControllerUpdate();
    for (int i = 0; i < 4; ++i) {
        auto* controller = controllers[i];
        if (!controller) {
            if (i != 0) continue;
            // No controller for player 1: the keyboard and mouse still drive the buttons and the Nunchuk stick.
            // Keep the emulated remote/Nunchuk attached across a physical controller disconnect.
            motes[0].controller_connected = true;
            motes[0].err = 0;
            pads[0].err = 0;
            RawPad keys_only;
            read_keys(slots[0], keys_only);
            prompt_activity(nullptr,keys_only);
            const MapperOutput out = map_pad(keys_only, slots[0].map, mapper[0], dt);
            motes[0].buttons |= out.buttons;
            if (out.recenter) { motes[0].ir_x = 0.5f; motes[0].ir_y = 0.5f; }
            if (out.stick_x) motes[0].nunchuk_x = out.stick_x;
            if (out.stick_y) motes[0].nunchuk_y = out.stick_y;
            motes[0].accel_x = out.remote_accel.x; motes[0].accel_y = out.remote_accel.y; motes[0].accel_z = out.remote_accel.z;
            motes[0].nunchuk_ax = out.nunchuk_accel.x; motes[0].nunchuk_ay = out.nunchuk_accel.y; motes[0].nunchuk_az = out.nunchuk_accel.z;
            continue;
        }
        const MapConfig& map = slots[i].map;
        const bool want_gyro = map.pointer == Pointer::Gyro || map.pointer == Pointer::Both;
        const bool want_accel = map.motion || want_gyro;   // gravity gives the gyro its world vertical
        motes[i].controller_connected = true;
        pads[i].err = 0;
        motes[i].err = 0;

        RawPad raw = read_raw(controller, want_gyro, want_accel);
        if (i == 0) read_keys(slots[0], raw);
        if (i == 0) prompt_activity(controller,raw);
        const MapperOutput out = map_pad(raw, map, mapper[i], dt);

        motes[i].buttons |= out.buttons;
        if (out.recenter) { motes[i].ir_x = 0.5f; motes[i].ir_y = 0.5f; }
        motes[i].ir_x = std::min(1.0f, std::max(0.0f, motes[i].ir_x + out.ir_dx));
        motes[i].ir_y = std::min(1.0f, std::max(0.0f, motes[i].ir_y + out.ir_dy));
        motes[i].accel_x = out.remote_accel.x; motes[i].accel_y = out.remote_accel.y; motes[i].accel_z = out.remote_accel.z;
        motes[i].nunchuk_ax = out.nunchuk_accel.x; motes[i].nunchuk_ay = out.nunchuk_accel.y; motes[i].nunchuk_az = out.nunchuk_accel.z;
        motes[i].nunchuk_x = out.stick_x;
        motes[i].nunchuk_y = out.stick_y;

        // GameCube pad (unchanged): fixed layout.
        if (raw.down[P_SOUTH]) pads[i].buttons |= 0x0100;
        if (raw.down[P_EAST]) pads[i].buttons |= 0x0200;
        if (raw.down[P_START]) pads[i].buttons |= 0x1000;
        if (raw.down[P_WEST]) pads[i].buttons |= 0x0400;
        if (raw.down[P_NORTH]) pads[i].buttons |= 0x0800;
        if (raw.down[P_DUP]) pads[i].buttons |= 0x0008;
        if (raw.down[P_DDOWN]) pads[i].buttons |= 0x0004;
        if (raw.down[P_DLEFT]) pads[i].buttons |= 0x0001;
        if (raw.down[P_DRIGHT]) pads[i].buttons |= 0x0002;
        if (raw.down[P_LSHOULDER]) pads[i].buttons |= 0x0040;
        if (raw.down[P_RSHOULDER]) pads[i].buttons |= 0x0020;
        if (raw.down[P_RTRIGGER]) pads[i].buttons |= 0x0010;
        if (std::fabs(raw.lx) * 32767.0f > 8000.0f) pads[i].stick_x = (int8_t)(raw.lx * 127.0f);
        if (std::fabs(raw.ly) * 32767.0f > 8000.0f) pads[i].stick_y = (int8_t)(-raw.ly * 127.0f);
        pads[i].trigger_l = (uint8_t)(std::max(0.0f, SDL_GameControllerGetAxis(controller, SDL_CONTROLLER_AXIS_TRIGGERLEFT) / 32767.0f) * 255.0f);
        pads[i].trigger_r = (uint8_t)(std::max(0.0f, SDL_GameControllerGetAxis(controller, SDL_CONTROLLER_AXIS_TRIGGERRIGHT) / 32767.0f) * 255.0f);
    }
}

}
