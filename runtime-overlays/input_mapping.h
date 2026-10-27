// NATIVE_INPUT_MAP_V1: launcher-configurable controller mapping for the physical Wii remote + Nunchuk adapter.
// Pure logic (no SDL): the gamepad source reads a controller into a RawPad, this turns it into the Wii-remote state.
//
// Mapping file (written by the launcher, path in NWII_INPUT_MAP), one "key=value" per line, '#' starts a comment:
//   a=South                      Wii input = controller inputs, comma separated; any one of them triggers it
//   z=RightShoulder,LeftTrigger
//   pointer=gyro                 stick | gyro | both | none
//   motion=1  motion_target=remote|nunchuk|both  ...
// Controller input names (case-insensitive): South East West North LeftShoulder RightShoulder LeftTrigger RightTrigger Back Start
//   Guide LeftStick RightStick DpadUp DpadDown DpadLeft DpadRight Touchpad Misc1 Paddle1 Paddle2 Paddle3 Paddle4
// Keyboard and mouse (player 1 only): Key:<SDL key name> (Key:Space, Key:Left Shift, Key:A) and Mouse:Left|Right|Middle|X1|X2.
//   stick_up / stick_down / stick_left / stick_right drive the Nunchuk stick from keys.
// A missing key keeps the built-in default, which reproduces the previous fixed mapping exactly; a line replaces every binding of
// that input, controller, keyboard and mouse alike.
#pragma once
#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <string>
#include <vector>

namespace nwii::runtime::input {

enum PadInput : int {
  P_SOUTH, P_EAST, P_WEST, P_NORTH, P_LSHOULDER, P_RSHOULDER, P_LTRIGGER, P_RTRIGGER, P_BACK, P_START, P_GUIDE,
  P_LSTICK, P_RSTICK, P_DUP, P_DDOWN, P_DLEFT, P_DRIGHT, P_TOUCHPAD, P_MISC1, P_PADDLE1, P_PADDLE2, P_PADDLE3, P_PADDLE4, P_COUNT
};
enum WiiInput : int {
  W_A, W_B, W_1, W_2, W_PLUS, W_MINUS, W_HOME, W_UP, W_DOWN, W_LEFT, W_RIGHT, W_C, W_Z,
  W_SHAKE_REMOTE, W_SHAKE_NUNCHUK, W_RECENTER, W_STICK_UP, W_STICK_DOWN, W_STICK_LEFT, W_STICK_RIGHT, W_COUNT
};
enum MouseButton : uint8_t { M_LEFT = 1, M_MIDDLE = 2, M_RIGHT = 4, M_X1 = 8, M_X2 = 16 };

struct RawPad {
  bool down[P_COUNT] = {};
  float lx = 0, ly = 0, rx = 0, ry = 0;           // sticks, -1..1 (raw, no dead zone)
  bool has_gyro = false, has_accel = false;
  float gyro[3] = {0, 0, 0};                      // rad/s, SDL controller axes (x right, y up, z toward the player)
  float accel[3] = {0, 0, 0};                     // m/s^2, same axes; at rest flat and face up it reads (0, 0, +9.8)
  bool keys[W_COUNT] = {};                        // the input's keyboard or mouse binding is held (filled by the SDL glue)
};

// Retail WPAD button bits.
static const uint32_t kWiiMask[W_COUNT] = {
  0x0800, 0x0400, 0x0200, 0x0100, 0x0010, 0x1000, 0x8000, 0x0008, 0x0004, 0x0001, 0x0002, 0x4000, 0x2000, 0, 0, 0, 0, 0, 0, 0
};

enum class Pointer { Stick, Gyro, Both, None };
enum MotionTarget { MT_REMOTE = 1, MT_NUNCHUK = 2, MT_BOTH = 3 };

struct MapConfig {
  uint32_t bind[W_COUNT] = {};
  std::vector<std::string> keys[W_COUNT];         // SDL key names, resolved to scancodes by the SDL glue
  uint8_t mouse[W_COUNT] = {};                    // MouseButton bits
  Pointer pointer = Pointer::Stick;
  bool nunchuk_right_stick = false;               // the Nunchuk stick follows the right stick instead of the left
  float pointer_speed = 1.0f, gyro_sens = 1.0f;
  bool gyro_inv_x = false, gyro_inv_y = false;
  bool motion = false;                            // controller motion drives the Wii accelerometers
  int motion_target = MT_BOTH;
  float motion_sens = 1.0f;
  bool motion_inv_x = false, motion_inv_y = false;
  bool from_file = false;
};

inline uint32_t bit(PadInput p) { return 1u << p; }

// Previous fixed mapping: A/B/1/2/+/-/Home/D-pad on the face and system buttons, C = L1, Z = R1 or L2, B also on R2.
inline MapConfig default_map(int legacy_mode) {
  MapConfig m;
  m.bind[W_A] = bit(P_SOUTH);
  m.bind[W_B] = bit(P_EAST) | bit(P_RTRIGGER);
  m.bind[W_1] = bit(P_WEST);
  m.bind[W_2] = bit(P_NORTH);
  m.bind[W_PLUS] = bit(P_START);
  m.bind[W_MINUS] = bit(P_BACK);
  m.bind[W_HOME] = bit(P_GUIDE);
  m.bind[W_UP] = bit(P_DUP); m.bind[W_DOWN] = bit(P_DDOWN); m.bind[W_LEFT] = bit(P_DLEFT); m.bind[W_RIGHT] = bit(P_DRIGHT);
  m.bind[W_C] = bit(P_LSHOULDER);
  m.bind[W_Z] = bit(P_RSHOULDER) | bit(P_LTRIGGER);
  m.pointer = legacy_mode == 4 ? Pointer::Gyro : Pointer::Stick;
  // Keyboard and mouse: the mouse buttons and Return keep their old meaning (A, B, +), the rest fills the gaps the old fixed
  // keyboard layout left.
  m.keys[W_A] = {"Space"}; m.mouse[W_A] = M_LEFT;
  m.mouse[W_B] = M_RIGHT;
  m.keys[W_1] = {"1"}; m.keys[W_2] = {"2"};
  m.keys[W_PLUS] = {"Return"}; m.keys[W_MINUS] = {"Backspace"};
  m.keys[W_UP] = {"Up"}; m.keys[W_DOWN] = {"Down"}; m.keys[W_LEFT] = {"Left"}; m.keys[W_RIGHT] = {"Right"};
  m.keys[W_C] = {"Q"}; m.keys[W_Z] = {"Left Shift"};
  m.keys[W_STICK_UP] = {"W"}; m.keys[W_STICK_DOWN] = {"S"}; m.keys[W_STICK_LEFT] = {"A"}; m.keys[W_STICK_RIGHT] = {"D"};
  m.keys[W_SHAKE_REMOTE] = {"F"}; m.keys[W_SHAKE_NUNCHUK] = {"R"}; m.mouse[W_RECENTER] = M_MIDDLE;
  return m;
}

inline bool mouse_by_name(const std::string& name, uint8_t& out) {
  static const struct { const char* n; uint8_t b; } t[] = {{"left", M_LEFT}, {"middle", M_MIDDLE}, {"right", M_RIGHT}, {"x1", M_X1}, {"x2", M_X2}};
  std::string n = name; for (auto& c : n) c = (char)std::tolower((unsigned char)c);
  for (auto& e : t) if (n == e.n) { out = e.b; return true; }
  return false;
}

inline std::string lower(std::string s) { for (auto& c : s) c = (char)std::tolower((unsigned char)c); return s; }
inline std::string trim(const std::string& s) {
  size_t a = 0, b = s.size();
  while (a < b && std::isspace((unsigned char)s[a])) ++a;
  while (b > a && std::isspace((unsigned char)s[b - 1])) --b;
  return s.substr(a, b - a);
}

inline bool pad_by_name(const std::string& name, PadInput& out) {
  static const struct { const char* n; PadInput p; } t[] = {
    {"south", P_SOUTH}, {"east", P_EAST}, {"west", P_WEST}, {"north", P_NORTH},
    {"leftshoulder", P_LSHOULDER}, {"rightshoulder", P_RSHOULDER}, {"lefttrigger", P_LTRIGGER}, {"righttrigger", P_RTRIGGER},
    {"back", P_BACK}, {"start", P_START}, {"guide", P_GUIDE}, {"leftstick", P_LSTICK}, {"rightstick", P_RSTICK},
    {"dpadup", P_DUP}, {"dpaddown", P_DDOWN}, {"dpadleft", P_DLEFT}, {"dpadright", P_DRIGHT},
    {"touchpad", P_TOUCHPAD}, {"misc1", P_MISC1}, {"paddle1", P_PADDLE1}, {"paddle2", P_PADDLE2}, {"paddle3", P_PADDLE3}, {"paddle4", P_PADDLE4},
  };
  const std::string n = lower(name);
  for (auto& e : t) if (n == e.n) { out = e.p; return true; }
  return false;
}

inline bool wii_by_name(const std::string& name, WiiInput& out) {
  static const struct { const char* n; WiiInput w; } t[] = {
    {"a", W_A}, {"b", W_B}, {"one", W_1}, {"two", W_2}, {"plus", W_PLUS}, {"minus", W_MINUS}, {"home", W_HOME},
    {"up", W_UP}, {"down", W_DOWN}, {"left", W_LEFT}, {"right", W_RIGHT}, {"c", W_C}, {"z", W_Z},
    {"shake_remote", W_SHAKE_REMOTE}, {"shake_nunchuk", W_SHAKE_NUNCHUK}, {"recenter", W_RECENTER},
    {"stick_up", W_STICK_UP}, {"stick_down", W_STICK_DOWN}, {"stick_left", W_STICK_LEFT}, {"stick_right", W_STICK_RIGHT},
  };
  const std::string n = lower(name);
  for (auto& e : t) if (n == e.n) { out = e.w; return true; }
  return false;
}

inline bool truthy(const std::string& v) { const std::string s = lower(v); return s == "1" || s == "true" || s == "yes" || s == "on"; }
inline float number(const std::string& v, float lo, float hi, float def) {
  char* end = nullptr; const float f = std::strtof(v.c_str(), &end);
  return (end && end != v.c_str() && std::isfinite(f)) ? std::min(hi, std::max(lo, f)) : def;
}

// Applies one "key=value" line to the config. Returns false for lines it does not recognise (ignored, so a newer launcher cannot
// break an older runtime; the loader reports them).
inline bool apply_line(MapConfig& m, const std::string& raw) {
  const std::string line = trim(raw.substr(0, raw.find('#')));
  if (line.empty()) return true;
  const size_t eq = line.find('=');
  if (eq == std::string::npos) return false;
  const std::string key = lower(trim(line.substr(0, eq))), value = trim(line.substr(eq + 1));
  WiiInput w;
  if (wii_by_name(key, w)) {
    uint32_t mask = 0; uint8_t mouse = 0; std::vector<std::string> keys; size_t pos = 0;
    while (pos <= value.size()) {
      size_t comma = value.find(',', pos); if (comma == std::string::npos) comma = value.size();
      const std::string token = trim(value.substr(pos, comma - pos)), head = lower(token.substr(0, 6));
      PadInput p; uint8_t b;
      if (head.rfind("key:", 0) == 0) { const std::string k = trim(token.substr(4)); if (!k.empty()) keys.push_back(k); }
      else if (head == "mouse:") { if (mouse_by_name(trim(token.substr(6)), b)) mouse |= b; }
      else if (pad_by_name(token, p)) mask |= bit(p);
      pos = comma + 1;
    }
    m.bind[w] = mask;   // an empty value deliberately unbinds the input
    m.keys[w] = keys; m.mouse[w] = mouse;
    return true;
  }
  if (key == "pointer") { const auto v = lower(value); m.pointer = v == "gyro" ? Pointer::Gyro : v == "both" ? Pointer::Both : v == "none" ? Pointer::None : Pointer::Stick; }
  else if (key == "nunchuk_stick") m.nunchuk_right_stick = lower(value) == "right";
  else if (key == "pointer_speed") m.pointer_speed = number(value, 0.1f, 5.f, 1.f);
  else if (key == "gyro_sensitivity") m.gyro_sens = number(value, 0.1f, 5.f, 1.f);
  else if (key == "gyro_invert_x") m.gyro_inv_x = truthy(value);
  else if (key == "gyro_invert_y") m.gyro_inv_y = truthy(value);
  else if (key == "motion") m.motion = truthy(value);
  else if (key == "motion_target") { const auto v = lower(value); m.motion_target = v == "remote" ? MT_REMOTE : v == "nunchuk" ? MT_NUNCHUK : MT_BOTH; }
  else if (key == "motion_sensitivity") m.motion_sens = number(value, 0.1f, 4.f, 1.f);
  else if (key == "motion_invert_x") m.motion_inv_x = truthy(value);
  else if (key == "motion_invert_y") m.motion_inv_y = truthy(value);
  else return false;
  return true;
}

// What the loader applied, for the log line that says whether the launcher's map was actually used.
struct LoadReport { int lines = 0, applied = 0; std::string unknown; };

// Applies the file on top of `m`; returns false when there is no readable file (m is then left unchanged).
inline bool load_map_into(MapConfig& m, const char* path, LoadReport* report = nullptr) {
  if (!path || !*path) return false;
  std::ifstream f(path);
  if (!f) return false;
  std::string line;
  while (std::getline(f, line)) {
    const std::string body = trim(line.substr(0, line.find('#')));
    if (body.empty()) continue;
    if (report) ++report->lines;
    if (apply_line(m, line)) { if (report) ++report->applied; }
    else if (report) { if (!report->unknown.empty()) report->unknown += ", "; report->unknown += body.substr(0, body.find('=')); }
  }
  m.from_file = true;
  return true;
}

inline MapConfig load_map(const char* path, int legacy_mode) {
  MapConfig m = default_map(legacy_mode);
  load_map_into(m, path);
  return m;
}

struct Vec3 { float x = 0, y = 0, z = 0; };

// Per-controller state that has to persist between polls.
struct MapperState {
  bool primed = false;
  Vec3 gravity{0, 0, 1};                 // smoothed accelerometer reading (g), points up in the controller frame
  float shake_remote_t = -1, shake_nunchuk_t = -1;   // seconds since the burst began, <0 when idle
  bool prev_shake_remote = false, prev_shake_nunchuk = false, prev_recenter = false;
};

struct MapperOutput {
  uint32_t buttons = 0;
  float ir_dx = 0, ir_dy = 0;            // pointer movement to add (normalised screen units)
  bool recenter = false;
  int8_t stick_x = 0, stick_y = 0;       // Nunchuk stick
  Vec3 remote_accel{0, 0, 1}, nunchuk_accel{0, 0, 1};   // g
};

constexpr float kGravity = 9.80665f;
constexpr float kShakeSeconds = 0.35f, kShakeHalfPeriod = 0.05f;

inline bool pressed(const RawPad& r, uint32_t mask) {
  for (int i = 0; i < P_COUNT; ++i) if ((mask & (1u << i)) && r.down[i]) return true;
  return false;
}
inline float clampf(float v, float lo, float hi) { return std::min(hi, std::max(lo, v)); }

// `dt` is the time since the previous poll in seconds.
inline MapperOutput map_pad(const RawPad& r, const MapConfig& m, MapperState& s, float dt) {
  MapperOutput o;
  dt = clampf(dt, 0.f, 0.05f);

  auto held = [&](int w) { return pressed(r, m.bind[w]) || r.keys[w]; };
  for (int w = W_A; w <= W_Z; ++w) if (held(w)) o.buttons |= kWiiMask[w];

  // Nunchuk stick with the existing dead zone; the stick is chosen by the mapping. Keys give full deflection when the stick is idle.
  const float sx = m.nunchuk_right_stick ? r.rx : r.lx, sy = m.nunchuk_right_stick ? r.ry : r.ly;
  if (std::fabs(sx) * 32767.f > 8000.f) o.stick_x = (int8_t)(clampf(sx, -1.f, 1.f) * 127.f);
  if (std::fabs(sy) * 32767.f > 8000.f) o.stick_y = (int8_t)(clampf(-sy, -1.f, 1.f) * 127.f);
  if (o.stick_x == 0) o.stick_x = (int8_t)((r.keys[W_STICK_RIGHT] ? 127 : 0) - (r.keys[W_STICK_LEFT] ? 127 : 0));
  if (o.stick_y == 0) o.stick_y = (int8_t)((r.keys[W_STICK_UP] ? 127 : 0) - (r.keys[W_STICK_DOWN] ? 127 : 0));

  // Gravity estimate (low-pass of the accelerometer), also used to turn gyro rates into world yaw.
  if (r.has_accel) {
    const Vec3 a{r.accel[0] / kGravity, r.accel[1] / kGravity, r.accel[2] / kGravity};
    if (!s.primed) { s.gravity = a; s.primed = true; }
    const float k = clampf(dt / 0.25f, 0.f, 1.f);
    s.gravity = {s.gravity.x + (a.x - s.gravity.x) * k, s.gravity.y + (a.y - s.gravity.y) * k, s.gravity.z + (a.z - s.gravity.z) * k};
  }

  // Pointer.
  if (m.pointer == Pointer::Stick || m.pointer == Pointer::Both) {
    // Unchanged from the fixed mapping: per poll step, right stick, dead zone 8000/32767.
    if (std::fabs(r.rx) * 32767.f > 8000.f) o.ir_dx += r.rx * 0.05f * m.pointer_speed;
    if (std::fabs(r.ry) * 32767.f > 8000.f) o.ir_dy += r.ry * 0.05f * m.pointer_speed;
  }
  if ((m.pointer == Pointer::Gyro || m.pointer == Pointer::Both) && r.has_gyro) {
    // Yaw is the rotation about the world vertical (gravity), so the pointer follows however the controller is held;
    // pitch is the rotation about the controller's own left-right axis. One screen width is about 0.7 rad at sensitivity 1.
    float gx = s.gravity.x, gy = s.gravity.y, gz = s.gravity.z;
    const float len = std::sqrt(gx * gx + gy * gy + gz * gz);
    if (len < 0.3f || !r.has_accel) { gx = 0; gy = 1; gz = 0; } else { gx /= len; gy /= len; gz /= len; }
    const float yaw = r.gyro[0] * gx + r.gyro[1] * gy + r.gyro[2] * gz;   // + = turning left
    const float pitch = r.gyro[0];                                          // + = nose up
    const float scale = 1.43f * m.gyro_sens * dt;
    o.ir_dx += (m.gyro_inv_x ? yaw : -yaw) * scale;
    o.ir_dy += (m.gyro_inv_y ? pitch : -pitch) * scale;
  }
  const bool recenter = held(W_RECENTER);
  o.recenter = recenter && !s.prev_recenter;
  s.prev_recenter = recenter;

  // Shake gestures: a short alternating burst, started by a button edge.
  const bool shake_r = held(W_SHAKE_REMOTE), shake_n = held(W_SHAKE_NUNCHUK);
  if (shake_r && !s.prev_shake_remote) s.shake_remote_t = 0;
  if (shake_n && !s.prev_shake_nunchuk) s.shake_nunchuk_t = 0;
  s.prev_shake_remote = shake_r; s.prev_shake_nunchuk = shake_n;
  auto burst = [&](float& t, Vec3& a) {
    if (t < 0) return;
    const float sign = ((int)(t / kShakeHalfPeriod) & 1) ? -1.f : 1.f;
    a.x += sign * 2.5f; a.y += sign * 1.2f;
    t += dt; if (t >= kShakeSeconds) t = -1;
  };

  // Controller motion -> Wii accelerometers. A Wii Remote at rest reads (0,0,1) g whatever its orientation, so the output is that
  // rest pose plus only the change from the smoothed gravity, scaled by the sensitivity. Holding the pad still gives (0,0,1).
  if (m.motion && r.has_accel) {
    const Vec3 a{r.accel[0] / kGravity, r.accel[1] / kGravity, r.accel[2] / kGravity};
    float dx = (a.x - s.gravity.x) * m.motion_sens, dy = (a.y - s.gravity.y) * m.motion_sens, dz = (a.z - s.gravity.z) * m.motion_sens;
    if (m.motion_inv_x) dx = -dx;
    if (m.motion_inv_y) dy = -dy;
    const Vec3 v{dx, dy, 1.0f + dz};
    if (m.motion_target & MT_REMOTE) o.remote_accel = v;
    if (m.motion_target & MT_NUNCHUK) o.nunchuk_accel = v;
  }
  burst(s.shake_remote_t, o.remote_accel);
  burst(s.shake_nunchuk_t, o.nunchuk_accel);
  for (Vec3* v : {&o.remote_accel, &o.nunchuk_accel}) { v->x = clampf(v->x, -2.9f, 2.9f); v->y = clampf(v->y, -2.9f, 2.9f); v->z = clampf(v->z, -2.9f, 2.9f); }
  return o;
}

}  // namespace nwii::runtime::input
