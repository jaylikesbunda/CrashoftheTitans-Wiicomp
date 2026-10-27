"""Wake the host loop immediately for on-demand GX drains with a condition variable (applies on top of fix_gx_idle_drain.py).

Polling with sleep_for(<=1 ms) gave Windows-timer-granularity latency; a 250 us spin burned a core. The CPU thread now
notifies a condition variable when it requests a drain, and the host loop waits on it (2 ms slices) until the field
deadline."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
root = PORT / 'build/nwii-full-source/nWiiRuntime/src/'


def edit(rel, pairs, marker):
    p = root / rel
    b = p.read_bytes().decode('utf-8')
    nl = '\r\n' if '\r\n' in b else '\n'
    t = b.replace('\r\n', '\n')
    if marker in t:
        print(rel, 'already staged')
        return
    for old, new in pairs:
        if old not in t:
            raise SystemExit(f'{rel}: context changed: {old[:70]!r}')
        t = t.replace(old, new, 1)
    if '#include <condition_variable>' not in t:
        t = '#include <condition_variable>\n#include <mutex>\n' + t
    p.write_bytes(t.replace('\n', nl).encode('utf-8'))
    print(rel, 'staged')


edit('hle/gx.cpp', [('std::atomic<bool> g_gx_drain_request{false};\n',
                     'std::atomic<bool> g_gx_drain_request{false};\nstd::mutex g_gx_drain_mutex;\nstd::condition_variable g_gx_drain_cv;\n')],
     'g_gx_drain_cv')
edit('hle/ios.cpp', [
    ('extern std::atomic<bool> g_gx_drain_request;\n', 'extern std::atomic<bool> g_gx_drain_request;\nextern std::condition_variable g_gx_drain_cv;\n'),
    ('      g_gx_drain_request.store(true, std::memory_order_relaxed); }',
     '      { g_gx_drain_request.store(true, std::memory_order_release); g_gx_drain_cv.notify_one(); } }')], 'g_gx_drain_cv')
edit('core/main.cpp', [
    ('namespace nwii::runtime { extern std::atomic<bool> g_gx_drain_request; }\n',
     'namespace nwii::runtime { extern std::atomic<bool> g_gx_drain_request; extern std::mutex g_gx_drain_mutex; extern std::condition_variable g_gx_drain_cv; }\n'),
    ('      const auto remaining = std::chrono::duration_cast<std::chrono::microseconds>(field_deadline - wait_now);\n'
     '      std::this_thread::sleep_for(std::min(remaining, std::chrono::microseconds(1000)));',
     '      std::unique_lock<std::mutex> drain_lock(nwii::runtime::g_gx_drain_mutex);\n'
     '      nwii::runtime::g_gx_drain_cv.wait_until(drain_lock, std::min(field_deadline, wait_now + std::chrono::milliseconds(2)),\n'
     '                                              [] { return nwii::runtime::g_gx_drain_request.load(std::memory_order_acquire); });'),
    ('g_gx_drain_request.exchange(false, std::memory_order_relaxed)', 'g_gx_drain_request.exchange(false, std::memory_order_acq_rel)')],
     'g_gx_drain_cv')
