#pragma once
// NATIVE_CAMERA_GLIDE_V1: vertical-only glide for the volume camera update.
//
// Call site: func_80045F18() immediately after func_80047824() (the volume
// camera update) returns, before the camera transform is consumed.  The helper
// only ever writes the camera world Y (+0x78) and look-at Y (+0x84); X/Z stay
// byte-identical.  A vertical step between consecutive guest frames (a ledge or
// stair, not a follow) is spread over NWII_CAMERA_GLIDE_MS with a smoothstep.
//
// Offline testing: Step() is pure and has no MMU dependency.  Apply() is the
// only guest-memory adapter.  The configuration is read once per process; the
// helper is a no-op unless NWII_CAMERA_GLIDE_MS is set.
#include <cmath>
#include <cstdint>
#include <cstdlib>

#if __has_include("runtime/cpu_context.h")
#include "runtime/cpu_context.h"
#define NWII_CAMERA_GLIDE_HAS_CONTEXT 1
#else
struct CPUContext;  // Step() remains usable when only the guest context is absent.
#define NWII_CAMERA_GLIDE_HAS_CONTEXT 0
#endif

namespace native_camera_glide {

#if NWII_CAMERA_GLIDE_HAS_CONTEXT
using CPUContext = nwii::runtime::CPUContext;
#endif

// Snapshot of the fields the glide reads.  Offsets are documented against
// CCameraVolumeBehaviour::this in Apply().
struct CameraFrame {
  float position[3]{0.0f, 0.0f, 0.0f};  // +0x74 X, +0x78 Y, +0x7c Z
  float look_at[3]{0.0f, 0.0f, 0.0f};   // +0x80 X, +0x84 Y, +0x88 Z
  uint32_t state = 0;                    // +0xa8
  int32_t volume_index = -1;             // +0xfc
  uint32_t interest_count = 0;           // +0x1d4
  bool jump_lock = false;                // primary node->source+0x3c bit 7
};

struct Limits {
  float min_y = 0.3f;
  float max_y = 8.0f;
  float max_xz = 0.3f;
  float duration = 0.2f;
};

// One camera's glide state.  The table in Apply() keeps these keyed by the
// guest CCameraVolumeBehaviour pointer (the guest is single-threaded).
struct Glide {
  bool have_previous = false;
  bool armed = false;
  float timer = 0.0f;
  float duration = 0.0f;
  float start_y = 0.0f;
  float start_look_y = 0.0f;
  CameraFrame previous{};
};

inline float Clamp01(float t) { return t < 0.0f ? 0.0f : (t > 1.0f ? 1.0f : t); }
inline float Smoothstep(float t) {
  t = Clamp01(t);
  return t * t * (3.0f - 2.0f * t);
}
inline float Abs(float v) { return v < 0.0f ? -v : v; }

// Pure glide.  Returns true when the output Y values were blended (including
// the final exact frame), false when the input passes through unchanged.
inline bool Step(Glide& g, float dt, const CameraFrame& in, CameraFrame& out,
                 const Limits& limits) {
  out = in;

  // First frame for this camera: learn where the guest camera is, never glide.
  if (!g.have_previous) {
    g.previous = in;
    g.have_previous = true;
    g.armed = false;
    return false;
  }

  // Hard gates are re-checked every frame, including while armed: a jump lock
  // or a lost camera volume must snap immediately rather than finish the glide.
  const bool open = in.state == 2 && in.volume_index >= 0 &&
                    in.interest_count == 1 && dt > 0.0f && dt < 0.1f &&
                    !in.jump_lock;
  if (!open) {
    g.armed = false;
    g.previous = in;
    return false;
  }

  if (!g.armed) {
    // Only a step between frames arms the glide.  A steady follow has no
    // vertical delta; a teleport/fall outside the limits passes through.
    const float dy = Abs(in.position[1] - g.previous.position[1]);
    const float dx = Abs(in.position[0] - g.previous.position[0]);
    const float dz = Abs(in.position[2] - g.previous.position[2]);
    if (dy < limits.min_y || dy > limits.max_y || dx + dz > limits.max_xz) {
      g.previous = in;
      return false;
    }
    g.armed = true;
    g.timer = 0.0f;
    g.duration = limits.duration > 0.0f ? limits.duration : 0.0f;
    g.start_y = g.previous.position[1];
    g.start_look_y = g.previous.look_at[1];
  }

  // While armed, blend toward this frame's current values with smoothstep.  The
  // final frame snaps to the guest's exact values so no rounding is left behind.
  g.timer += dt;
  const float t = g.duration > 0.0f ? Clamp01(g.timer / g.duration) : 1.0f;
  if (t >= 1.0f) {
    out.position[1] = in.position[1];
    out.look_at[1] = in.look_at[1];
    g.armed = false;
  } else {
    const float s = Smoothstep(t);
    out.position[1] = g.start_y + (in.position[1] - g.start_y) * s;
    out.look_at[1] = g.start_look_y + (in.look_at[1] - g.start_look_y) * s;
  }
  g.previous = in;  // Always track the guest's raw target, never the blend.
  return true;
}

// ---------------------------------------------------------------------------
// Configuration, read once.  NWII_CAMERA_GLIDE_MS is both the enable switch
// and the duration; NWII_NO_CAMERA_GLIDE is the kill switch.  With neither set
// the helper is a strict no-op.
// ---------------------------------------------------------------------------
struct Config {
  bool enabled = false;
  Limits limits{};
};

inline float EnvFloat(const char* name, float fallback) {
  const char* text = std::getenv(name);
  if (!text || !*text) return fallback;
  char* end = nullptr;
  const double value = std::strtod(text, &end);
  if (end == text || *end || !std::isfinite(value)) return fallback;
  return static_cast<float>(value);
}

inline Config ReadConfig() {
  Config config;
  const char* milliseconds = std::getenv("NWII_CAMERA_GLIDE_MS");
  if (!milliseconds || std::getenv("NWII_NO_CAMERA_GLIDE")) return config;

  double duration_ms = 200.0;
  if (*milliseconds) {
    char* end = nullptr;
    const double value = std::strtod(milliseconds, &end);
    if (end != milliseconds && !*end && std::isfinite(value)) duration_ms = value;
  }
  if (!(duration_ms > 0.0)) return config;  // 0/negative disables the glide.
  config.limits.duration = static_cast<float>(duration_ms / 1000.0);
  config.limits.min_y = EnvFloat("NWII_CAMERA_GLIDE_MIN_Y", 0.3f);
  config.limits.max_y = EnvFloat("NWII_CAMERA_GLIDE_MAX_Y", 8.0f);
  config.limits.max_xz = EnvFloat("NWII_CAMERA_GLIDE_MAX_XZ", 0.3f);
  if (!(config.limits.min_y >= 0.0f)) config.limits.min_y = 0.3f;
  if (!(config.limits.max_y >= config.limits.min_y)) config.limits.max_y = 8.0f;
  if (!(config.limits.max_xz >= 0.0f)) config.limits.max_xz = 0.3f;
  config.enabled = true;
  return config;
}

inline const Config& Configuration() {
  static const Config config = ReadConfig();
  return config;
}
inline bool Enabled() { return Configuration().enabled; }

// ---------------------------------------------------------------------------
// Fixed eight-entry state table, keyed by the guest self pointer.
// ---------------------------------------------------------------------------
struct Entry {
  uint32_t self = 0;
  bool used = false;
  uint64_t stamp = 0;
  uint64_t epoch = 0;
  Glide glide{};
};

inline Entry* Table() {
  static Entry entries[8];
  return entries;
}
inline uint64_t& Clock() {
  static uint64_t value = 0;
  return value;
}
inline uint64_t& EpochWord() {
  static uint64_t value = 1;
  return value;
}
inline uint64_t Epoch() { return EpochWord(); }

// Save/load follow-up: call BumpEpoch() from the native save-state load hook
// (native_ss) so a restored camera starts with a fresh previous/armed state.
// No hook is installed by this stage; Reset() is provided for tests and boot.
inline void BumpEpoch() { ++EpochWord(); }
inline void Reset() {
  ++EpochWord();
  Entry* table = Table();
  for (unsigned i = 0; i < 8; ++i) table[i] = Entry{};
}

inline Glide& Acquire(uint32_t self) {
  Entry* table = Table();
  Entry* replacement = nullptr;
  for (unsigned i = 0; i < 8; ++i) {
    Entry& entry = table[i];
    if (entry.used && entry.self == self) {
      if (entry.epoch != EpochWord()) {
        entry.glide = Glide{};
        entry.epoch = EpochWord();
      }
      entry.stamp = ++Clock();
      return entry.glide;
    }
    if (!entry.used) {
      if (!replacement) replacement = &entry;
    } else if (!replacement || entry.stamp < replacement->stamp) {
      replacement = &entry;
    }
  }
  replacement->self = self;
  replacement->used = true;
  replacement->stamp = ++Clock();
  replacement->epoch = EpochWord();
  replacement->glide = Glide{};
  return replacement->glide;
}

// ---------------------------------------------------------------------------
// Guest adapter: read only, then write only the two Y floats that Step chose.
// ---------------------------------------------------------------------------
template <class Context>
inline bool ReadJumpLock(Context& ctx, uint32_t self, uint32_t interest_count) {
  if (interest_count != 1) return false;  // the count gate rejects this frame
  const uint32_t node = ctx.mmu.read32(self + 0xc4u);
  if (!node) return true;                 // cannot prove the lock is clear
  const uint32_t source = ctx.mmu.read32(node + 0x04u);
  if (!source) return true;
  return (ctx.mmu.read8(source + 0x3cu) & 0x80u) != 0;
}

template <class Context>
inline void ApplyImpl(Context& ctx, uint32_t self, float dt) {
  const Config& config = Configuration();
  if (!config.enabled || !self) return;

  CameraFrame in;
  in.position[0] = ctx.mmu.read_f32(self + 0x74u);
  in.position[1] = ctx.mmu.read_f32(self + 0x78u);
  in.position[2] = ctx.mmu.read_f32(self + 0x7cu);
  in.look_at[0] = ctx.mmu.read_f32(self + 0x80u);
  in.look_at[1] = ctx.mmu.read_f32(self + 0x84u);
  in.look_at[2] = ctx.mmu.read_f32(self + 0x88u);
  in.state = ctx.mmu.read32(self + 0xa8u);
  in.volume_index = static_cast<int32_t>(ctx.mmu.read32(self + 0xfcu));
  in.interest_count = ctx.mmu.read32(self + 0x1d4u);
  in.jump_lock = ReadJumpLock(ctx, self, in.interest_count);

  CameraFrame out;
  if (Step(Acquire(self), dt, in, out, config.limits)) {
    ctx.mmu.write_f32(self + 0x78u, out.position[1]);
    ctx.mmu.write_f32(self + 0x84u, out.look_at[1]);
  }
}

#if NWII_CAMERA_GLIDE_HAS_CONTEXT
inline void Apply(CPUContext& ctx, uint32_t self, float dt) {
  ApplyImpl(ctx, self, dt);
}
#endif

}  // namespace native_camera_glide

#undef NWII_CAMERA_GLIDE_HAS_CONTEXT
