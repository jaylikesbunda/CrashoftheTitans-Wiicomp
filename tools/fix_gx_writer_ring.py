"""Opt-in (NWII_GX_DESYNC_PROBE=1): ring of the last 16384 gather-pipe byte writes (byte, guest pc, lr), printed run-length
grouped alongside the GXDESYNC dump. Read-only."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
BS = chr(92)
gx = PORT / 'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp'
s = gx.read_bytes().decode('utf-8')
nl = '\r\n' if '\r\n' in s else '\n'
t = s.replace('\r\n', '\n')
if 'g_wgp_ring' in t:
    print('already staged')
else:
    old = 'static inline void wgp_push(uint8_t b) {'
    new = '''WgpRingEntry g_wgp_ring[16384];
uint64_t g_wgp_ring_n = 0;
static inline void wgp_push(uint8_t b) {
    {
        static const bool ring_probe = std::getenv("NWII_GX_DESYNC_PROBE") != nullptr;
        if (ring_probe) {
            extern CPUContext* g_ctx_ptr;
            g_wgp_ring[g_wgp_ring_n++ % 16384] = WgpRingEntry{b, g_ctx_ptr ? g_ctx_ptr->pc : 0, g_ctx_ptr ? g_ctx_ptr->lr : 0};
        }
    }'''
    if old not in t:
        raise SystemExit('wgp_push context changed')
    t = t.replace(old, new, 1)
    fwd = 'namespace nwii::runtime { struct WgpRingEntry { uint8_t b; uint32_t pc, lr; }; extern WgpRingEntry g_wgp_ring[16384]; extern uint64_t g_wgp_ring_n; }' + chr(10) + 'void ProcessGXFifo() {'
    if 'void ProcessGXFifo() {' not in t:
        raise SystemExit('ProcessGXFifo anchor missing')
    t = t.replace('void ProcessGXFifo() {', fwd, 1)
    # print block: insert before the fflush in the desync probe
    anchor = '            fflush(stdout);' + chr(10) + '        }' + chr(10) + '    }'
    printer = '''            {
                namespace rt = nwii::runtime;
                const uint64_t n = rt::g_wgp_ring_n;
                const uint64_t cnt = n < 16384 ? n : 16384;
                printf("[GXWRITER] last %llu pipe writes (oldest first), runs by (pc,lr):@@n", (unsigned long long)cnt);
                uint64_t i = n - cnt;
                while (i < n) {
                    const auto& e0 = rt::g_wgp_ring[i % 16384];
                    uint64_t j = i;
                    std::string bytes;
                    char buf[8];
                    while (j < n && rt::g_wgp_ring[j % 16384].pc == e0.pc && rt::g_wgp_ring[j % 16384].lr == e0.lr) {
                        if (bytes.size() < 96) { snprintf(buf, sizeof buf, "%02x ", rt::g_wgp_ring[j % 16384].b); bytes += buf; }
                        ++j;
                    }
                    printf("[GXWRITER] pc=0x%08X lr=0x%08X n=%llu: %s@@n", e0.pc, e0.lr, (unsigned long long)(j - i), bytes.c_str());
                    i = j;
                }
            }
'''.replace('@@n', BS + 'n')
    a = t.find(anchor)
    if a < 0:
        raise SystemExit('desync print anchor not found')
    t = t[:a] + printer + t[a:]
    gx.write_bytes(t.replace(chr(10), nl).encode('utf-8'))
    print('GX writer ring staged')
