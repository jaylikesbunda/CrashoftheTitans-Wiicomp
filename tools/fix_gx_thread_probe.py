"""Opt-in probe (NWII_GX_THREAD_PROBE=1): log which guest thread writes the GX gather pipe and when the writer changes.

Reads the current-thread pointer at 0x800000E4 for each pushed byte; prints a line when it changes, with the running byte
count and guest PC/LR. Read-only."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
BS = chr(92)
gx = PORT / 'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp'
s = gx.read_bytes().decode('utf-8')
nl = '\r\n' if '\r\n' in s else '\n'
t = s.replace('\r\n', '\n')
if 'NWII_GX_THREAD_PROBE' in t:
    print('already staged')
else:
    old = 'static inline void wgp_push(uint8_t b) {'
    new = old + '''
    {
        static const bool thread_probe = std::getenv("NWII_GX_THREAD_PROBE") != nullptr;
        if (thread_probe) {
            extern CPUContext* g_ctx_ptr;
            static uint64_t pushed = 0;
            static uint32_t last_thread = 0;
            static int shown = 0;
            ++pushed;
            if (g_ctx_ptr) {
                const uint32_t th = g_ctx_ptr->mmu.read32(0x800000E4);
                if (th != last_thread && shown < 600) {
                    ++shown;
                    printf("[GXTHREAD] byte#%llu thread 0x%08X -> 0x%08X pc=0x%08X lr=0x%08X in_cb=%d@@n", (unsigned long long)pushed, last_thread, th,
                           g_ctx_ptr->pc, g_ctx_ptr->lr, (int)g_ctx_ptr->in_callback);
                    fflush(stdout);
                    last_thread = th;
                }
            }
        }
    }'''.replace('@@n', BS + 'n')
    if old not in t:
        raise SystemExit('wgp_push context changed')
    gx.write_bytes(t.replace(old, new, 1).replace(chr(10), nl).encode('utf-8'))
    print('GX thread probe staged')
