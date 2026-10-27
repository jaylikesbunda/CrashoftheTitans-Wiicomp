#pragma once
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <mutex>
#include "runtime/native_frame_trace.h"

// Live-selectable wait backend. No guest clock, field cadence or FIFO ordering changes.
namespace nwii::runtime::native_render_wait {
using Clock = std::chrono::steady_clock;
inline bool LegacyDrain() { static const bool legacy = std::getenv("NWII_LEGACY_DRAIN_WAKE") != nullptr; return legacy; }
inline std::atomic<bool>& Selection() {
    static std::atomic<bool> requested{[] {
        const char* mode = std::getenv("NWII_RENDER_WAIT");
        return mode && std::strcmp(mode, "timer") == 0;
    }()};
    return requested;
}
inline bool RequestedTimer() { return Selection().load(std::memory_order_relaxed) && !LegacyDrain(); }
inline void SetRequestedTimer(bool enabled) { Selection().store(enabled, std::memory_order_relaxed); }

// Called on the render thread at field boundaries, never on a GX producer or under
// the drain mutex. Atomic launcher replacement prevents partial file reads.
inline bool ReloadControlFile(const std::filesystem::path& path) {
    std::ifstream file(path, std::ios::binary);
    char data[16]{}; file.read(data, sizeof(data)-1);
    size_t size = size_t(file.gcount());
    if (!size || size == sizeof(data)-1) return false;
    while (size && (data[size-1]=='\n' || data[size-1]=='\r' || data[size-1]==' ' || data[size-1]=='\t')) --size;
    if (size==5 && std::memcmp(data,"timer",5)==0) { SetRequestedTimer(true); return true; }
    if (size==2 && std::memcmp(data,"cv",2)==0) { SetRequestedTimer(false); return true; }
    return false; // Missing, partial or invalid commands retain the current selection.
}
inline void Poll(bool force = false) {
    static const std::filesystem::path path = [] {
        const char* file = std::getenv("NWII_RENDER_WAIT_FILE");
        return file ? std::filesystem::path(reinterpret_cast<const char8_t*>(file)) : std::filesystem::path{};
    }();
    if (path.empty()) return;
    static Clock::time_point next{};
    const auto now = Clock::now();
    if (!force && now < next) return;
    next = now + std::chrono::milliseconds(250);
    ReloadControlFile(path);
}
struct Resources {
    std::atomic<bool> timer_armed{false};
#ifdef _WIN32
    HANDLE event = nullptr, timer = nullptr;
    explicit Resources(bool enabled) {
        if (enabled) {
            // Auto-reset retains a signal issued before the waiter enters the kernel.
            event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
            timer = CreateWaitableTimerExW(nullptr, nullptr, 0x2, TIMER_ALL_ACCESS);
        }
    }
    ~Resources() { if (timer) CloseHandle(timer); if (event) CloseHandle(event); }
    bool ready() const { return event && timer; }
#else
    explicit Resources(bool) {}
    bool ready() const { return false; }
#endif
    Resources(const Resources&) = delete;
    Resources& operator=(const Resources&) = delete;
};
// Keep the handles for the process lifetime, including while CV is selected.
// The waiter arms notification under the publication mutex before unlocking.
// CV mode therefore needs no additional kernel event signal.
inline Resources& State() { static Resources state(true); return state; }
inline void Initialize() { (void)State(); (void)Selection(); (void)LegacyDrain(); Poll(true); } // before producers
inline const char* Mode() { return RequestedTimer() ? State().ready() ? "timer" : "cv (timer unavailable)" : "cv"; }

// Correlation IDs are unique across requests and captures. Raw stages use the existing
// bounded per-thread frame-trace buffers, with no new locks or file I/O in the hot path.
inline uint64_t Stamp() { return native_trace::active.load(std::memory_order_relaxed) ? native_trace::now_us() : 0; }
inline void At(const char* stage, uint64_t stamp, uint64_t id, uint64_t aux = 0) {
    if (stamp && native_trace::active.load(std::memory_order_relaxed))
        native_trace::record({stage, stamp, 0, id, aux, native_trace::frame.load(), 1, 'i'});
}
inline void Mark(const char* stage, uint64_t id, uint64_t aux = 0) { At(stage, Stamp(), id, aux); }
inline std::atomic<uint64_t> next_wait{0}, timer_waits{0}, cv_waits{0}, fallbacks{0};
inline thread_local uint64_t current_wait = 0, consumed_request = 0;
inline thread_local Clock::time_point last_deadline{};
inline void Notify(uint64_t request_id) {
    Mark("gx.pacing.notify", request_id, State().ready() ? 1 : 0);
#ifdef _WIN32
    if (State().timer_armed.load(std::memory_order_relaxed)) SetEvent(State().event);
#endif
}
inline void DrainStart() {
    if (consumed_request) Mark("gx.pacing.drain_start", consumed_request, current_wait);
    consumed_request = current_wait = 0;
}

// Caller owns the drain mutex and has performed its normal park-entry recheck.
// No ResetEvent: a late/stale notification is harmless; resetting could lose a wake.
inline bool Wait(std::unique_lock<std::mutex>& lock, std::condition_variable& cv,
                 const std::atomic<bool>& pending, Clock::time_point field_deadline,
                 Clock::time_point poll_deadline, Resources& resources = State()) {
    const uint64_t begin = Stamp();
    const bool timer = RequestedTimer() && resources.ready();
    last_deadline = timer ? field_deadline : poll_deadline;
    current_wait = begin ? next_wait.fetch_add(1, std::memory_order_relaxed) + 1 : 0;
    const uint64_t deadline = uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(last_deadline.time_since_epoch()).count());
    At("host.pacing.wait_begin", begin, current_wait, deadline);
    Mark("host.pacing.backend", current_wait, timer ? 1 : 0);
    if (pending.load(std::memory_order_acquire)) {
        Mark("host.pacing.pending", current_wait);
        return true;
    }
#ifdef _WIN32
    if (timer) {
        const auto remaining = field_deadline - Clock::now();
        if (remaining <= Clock::duration::zero()) {
            Mark("host.pacing.deadline", current_wait);
            return false;
        }
        LARGE_INTEGER due{};
        // Round down to 100 ns, never deliberately arm beyond the field deadline.
        const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(remaining).count();
        if (ns < 100) { Mark("host.pacing.deadline", current_wait); return false; }
        due.QuadPart = -(ns / 100);
        if (SetWaitableTimer(resources.timer, &due, 0, nullptr, nullptr, FALSE)) {
            HANDLE handles[] = {resources.event, resources.timer};
            resources.timer_armed.store(true,std::memory_order_relaxed);
            lock.unlock();
            const DWORD result = WaitForMultipleObjects(2, handles, FALSE, INFINITE);
            // These are observable user-mode return/lock stages, not OS ready-to-run times.
            Mark("host.pacing.wait_return", current_wait,
                 result == WAIT_OBJECT_0 ? 1 : result == WAIT_OBJECT_0 + 1 ? 2 : 3);
            Mark("host.pacing.lock_begin", current_wait);
            lock.lock();
            resources.timer_armed.store(false,std::memory_order_relaxed);
            Mark("host.pacing.lock_acquired", current_wait);
            CancelWaitableTimer(resources.timer);
            if (result == WAIT_OBJECT_0 || result == WAIT_OBJECT_0 + 1) {
                if (begin) timer_waits.fetch_add(1, std::memory_order_relaxed);
                return pending.load(std::memory_order_acquire);
            }
        }
        if (begin) fallbacks.fetch_add(1, std::memory_order_relaxed);
        Mark("host.pacing.fallback", current_wait,
             uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(poll_deadline.time_since_epoch()).count()));
    }
#endif
    // Original condition-variable algorithm remains the default and the API-failure fallback.
    // Its return already owns the mutex: wake latency and lock wait cannot be separated here.
    last_deadline = poll_deadline;
    if (begin) cv_waits.fetch_add(1, std::memory_order_relaxed);
    const bool requested = cv.wait_until(lock, poll_deadline, [&] { return pending.load(std::memory_order_acquire); });
    Mark("host.pacing.cv_return_locked", current_wait, requested ? 1 : 0);
    return requested;
}
}
