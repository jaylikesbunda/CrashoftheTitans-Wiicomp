"""Stop paying a mutex lock and an atomic add for every byte the guest writes to the GX pipe.

Every GX_WGPIPE_Write8/16/32/F64 locked g_fifo_mutex and wgp_push did an atomic
fetch_add per byte before push_back into the shared vector. A frame writes
hundreds of kilobytes, all on the guest CPU thread.

Now the CPU thread appends to a private buffer with no locking and publishes it
to the shared FIFO (one lock, one counter update) every 4 KiB and, importantly,
at every guest poll (process_pending_callbacks) before the idle-drain check, so
the drain thread sees exactly the bytes it used to see whenever the guest could
be waiting on the GPU. Drain scheduling itself is unchanged.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
src = port / 'build/nwii-full-source/nWiiRuntime/src/hle'
marker = 'NATIVE_GX_PRIVATE_FIFO_V1'

p = src / 'gx.cpp'
t = p.read_text()
if marker not in t:
    anchor = 'static inline void wgp_push(uint8_t b) {'
    assert t.count(anchor) == 1
    new = r'''// NATIVE_GX_PRIVATE_FIFO_V1: guest-CPU-thread-only staging buffer.
static std::vector<uint8_t> g_cpu_fifo;
void native_gx_publish() {
    if (g_cpu_fifo.empty()) return;
    {
        std::lock_guard<std::mutex> lk(g_fifo_mutex);
        g_hw_fifo.insert(g_hw_fifo.end(), g_cpu_fifo.begin(), g_cpu_fifo.end());
        g_gx_pending_bytes.fetch_add(g_cpu_fifo.size(), std::memory_order_relaxed);
    }
    g_cpu_fifo.clear();
}
'''
    t = t.replace(anchor, new + anchor, 1)
    old = '    g_gx_pending_bytes.fetch_add(1, std::memory_order_relaxed);'
    assert t.count(old) == 1
    t = t.replace(old, '', 1)
    old = '    g_hw_fifo.push_back(b);'
    assert t.count(old) == 1
    t = t.replace(old, '    g_cpu_fifo.push_back(b);\n    if (g_cpu_fifo.size() >= 4096) native_gx_publish();', 1)
    lock = '    std::lock_guard<std::mutex> lock(g_fifo_mutex);\n'
    n = 0
    for follow in ['    wgp_push(val);', '    wgp_push(val >> 8);', '    wgp_push(val >> 24);', '    for (int s = 56; s >= 0; s -= 8)']:
        s = lock + follow
        assert t.count(s) == 1, follow
        t = t.replace(s, follow)
        n += 1
    p.write_text(t)

p = src / 'ios.cpp'
t = p.read_text()
if 'NATIVE_GX_PRIVATE_FIFO_V1' not in t:
    old = 'extern std::atomic<size_t> g_gx_pending_bytes;\n'
    assert t.count(old) == 1
    t = t.replace(old, old + 'extern void native_gx_publish(); // NATIVE_GX_PRIVATE_FIFO_V1\n')
if 'native_gx_publish();' not in t:
    old = 'bool process_pending_callbacks(CPUContext &ctx) {'
    assert t.count(old) == 1
    t = t.replace(old, old + '\n  native_gx_publish();', 1)
p.write_text(t)
print('GX pipe writes are staged lock-free and published in batches.')
