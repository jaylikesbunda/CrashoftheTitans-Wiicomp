"""Small guest-thread costs found in capture frame-cost-20261006-221021 (call stacks resolved to depth 9).

1. Clock reads: steady_clock::now on the guest thread came from native_process_callbacks_slow's diagnostics timeline (20 of 36 samples:
   it read the clock on every due poll but only prints every 5 s) and native_wpad_poll (8 samples, already 1/32 throttled). The timeline now
   reads the clock every 256th due poll and the wpad poll runs every 128th call (the pad is sampled at 100 Hz of guest time).
2. GX FIFO staging (native_wgp_bulk 0.73 ms/frame + GX_WGPIPE_* helpers): the CPU-private staging buffer was a std::vector<uint8_t> filled
   one push_back per byte. It is now a fixed 4,104-byte buffer filled with one byte-swapped store per write. Bytes, order and publish
   boundaries (a publish exactly when 4,096 bytes are staged, and on every poll) are unchanged; publishing copies the staged bytes
   into g_hw_fifo (reserved once so drains do not make it re-grow).
Idempotent; each edit carries a marker. The WPAD phase upgrade matches the line by shape because
NATIVE_POLL_CLOCK_THROTTLE_V1 may have wrapped it in the NWII_NO_POLL_THROTTLE switch: the switch is
part of the line and is preserved (dropping it would silently retire the rollback).
"""
from pathlib import Path
import re
p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime'


def patch(path, edits):
    s = path.read_text(errors='surrogateescape')
    changed = False
    for marker, old, new in edits:
        if marker in s:
            continue
        assert s.count(old) == 1, (path.name, marker, s.count(old))
        s = s.replace(old, new)
        changed = True
    if changed:
        path.write_text(s, errors='surrogateescape')
    return changed


# 1/32 -> 1/128 on the WPAD poll phase. fix_poll_clock_throttle.py may have wrapped the statement in
# `static const bool no_thr=std::getenv("NWII_NO_POLL_THROTTLE")!=nullptr; ... if (!no_thr && ...)`, so
# the mask is upgraded in place and every other character of the line (the switch included) is kept.
WPAD_V2 = re.compile(r'(?P<head>\{[^\n]*wpad_phase=0;[^\n]*& )31u'
                     r'(?P<tail>\)!=0\) return; \} // NATIVE_WPAD_POLL_THROTTLE_)V2')
WPAD_V3 = 'V3 (was 1/32; pad data is sampled at 100 Hz of guest time)'


def wpad_throttle(s):
    """Upgrade the phase mask in place; (text, changed). No-op once the V3 marker is present."""
    if 'NATIVE_WPAD_POLL_THROTTLE_V3' in s:
        return s, False
    new, n = WPAD_V2.subn(lambda m: m.group('head') + '127u' + m.group('tail') + WPAD_V3, s)
    assert n == 1, ('NATIVE_WPAD_POLL_THROTTLE_V3 anchor matched %d times (expected 1)' % n)
    return new, True


ios = rt / 'src/hle/ios.cpp'
patch(ios, [
    ('NATIVE_DIAG_CLOCK_THROTTLE_V1',
     "    static auto diag_last = diag_t0;\n    if (diag_on && poll_clock_due) {",
     "    static auto diag_last = diag_t0;\n    static uint32_t diag_phase = 0; // NATIVE_DIAG_CLOCK_THROTTLE_V1: the timeline prints every 5 s, so reading the clock on every due poll was waste\n    if (diag_on && poll_clock_due && ((++diag_phase) & 255u) == 0) {"),
])
text, changed = wpad_throttle(ios.read_text(errors='surrogateescape'))
if changed:
    ios.write_text(text, errors='surrogateescape')

gx = rt / 'src/hle/gx.cpp'
patch(gx, [
    ('NATIVE_CPU_FIFO_FIXED_V1',
     "static std::vector<uint8_t> g_cpu_fifo;\n",
     """// NATIVE_CPU_FIFO_FIXED_V1: fixed staging buffer (at most 4,096 bytes are ever staged; publication happens when the 4,096th byte arrives).
struct NativeCpuFifo {
    alignas(64) uint8_t data[4160];
    size_t n = 0;
    bool empty() const { return n == 0; }
    size_t size() const { return n; }
    void clear() { n = 0; }
};
static NativeCpuFifo g_cpu_fifo;
static void NativeAppendFifo(std::vector<uint8_t>& destination, NativeCpuFifo& source) {
    if (destination.capacity() < 131072) destination.reserve(131072);
    destination.insert(destination.end(), source.data, source.data + source.n);
    source.n = 0;
}
"""),
    ('NATIVE_CPU_FIFO_PUSH_V1',
     "    g_cpu_fifo.push_back(b);\n    if (g_cpu_fifo.size() >= 4096) native_gx_publish();",
     "    g_cpu_fifo.data[g_cpu_fifo.n++] = b; // NATIVE_CPU_FIFO_PUSH_V1\n    if (g_cpu_fifo.n >= 4096) native_gx_publish();"),
    ('NATIVE_FIFO_INLINE_STORE_V1',
     """    const size_t first = g_cpu_fifo.size();
    if (first + bytes < 4096) {
        // NATIVE_FIFO_PUSH_INLINE_V1: no out-of-line resize and no zero-fill; same bytes in the same order.
        if (__builtin_expect(g_cpu_fifo.capacity() < 4104, 0)) g_cpu_fifo.reserve(4104);
        for (unsigned i = 0; i < bytes; ++i) g_cpu_fifo.push_back(uint8_t(value >> (8 * (bytes - 1 - i))));
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
}""",
     """    const size_t first = g_cpu_fifo.n;
    if (first + bytes < 4096) {
        // NATIVE_FIFO_INLINE_STORE_V1 (supersedes NATIVE_FIFO_PUSH_INLINE_V1, which earlier stages test for): one byte-swapped store; same bytes in the same order.
        uint8_t* d = g_cpu_fifo.data + first;
        switch (bytes) {
        case 4: { const uint32_t v = __builtin_bswap32(uint32_t(value)); std::memcpy(d, &v, 4); break; }
        case 1: d[0] = uint8_t(value); break;
        case 2: { const uint16_t v = __builtin_bswap16(uint16_t(value)); std::memcpy(d, &v, 2); break; }
        default: { const uint64_t v = __builtin_bswap64(value); std::memcpy(d, &v, 8); break; }
        }
        g_cpu_fifo.n = first + bytes;
        return;
    }
    // Crossing a publish boundary: one byte at a time, publishing exactly when the 4,096th byte is staged.
    for (unsigned i = 0; i < bytes; ++i) {
        g_cpu_fifo.data[g_cpu_fifo.n++] = uint8_t(value >> (8 * (bytes - 1 - i)));
        if (g_cpu_fifo.n == 4096) native_gx_publish();
    }
}"""),
])

main = rt / 'src/core/main.cpp'
patch(main, [
    ('NATIVE_FMA_GUARD_V1',
     'int main(int argc, char **argv) {' + chr(10) + '  if (argc < 2) {',
     'int main(int argc, char **argv) {' + chr(10) +
     '#ifdef NWII_BUILT_WITH_FMA // NATIVE_FMA_GUARD_V1: the guest code is built with -mfma (hardware fused multiply-add)' + chr(10) +
     '  if (!__builtin_cpu_supports(' + chr(34) + 'fma' + chr(34) + ')) { std::cerr << ' + chr(34) + 'This build needs a CPU with FMA3 (Intel since Haswell, AMD since Piledriver/Zen). Rebuild with NWII_HOT_FMA=0 for older CPUs.' + chr(92) + 'n' + chr(34) + '; return 2; }' + chr(10) +
     '#endif' + chr(10) +
     '  if (argc < 2) {'),
])
print('guest runtime trim installed')
