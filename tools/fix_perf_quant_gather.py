"""Remove libm calls from exact GQR scales and batch diagnostic-free FIFO writes.

Redirects and diagnostic byte streams retain the existing byte implementation.
Publications still occur at exactly the same 4096-byte boundaries and guest polls.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime'
h = rt / 'include/runtime/cpu_context.h'
s = h.read_text()
if 'NATIVE_GQR_EXPONENT_V1' not in s:
    (p / 'runtime-overlays/perf_quant_baseline.h').write_text(s)
    for old, exponent in [('std::ldexp(1.0f, -signed_scale)', '127 - signed_scale'),
                          ('std::ldexp(1.0f, signed_scale)', '127 + signed_scale')]:
        assert s.count(old) == 1
        s = s.replace('float scale = ' + old + ';',
            'const uint32_t scale_bits = uint32_t(' + exponent + ') << 23; // NATIVE_GQR_EXPONENT_V1\n'
            '    float scale; std::memcpy(&scale, &scale_bits, sizeof(scale));', 1)
    h.write_text(s)
gx = rt / 'src/hle/gx.cpp'
s = gx.read_text()
if 'NATIVE_WGP_BULK_V1' not in s:
    (p / 'runtime-overlays/perf_gather_baseline.cpp').write_text(s)
    helper = '''// NATIVE_WGP_BULK_V1: CPU-private writes, preserving every publish boundary.
static bool native_wgp_diagnostics() {
    static const bool enabled = std::getenv("NWII_GX_DESYNC_PROBE") ||
        std::getenv("NWII_GX_THREAD_PROBE") || std::getenv("NWII_GXDUMP") ||
        std::getenv("NWII_WGSEG") || std::getenv("NWII_WGTRACE") ||
        NativeLaunchFlag_NWII_SAMPLE();
    return enabled;
}
static inline void native_wgp_bulk(uint64_t value, unsigned bytes) {
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
'''
    anchor = 'void GX_WGPIPE_Write8(uint8_t val) {'
    assert s.count(anchor) == 1
    s = s.replace(anchor, helper + '\n' + anchor, 1)
    s = s.replace('    wgp_push(val);\n}',
        '    if (!native_wgp_diagnostics()) {native_wgp_bulk(val, 1); return;}\n    wgp_push(val);\n}', 1)
    s = s.replace('    wgp_push(val >> 8);\n    wgp_push(val & 0xFF);',
        '    if (!native_wgp_diagnostics()) {native_wgp_bulk(val, 2); return;}\n'
        '    wgp_push(val >> 8);\n    wgp_push(val & 0xFF);', 1)
    anchor = '    if (NativeLaunchFlag_NWII_SAMPLE()) {\n        static uint64_t n = 0;'
    assert s.count(anchor) == 1
    s = s.replace(anchor, '    if (!native_wgp_diagnostics()) {native_wgp_bulk(val, 4); return;}\n' + anchor, 1)
    anchor = '    for (int s = 56; s >= 0; s -= 8)\n        wgp_push((u.i >> s) & 0xFF);'
    assert s.count(anchor) == 1
    s = s.replace(anchor, '    if (!native_wgp_diagnostics()) {native_wgp_bulk(u.i, 8); return;}\n' + anchor, 1)
    gx.write_text(s)
print('Exact GQR exponents and diagnostic-free bulk FIFO writes staged.')
