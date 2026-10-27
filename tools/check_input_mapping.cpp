// Standalone check of runtime-overlays/input_mapping.h (no SDL):
//   g++ -std=c++17 -I runtime-overlays tools/check_input_mapping.cpp -o check_input_mapping && ./check_input_mapping
#include "input_mapping.h"
#include <cstdio>
#include <cstring>

using namespace nwii::runtime::input;
static int failures = 0, checks = 0;
#define CHECK(cond) do { ++checks; if (!(cond)) { ++failures; std::printf("FAIL line %d: %s\n", __LINE__, #cond); } } while (0)
static bool near(float a, float b, float eps = 1e-3f) { return std::fabs(a - b) <= eps; }

static MapperOutput one(const RawPad& r, const MapConfig& m, MapperState& s, float dt = 0.016f) { return map_pad(r, m, s, dt); }
static RawPad with(PadInput p) { RawPad r; r.down[p] = true; return r; }

int main() {
  // 1. defaults reproduce the previous fixed mapping
  {
    MapConfig m = default_map(2); MapperState s;
    struct { PadInput p; uint32_t want; } cases[] = {
      {P_SOUTH, 0x0800}, {P_EAST, 0x0400}, {P_RTRIGGER, 0x0400}, {P_WEST, 0x0200}, {P_NORTH, 0x0100}, {P_START, 0x0010}, {P_BACK, 0x1000},
      {P_GUIDE, 0x8000}, {P_DUP, 0x0008}, {P_DDOWN, 0x0004}, {P_DLEFT, 0x0001}, {P_DRIGHT, 0x0002}, {P_LSHOULDER, 0x4000},
      {P_RSHOULDER, 0x2000}, {P_LTRIGGER, 0x2000}};
    for (auto& c : cases) CHECK(one(with(c.p), m, s).buttons == c.want);
    CHECK(default_map(4).pointer == Pointer::Gyro && default_map(2).pointer == Pointer::Stick);
  }
  // 2. remap, multi-binding, unbind, unknown names ignored
  {
    MapConfig m = default_map(2); MapperState s;
    apply_line(m, "c=North  # was L1");
    apply_line(m, "two=");
    apply_line(m, "z=Paddle1, Misc1, Nonsense");
    apply_line(m, "wibble=South");
    CHECK(one(with(P_NORTH), m, s).buttons == 0x4000);
    CHECK(one(with(P_LSHOULDER), m, s).buttons == 0);
    CHECK(one(with(P_PADDLE1), m, s).buttons == 0x2000 && one(with(P_MISC1), m, s).buttons == 0x2000);
    CHECK(one(with(P_RSHOULDER), m, s).buttons == 0);
    RawPad both; both.down[P_SOUTH] = both.down[P_EAST] = true;
    CHECK(one(both, m, s).buttons == (0x0800 | 0x0400));
  }
  // 3. config values
  {
    MapConfig m = default_map(2);
    apply_line(m, "pointer=BOTH"); apply_line(m, "gyro_sensitivity=2.5"); apply_line(m, "gyro_invert_y=yes"); apply_line(m, "motion=on");
    apply_line(m, "motion_target=nunchuk"); apply_line(m, "nunchuk_stick=right"); apply_line(m, "pointer_speed=99");
    CHECK(m.pointer == Pointer::Both && near(m.gyro_sens, 2.5f) && m.gyro_inv_y && m.motion && m.motion_target == MT_NUNCHUK);
    CHECK(m.nunchuk_right_stick && near(m.pointer_speed, 5.f));
    CHECK(!load_map("definitely/not/a/file.txt", 2).from_file);
  }
  // 4. gyro pointer signs, in both ways of holding the controller
  {
    MapConfig m = default_map(2); m.pointer = Pointer::Gyro;
    const float dt = 0.01f;
    MapperState s; RawPad upright; upright.has_gyro = upright.has_accel = true; upright.accel[1] = kGravity;   // held upright facing the screen
    upright.gyro[1] = 1.0f;                                                                               // turning left
    auto o = one(upright, m, s, dt);
    CHECK(o.ir_dx < 0 && near(o.ir_dx, -1.43f * dt, 1e-4f)); CHECK(near(o.ir_dy, 0));
    MapperState s2; RawPad flat; flat.has_gyro = flat.has_accel = true; flat.accel[2] = kGravity;           // lying flat, like a Wii Remote
    flat.gyro[2] = 1.0f;                                                                                  // turning left about the vertical
    o = one(flat, m, s2, dt);
    CHECK(o.ir_dx < 0 && near(o.ir_dx, -1.43f * dt, 1e-4f));
    MapperState s3; RawPad pitch = flat; pitch.gyro[2] = 0; pitch.gyro[0] = 1.0f;                           // nose up -> pointer up
    o = one(pitch, m, s3, dt); CHECK(o.ir_dy < 0 && near(o.ir_dx, 0));
    m.gyro_inv_x = true; MapperState s4; o = one(flat, m, s4, dt); CHECK(o.ir_dx > 0);
    m.pointer = Pointer::None; MapperState s5; o = one(flat, m, s5, dt); CHECK(near(o.ir_dx, 0));
  }
  // 5. stick pointer keeps the old per-poll step
  {
    MapConfig m = default_map(2); MapperState s; RawPad r; r.rx = 1.0f; r.ry = 0.1f;
    auto o = one(r, m, s); CHECK(near(o.ir_dx, 0.05f)); CHECK(near(o.ir_dy, 0));            // 0.1 is inside the dead zone
  }
  // 6. recentre fires once per press
  {
    MapConfig m = default_map(2); m.bind[W_RECENTER] = bit(P_LSTICK); MapperState s;
    RawPad r = with(P_LSTICK);
    CHECK(one(r, m, s).recenter); CHECK(!one(r, m, s).recenter); CHECK(!one(RawPad(), m, s).recenter); CHECK(one(r, m, s).recenter);
  }
  // 7. Nunchuk stick: dead zone and stick choice
  {
    MapConfig m = default_map(2); MapperState s; RawPad r; r.lx = 0.9f; r.ly = -0.5f; r.rx = -1.f;
    auto o = one(r, m, s); CHECK(o.stick_x == 114 && o.stick_y == 63);
    m.nunchuk_right_stick = true; o = one(r, m, s); CHECK(o.stick_x == -127 && o.stick_y == 0);
    RawPad small; small.lx = 0.1f; CHECK(one(small, default_map(2), s).stick_x == 0);
  }
  // 8. shake bursts: start on press, alternate, end, only the chosen device
  {
    MapConfig m = default_map(2); m.bind[W_SHAKE_NUNCHUK] = bit(P_RSTICK); MapperState s; RawPad r = with(P_RSTICK);
    auto o = one(r, m, s, 0.016f);
    CHECK(o.nunchuk_accel.x > 1.0f && near(o.remote_accel.x, 0));                           // positive half first
    bool saw_negative = false, ended = false; float t = 0.016f;
    for (int i = 0; i < 40; ++i) { o = one(r, m, s, 0.016f); t += 0.016f; if (o.nunchuk_accel.x < -1.0f) saw_negative = true; if (t > 0.5f && near(o.nunchuk_accel.x, 0)) ended = true; }
    CHECK(saw_negative); CHECK(ended);                                                       // one burst per press, held button does not retrigger
    CHECK(near(o.nunchuk_accel.z, 1.0f));
  }
  // 9. motion passthrough
  {
    MapConfig m = default_map(2); m.motion = true; m.motion_target = MT_REMOTE; MapperState s;
    RawPad r; r.has_accel = true; r.accel[2] = kGravity;
    auto o = one(r, m, s); CHECK(near(o.remote_accel.z, 1.0f) && near(o.remote_accel.x, 0) && near(o.nunchuk_accel.z, 1.0f));
    for (int i = 0; i < 30; ++i) one(r, m, s);                                               // let gravity settle
    r.accel[0] = 2.0f * kGravity;                                                            // a sudden push
    o = one(r, m, s); CHECK(o.remote_accel.x > 1.5f && near(o.nunchuk_accel.x, 0));
    m.motion_sens = 0.5f; MapperState s2; for (int i = 0; i < 30; ++i) { RawPad rest; rest.has_accel = true; rest.accel[2] = kGravity; one(rest, m, s2); }
    o = one(r, m, s2); CHECK(o.remote_accel.x > 0.8f && o.remote_accel.x < 1.2f);          // half the push
    m.motion_inv_x = true; MapperState s3;
    for (int i = 0; i < 30; ++i) { RawPad rest; rest.has_accel = true; rest.accel[2] = kGravity; one(rest, m, s3); }   // settle first
    o = one(r, m, s3); CHECK(o.remote_accel.x < 0);
    m.motion = false; MapperState s4; o = one(r, m, s4); CHECK(near(o.remote_accel.x, 0) && near(o.remote_accel.z, 1.0f));
  }
  // 10. a still pad reads the Wii rest pose (0,0,1) whatever its orientation (regression: it used to report its own gravity)
  {
    const float poses[][3] = {{0, kGravity, 0}, {0, 0, kGravity}, {kGravity, 0, 0}, {0, -kGravity, 0}, {0.6f * kGravity, 0.8f * kGravity, 0}};
    for (const auto& pose : poses) {
      MapConfig m = default_map(2); m.motion = true; m.motion_target = MT_BOTH; MapperState s;
      RawPad r; r.has_accel = true; r.accel[0] = pose[0]; r.accel[1] = pose[1]; r.accel[2] = pose[2];
      MapperOutput o; for (int i = 0; i < 60; ++i) o = one(r, m, s);                          // settle gravity
      CHECK(near(o.remote_accel.x, 0, 0.01f) && near(o.remote_accel.y, 0, 0.01f) && near(o.remote_accel.z, 1.0f, 0.01f));
      CHECK(near(o.nunchuk_accel.x, 0, 0.01f) && near(o.nunchuk_accel.y, 0, 0.01f) && near(o.nunchuk_accel.z, 1.0f, 0.01f));
    }
  }
  // 11. the loader reports what it applied and which keys it ignored
  {
    const char* path = "check_input_mapping_report.txt";
    { std::ofstream f(path); f << "# comment\nc=LeftStick\nshake_remote=LeftTrigger\npointer=gyro\nbogus=1\nnot a line\n\n"; }
    MapConfig m = default_map(2); LoadReport rep;
    CHECK(load_map_into(m, path, &rep));
    CHECK(rep.lines == 5 && rep.applied == 3);
    CHECK(rep.unknown == "bogus, not a line");
    CHECK(m.pointer == Pointer::Gyro && m.bind[W_C] == bit(P_LSTICK));
    std::remove(path);
  }
  // keyboard and mouse bindings: parsed next to controller inputs, replaced as a whole by a line, and fed through RawPad::keys
  {
    MapConfig m = default_map(6); MapperState s;
    CHECK(m.keys[W_A].size() == 1 && m.keys[W_A][0] == "Space" && m.mouse[W_A] == M_LEFT && m.mouse[W_B] == M_RIGHT);
    CHECK(m.keys[W_STICK_UP][0] == "W" && m.keys[W_Z][0] == "Left Shift");
    apply_line(m, "a=South, Key:Left Ctrl, key:J, Mouse:X1, mouse:bogus");
    CHECK(m.bind[W_A] == bit(P_SOUTH) && m.keys[W_A].size() == 2 && m.keys[W_A][0] == "Left Ctrl" && m.keys[W_A][1] == "J" && m.mouse[W_A] == M_X1);
    apply_line(m, "b=North");
    CHECK(m.keys[W_B].empty() && m.mouse[W_B] == 0);
    RawPad r; r.keys[W_A] = true; r.keys[W_STICK_LEFT] = true; r.keys[W_STICK_UP] = true;
    MapperOutput o = one(r, m, s);
    CHECK(o.buttons == 0x0800 && o.stick_x == -127 && o.stick_y == 127);
    RawPad both; both.lx = 0.9f; both.keys[W_STICK_RIGHT] = true;
    CHECK(one(both, m, s).stick_x > 100);   // a moved stick wins over keys
    RawPad shake; shake.keys[W_SHAKE_REMOTE] = true;
    CHECK(std::fabs(one(shake, m, s).remote_accel.x) > 1.f);
  }
  std::printf("%d checks, %d failed\n", checks, failures);
  return failures ? 1 : 0;
}
