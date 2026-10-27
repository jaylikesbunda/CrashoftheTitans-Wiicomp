#pragma once
// NATIVE_RENDER_QUEUE_V1: counters for the GL render queue, shown in F3 (native_hud_gl.h) and exported in F4. Written by the
// front end (main thread) when it submits, flushes and posts; the GL thread only bumps `executed`. Relaxed atomics: the
// numbers are for display, and nothing else reads them.
#include <atomic>
#include <cstdint>

namespace nwii::runtime::native_render_queue_stats {
inline std::atomic<bool> enabled{false};          // the GL thread was started for this run
inline std::atomic<uint64_t> batches{0};          // drains handed to the GL thread
inline std::atomic<uint64_t> tasks{0};            // frame tasks (composite, present, HUD) handed to the GL thread
inline std::atomic<uint64_t> executed{0};         // batches the GL thread has rendered
inline std::atomic<uint64_t> drains_queued{0};    // batches submitted while an earlier batch was still in flight
inline std::atomic<uint64_t> depth_sum{0};        // in-flight items after each publish (for the average)
inline std::atomic<uint64_t> depth_samples{0};
inline std::atomic<uint64_t> depth_max{0};
inline std::atomic<uint64_t> flush_copy{0};       // flushes before a drain that contains an EFB copy
inline std::atomic<uint64_t> flush_savestate{0};  // flushes before a savestate
inline std::atomic<uint64_t> flush_waits{0};      // flushes that found work in flight (the rest return at once)
inline std::atomic<uint64_t> flush_wait_us{0};    // time spent in those waits
}
