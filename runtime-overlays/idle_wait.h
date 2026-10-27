#pragma once
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <algorithm>
#include "runtime/native_hud.h"
#include "runtime/native_event_mask.h" // NATIVE_EVENT_MASK_V1
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

namespace nwii::runtime::native_idle {
inline thread_local uint64_t waits = 0;
// NATIVE_POLL_CREDIT_V1: preserve the existing producer attention latch.
inline std::atomic<uint32_t> attention{0};
// Only the guest thread owns its timer; producers merely signal the wake event.
// No guest register, RAM, instruction count or virtual clock is advanced here.
inline uint64_t budget_ticks(uint64_t now, uint64_t next, bool dec_pending,
                             uint64_t dec_origin, uint64_t dec_value, uint64_t freq) {
    uint64_t ticks = freq / 4000; // at most 250 us, also bounds unnotified sources
    if (!ticks) return 0;
    if (next <= now) return 0;
    ticks = std::min(ticks, next - now);
    if (dec_pending) {
        const uint64_t elapsed = now - dec_origin;
        if (elapsed >= dec_value) return 0;
        ticks = std::min(ticks, dec_value - elapsed);
    }
    return ticks;
}
#ifdef _WIN32
struct Wake {
    HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    ~Wake() {if (event) CloseHandle(event);}
};
inline Wake& wake() {static Wake s; return s;}
inline void notify() {attention.store(1,std::memory_order_relaxed);if (wake().event) SetEvent(wake().event);}
struct Timer {
    // Fail open on older systems; a coarse Windows sleep can delay a full field.
    HANDLE timer = CreateWaitableTimerExW(nullptr, nullptr, 0x2, TIMER_ALL_ACCESS);
    ~Timer() {if (timer) CloseHandle(timer);}
};
template<class Pending>
inline bool wait(uint64_t ticks, uint64_t freq, Pending pending) {
    if (!ticks || !freq || pending()) return false;
    static thread_local Timer t;
    auto& w = wake();
    if (!t.timer || !w.event) return false;
    LARGE_INTEGER due{};
    // floor duration to a 100 ns tick, never round past the chosen deadline
    const uint64_t ns100 = ticks * 10000000ull / freq;
    if (!ns100) return false;
    due.QuadPart = -static_cast<LONGLONG>(ns100);
    if (!SetWaitableTimer(t.timer, &due, 0, nullptr, nullptr, FALSE)) return false;
    if (pending()) {CancelWaitableTimer(t.timer); return false;}
    HANDLE handles[] = {w.event, t.timer};
    // A signal arriving between predicate check and wait remains in the event.
    ++waits;
    const uint64_t start=native_hud::on.load(std::memory_order_relaxed)?native_hud::now_us():0;
    const DWORD result=WaitForMultipleObjects(2, handles, FALSE, INFINITE);
    if(start){const uint64_t elapsed=native_hud::now_us()-start,requested=(ns100+9)/10;
      native_hud::record_wait(true,result==WAIT_OBJECT_0?0u:result==WAIT_OBJECT_0+1?1u:2u,elapsed,result==WAIT_OBJECT_0+1 && elapsed>requested?elapsed-requested:0);}
    CancelWaitableTimer(t.timer);
    return true;
}
#else
inline void notify() {attention.store(1,std::memory_order_relaxed);}
template<class Pending> inline bool wait(uint64_t, uint64_t, Pending) {return false;}
#endif
}
