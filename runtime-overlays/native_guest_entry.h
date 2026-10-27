#pragma once
#include <cstdlib>
#include <atomic>
#include "runtime/native_hud.h"
namespace nwii::runtime {extern bool g_trace_calls;extern uint32_t g_watch_addr;}
namespace native_guest_entry {
inline std::atomic<uint64_t> fast_calls{0},resume_calls{0},continuation_calls{0},epoch{0};
inline const bool continuations_enabled=!std::getenv("NWII_NO_FAST_CONTINUATIONS");
inline bool ContinuationsEnabled(){return continuations_enabled;}
// NATIVE_ENTRY_FLAG_HOIST_V1: launch-fixed flag hoisted to namespace scope so the
// hot entry gate is one load instead of a function-local-static guard load. The
// value is identical for a fixed launch environment (same pattern as
// NATIVE_POLL_GUARD_OUTLINE_V1).
inline const bool entry_enabled=!std::getenv("NWII_NO_ENTRY_SPECIALIZATION") && !std::getenv("NWII_SAMPLE") && !std::getenv("NWII_STARTUP_PROFILE") && !std::getenv("NWII_PEEK") && !std::getenv("NWII_LOOPTRACE") && !std::getenv("NWII_FP_CONTEXT_PROBE") && !std::getenv("NWII_RUNTIME_DIAG");
inline const bool entry_gate=entry_enabled && !([]{const char* e=std::getenv("NWII_TRACE_CALLS");return e && e[0]=='1';}())
 && ([]{const char* e=std::getenv("NWII_WATCH");const uint32_t w=e?uint32_t(std::strtoul(e,nullptr,16)):0x80597790u;return !w || w==0x80597790u;}());
__attribute__((always_inline)) inline bool Enabled(){return entry_gate;}
inline uint64_t generation=~uint64_t(0),pending[3]{}; // guest execution thread only
// Guest-only accumulation; publish batches to avoid an atomic increment per call.
// Exported counts are lower bounds, short by fewer than 128 calls per category.
// Only the HUD test stays inline; the accumulation is out of line and cold.
__attribute__((noinline,cold)) inline void RecordKindSlow(unsigned kind){
 const auto current=epoch.load(std::memory_order_relaxed);
 if(generation!=current){generation=current;pending[0]=pending[1]=pending[2]=0;}
 auto& value=pending[kind];if(++value==128){(kind==0?fast_calls:kind==1?resume_calls:continuation_calls).fetch_add(value,std::memory_order_relaxed);value=0;}
}
__attribute__((always_inline)) inline void RecordKind(unsigned kind){
 if(!native_hud::on.load(std::memory_order_relaxed))return;
 RecordKindSlow(kind);
}
__attribute__((always_inline)) inline void Record(bool fast){RecordKind(fast?0:1);}
__attribute__((always_inline)) inline void RecordContinuation(){RecordKind(2);}
inline void Reset(){fast_calls=0;resume_calls=0;continuation_calls=0;epoch.fetch_add(1,std::memory_order_relaxed);}
}
