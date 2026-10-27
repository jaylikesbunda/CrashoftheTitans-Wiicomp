#pragma once
// NATIVE_HOT_REGION_V1 runtime gate.
//
// The generated flatten/promote wrapper calls native_hot_region::Enabled() and
// falls back to the exact baseline whenever the experiment is disabled. The gate
// is false when any diagnostic/rollback environment variable is present (the
// same presence-based convention as runtime-overlays/native_guest_region_stats.h
// and native_linked_region.h) or while a call trace is live:
//   NWII_NO_HOT_REGION, NWII_NO_HOT_REGIONS, NWII_SAMPLE, NWII_PEEK,
//   NWII_STARTUP_PROFILE, NWII_LOOPTRACE, NWII_RESCHED, NWII_WATCH,
//   NWII_WPAD_SCRIPT, NWII_WPAD_LIVE, NWII_LOAD_STATE,
//   nwii::runtime::g_trace_calls.
//
// Re-entry: the wrapper refuses a second entry while active != 0 and runs the
// baseline instead, so a region can never nest. entered/fallbacks/active are
// incremented and decremented by hand (no RAII): an IOS helper can longjmp out
// of a region, which may leave active == 1. From then on every call falls back
// to the baseline until native_hot_region::Reset() runs. That is safe (the
// baseline is always correct), only slower, so no recovery hook is installed in
// the generated dispatch loop yet.
#include <cstdint>
#include <cstdlib>
namespace native_hot_region {
inline uint64_t entered = 0, fallbacks = 0;
inline unsigned active = 0;
inline bool Enabled() {
  static const bool enabled=[](){
    const char* blocked[]={"NWII_NO_HOT_REGION","NWII_NO_HOT_REGIONS","NWII_SAMPLE","NWII_PEEK",
      "NWII_STARTUP_PROFILE","NWII_LOOPTRACE","NWII_RESCHED","NWII_WATCH","NWII_WPAD_SCRIPT",
      "NWII_WPAD_LIVE","NWII_LOAD_STATE"};
    for(auto name:blocked)if(std::getenv(name))return false;
    return true;}();
  return enabled && !nwii::runtime::g_trace_calls;
}
inline bool Enterable() { return Enabled() && active == 0; }
inline void Reset() { active = 0; entered = 0; fallbacks = 0; }
}
