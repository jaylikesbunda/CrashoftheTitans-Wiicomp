"""Straight-line ordinary write-gather path (NWII_NO_GATHER_ORDINARY_FAST=1 disables).

Capture frame-cost-20261006-102126-770700, guest thread, FIFO write path (about 100k scalar writes and
406 KB of GX bytes per frame): native_wgp_bulk 1.09 ms + native_gather_redirect_span 0.85 ms +
wgp_redirect 0.65 ms + GX_WGPIPE_Write32 0.39 ms + pi_fifo_get 0.28 ms self time. With the default watch
set, ordinary FIFO writes return -1 from the redirect classifier (NATIVE_WATCH_ORDINARY_FIFO_PARITY_V1) and
then run wgp_redirect byte probes before the same CPU-private append.

Ordinary FIFO destination (PI base 0 with end below 64 MiB, or PI base equal to the CP base) never touches
guest RAM, so the watch cannot observe it; the predicate below is exactly the one wgp_redirect and
native_gather_redirect_span apply, read straight from the PI/CP registers. Redirected, WPAR, probe and
diagnostic modes keep their original paths. native_wgp_bulk keeps every 4096-byte publish boundary.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime/src'
marker='NATIVE_GATHER_ORDINARY_FAST_V1'
for rel,old,new in (('hw/hw_pi.cpp','static uint32_t pi_fifo_base = 0;\nstatic uint32_t pi_fifo_end = 0;','uint32_t pi_fifo_base = 0; // NATIVE_GATHER_ORDINARY_FAST_V1: read by gx.cpp\nuint32_t pi_fifo_end = 0;'),
                    ('hw/hw_cp.cpp','static uint32_t g_cp_fifo_base = 0;','uint32_t g_cp_fifo_base = 0; // NATIVE_GATHER_ORDINARY_FAST_V1: read by gx.cpp')):
    f=rt/rel;s=f.read_text()
    if marker not in s:
        assert s.count(old)==1,rel;f.write_text(s.replace(old,new,1))
f=rt/'hle/gx.cpp';s=f.read_text()
if marker not in s:
    a=s.index('static inline void native_wgp_bulk(uint64_t value, unsigned bytes) {')
    b=s.index('\nvoid GX_WGPIPE_Write8(',a)
    old=s[a:b]
    new='''namespace hw { extern uint32_t pi_fifo_base, pi_fifo_end, g_cp_fifo_base; } // NATIVE_GATHER_ORDINARY_FAST_V1
static inline void native_wgp_bulk(uint64_t value, unsigned bytes) {
    // NATIVE_GATHER_ORDINARY_FAST_V1: one append when no 4096-byte publish boundary is reached.
    const size_t first = g_cpu_fifo.size();
    if (first + bytes < 4096) {
        if (g_cpu_fifo.capacity() < 4104) g_cpu_fifo.reserve(4104);
        g_cpu_fifo.resize(first + bytes);
        const uint64_t be = __builtin_bswap64(value << (8 * (8 - bytes)));
        std::memcpy(g_cpu_fifo.data() + first, &be, bytes);
        return;
    }
    // A single append when it fits; split exactly where wgp_push published.
    while (bytes) {
        const size_t old = g_cpu_fifo.size();
        const unsigned n = unsigned(std::min<size_t>(bytes, 4096 - old));
        g_cpu_fifo.resize(old + n);
        for (unsigned i = 0; i < n; ++i)
            g_cpu_fifo[old + i] = uint8_t(value >> (8 * (bytes - 1 - i)));
        bytes -= n;
        if (g_cpu_fifo.size() == 4096) native_gx_publish();
    }
}
// Same predicate as wgp_redirect/native_gather_redirect_span: ordinary FIFO never writes guest RAM.
static inline bool native_ordinary_fifo_fast() {
    static const bool enabled = !std::getenv("NWII_GX_REDIRECT_PROBE") && !native_wgp_diagnostics() &&
        !std::getenv("NWII_NO_GATHER_ORDINARY_FAST");
    if (!enabled || g_wpar_redirect || !nwii::runtime::g_mmu) return false;
    const uint32_t base = nwii::runtime::hw::pi_fifo_base, end = nwii::runtime::hw::pi_fifo_end;
    return (base == 0 && end < 0x04000000u) ||
           (base != 0 && (base & 0x03ffffffu) == (nwii::runtime::hw::g_cp_fifo_base & 0x03ffffffu));
}
'''
    s=s[:a]+new+s[b:]
    for sig,body in (('void GX_WGPIPE_Write8(uint8_t val) {\n','native_wgp_bulk(val,1)'),('void GX_WGPIPE_Write16(uint16_t val) {\n','native_wgp_bulk(val,2)'),
                     ('void GX_WGPIPE_Write32(uint32_t val) {\n','native_wgp_bulk(val,4)')):
        assert s.count(sig)==1;s=s.replace(sig,sig+'    if (native_ordinary_fifo_fast()) {'+body+'; return;}\n',1)
    sig='    union { double d; uint64_t i; } u; u.d = val;\n    const int redirect=native_gather_redirect_span(u.i,8);\n'
    assert s.count(sig)==1
    s=s.replace(sig,'    union { double d; uint64_t i; } u; u.d = val;\n    if (native_ordinary_fifo_fast()) {native_wgp_bulk(u.i,8); return;}\n    const int redirect=native_gather_redirect_span(u.i,8);\n',1)
    f.write_text(s)
print('Ordinary write-gather fast path installed.')
