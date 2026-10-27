"""Opt-in probe (NWII_GX_REDIRECT_PROBE=1): log gather-pipe redirect transitions with guest PC/LR and bytes diverted.

wgp_redirect() sends pipe bytes to a RAM FIFO (display-list recording) when the PI FIFO base differs from the CP base.
This logs when that diversion starts/stops so it can be matched against GX stream desyncs. Read-only."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
BS = chr(92)
gx = PORT / 'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp'
s = gx.read_bytes().decode('utf-8')
nl = '\r\n' if '\r\n' in s else '\n'
t = s.replace('\r\n', '\n')
if 'NWII_GX_REDIRECT_PROBE' in t:
    print('already staged')
else:
    old = '    nwii::runtime::hw::pi_fifo_get(base, end, wptr);'
    new = old + chr(10) + '''    {
        static const bool redirect_probe = std::getenv("NWII_GX_REDIRECT_PROBE") != nullptr;
        static bool was_redirecting = false;
        static uint64_t diverted = 0;
        static int shown = 0;
        if (redirect_probe) {
            extern CPUContext* g_ctx_ptr;
            uint32_t cpb = base ? nwii::runtime::hw::cp_fifo_base_reg() : 0;
            bool redirecting = base != 0 && (base & 0x03FFFFFF) != (cpb & 0x03FFFFFF);
            if (redirecting) ++diverted;
            if (redirecting != was_redirecting && shown < 200) {
                ++shown;
                printf("[GXREDIR] %s base=0x%08X cp_base=0x%08X end=0x%08X wptr=0x%08X pc=0x%08X lr=0x%08X diverted_bytes=%llu@@n",
                       redirecting ? "START" : "STOP", base, cpb, end, wptr, g_ctx_ptr ? g_ctx_ptr->pc : 0, g_ctx_ptr ? g_ctx_ptr->lr : 0,
                       (unsigned long long)diverted);
                fflush(stdout);
                if (!redirecting) diverted = 0;
            }
            was_redirecting = redirecting;
        }
    }'''.replace('@@n', BS + 'n')
    if t.count(old) != 1:
        raise SystemExit('wgp_redirect context changed')
    gx.write_bytes(t.replace(old, new, 1).replace('\n', nl).encode('utf-8'))
    print('GX redirect probe staged')
