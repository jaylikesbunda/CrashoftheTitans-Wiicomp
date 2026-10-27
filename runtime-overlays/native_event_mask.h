#pragma once
#include <atomic>
#include <cstdint>
// NATIVE_EVENT_MASK_V1: one cache line that says "some producer may have made a
// callback / interrupt due since the last real poll".
//
// The poll fast path used to read five independent producer locations on every
// real poll (g_ipc_interrupt_delay, hw::g_di_interrupt_delay, ctx.vblank_pending,
// hw::pi_intsr, hw::pi_intmr). The mask replaces the three that are cross-thread
// atomics with a single word, read once per call.
//
// Producer -> bit table (every producer that can make a poll non-trivial):
//
//  bit                 producer                                          raiser / lowerer
//  ------------------  ------------------------------------------------  ------------------------------------------
//  kIpcDelay   (0)     hw_ipc.cpp arms g_ipc_interrupt_delay              raise on arm; ios.cpp poll lowers at 0
//  kVBlank     (2)     host VI thread (main.cpp) and the EventScheduler  raise where vblank_pending is set;
//                      VI callback (ios.cpp) set ctx.vblank_pending       ios.cpp poll lowers when it consumes it
//  kPiIrq      (3)     trigger_pi_interrupt (VI/PE/DSP/AI/DI/IPC) and    raise on an unmasked 0->1 transition of
//                      the PI INTMR writer                              pi_intsr / on an INTMR enable; lowered by
//                                                                        the poll once (pi_intsr & pi_intmr) == 0
//
// Producers that deliberately do NOT get a bit, because the poll already reads
// the exact value at the point where it decides:
//
//  hw::g_di_interrupt_delay  has no arming site in the runtime (only the zero
//                        initializer and the di_tick() countdown exist), so the
//                        fast path keeps its exact `== 0` test on the plain int
//                        instead of a bit that nothing raises.
//  queue_callback        pending_cb_count is re-read in the fast path: it is the
//                        dispatch decision itself, and a queued callback is not a
//                        "maybe" event. queue_callback already raises the
//                        native_idle::attention latch through notify().
//  DEC expiry            ctx.dec_expired() is only evaluated when the 16-poll
//                        clock throttle says `due`, which already forces the
//                        slow path; the mask cannot widen that cadence.
//  EventScheduler        advanced only when `due`, same argument.
//  GX staged bytes       read only on the idle PC (0x803BBFA0), where the value
//                        itself is required; it is not a callback source.
//  WPAD phase            native_wpad_poll runs on the fast path itself.
//  native_idle::notify   keeps its own attention latch (native_idle::attention),
//                        which native_poll_due() already tests at every site.
//
// Ordering: a producer raises its bit with release ordering *after* publishing
// the value the poll reads, and the poll loads the mask with acquire ordering
// before it looks at anything else. So "mask & kSlowMask == 0" observed by the
// poll means the three conditions were false as of the mask load.
//
// A raise that races a lower can be lost (the same bounded-delay property the
// existing native_idle::attention latch already relies on): the credit
// countdown in native_poll_due() re-runs the slow path within NWII_POLL_CREDIT
// poll points, and the slow path re-derives every condition from the raw
// registers. The IPC rule is not affected by that bound: kIpcDelay stays set for
// the whole countdown, so a poll runs at every site while a delay is armed.
namespace nwii::runtime::native_events {

enum : uint32_t {
  kIpcDelay = 1u << 0,
  kVBlank   = 1u << 2,
  kPiIrq    = 1u << 3,
  // The bits that gate the poll fast path.
  kSlowMask = kIpcDelay | kVBlank | kPiIrq,
};

inline std::atomic<uint32_t> mask{0};

inline void raise(uint32_t bits) { mask.fetch_or(bits, std::memory_order_release); }
inline void lower(uint32_t bits) { mask.fetch_and(~bits, std::memory_order_release); }
inline bool any(uint32_t bits) { return (mask.load(std::memory_order_relaxed) & bits) != 0; }

} // namespace nwii::runtime::native_events
