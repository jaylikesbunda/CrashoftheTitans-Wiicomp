"""Opt-in (NWII_GX_MTXDIAG=1): classify draws by position-matrix state at parse time and print a summary every 3 s.

Categories: per-vertex matrix index draws (with / without NaN in the parse-time bank), snapshot finite, snapshot
containing NaN/inf, snapshot missing (falls back to the renderer's live matrix). Also dumps the first few NaN
snapshots. Read-only."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
BS = chr(92)
p = PORT / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/fifo_parser.cpp'
s = p.read_bytes().decode('utf-8')
nl = '\r\n' if '\r\n' in s else '\n'
t = s.replace('\r\n', '\n')
if 'NWII_GX_MTXDIAG' in t:
    print('already staged')
else:
    anchor = '            if (g_skindbg) {\n                g_skin_draws_total++;'
    if anchor not in t:
        raise SystemExit('fifo_parser: snapshot anchor changed')
    diag = '''            {
                static const bool mtxdiag = std::getenv("NWII_GX_MTXDIAG") != nullptr;
                if (mtxdiag) {
                    static uint64_t total = 0, pmidx = 0, nan_bank = 0, snap_ok = 0, snap_nan = 0, snap_none = 0;
                    static int dumped = 0;
                    ++total;
                    if (vat.posMatIdx) {
                        ++pmidx;
                        bool anynan = false;
                        for (int k = 0; k < 256; ++k) if (!std::isfinite(g_parse_posmtx[k])) { anynan = true; break; }
                        if (anynan) ++nan_bank;
                    } else if (c.raw->have_snap_mtx) {
                        bool bad = false;
                        for (int k = 0; k < 12; ++k) if (!std::isfinite(c.raw->snap_pos_mtx[k])) bad = true;
                        if (bad) {
                            ++snap_nan;
                            if (dumped < 6) {
                                ++dumped;
                                printf("[MTXDIAG] NaN snapshot idx=%d:", (int)g_state.defPosMtxIdx);
                                for (int k = 0; k < 12; ++k) printf(" %g", c.raw->snap_pos_mtx[k]);
                                printf("@@n");
                            }
                        } else {
                            ++snap_ok;
                            static int shown_ok = 0, window_ok = 0;
                            if (++window_ok > 400) { window_ok = 0; shown_ok = 0; }
                            if (shown_ok < 8 && g_state.projType == 0 && offset + 3 + 12 <= fifo_size && (int)vat.posType == 4) {
                                ++shown_ok;
                                auto bef = [&](size_t o) { uint32_t u = (uint32_t(fifo[offset+3+o]) << 24) | (uint32_t(fifo[offset+4+o]) << 16) | (uint32_t(fifo[offset+5+o]) << 8) | uint32_t(fifo[offset+6+o]); float f; std::memcpy(&f, &u, 4); return f; };
                                const float x = bef(0), y = bef(4), z = bef(8);
                                const float* m = c.raw->snap_pos_mtx;
                                printf("[MTXDIAG] draw cmd=0x%02X count=%u posMtx=[%g %g %g %g | %g %g %g %g | %g %g %g %g] v0=(%g,%g,%g) view=(%g,%g,%g)@@n",
                                       cmd, (unsigned)vtx_count, m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9], m[10], m[11], x, y, z,
                                       m[0]*x+m[1]*y+m[2]*z+m[3], m[4]*x+m[5]*y+m[6]*z+m[7], m[8]*x+m[9]*y+m[10]*z+m[11]);
                            }
                        }
                    } else ++snap_none;
                    static auto t0 = std::chrono::steady_clock::now();
                    auto now = std::chrono::steady_clock::now();
                    if (std::chrono::duration_cast<std::chrono::seconds>(now - t0).count() >= 3) {
                        t0 = now;
                        printf("[MTXDIAG] draws=%llu posMatIdx=%llu(nanbank=%llu) snap_ok=%llu snap_nan=%llu snap_none=%llu defPosMtxIdx=%d@@n",
                               (unsigned long long)total, (unsigned long long)pmidx, (unsigned long long)nan_bank,
                               (unsigned long long)snap_ok, (unsigned long long)snap_nan, (unsigned long long)snap_none, (int)g_state.defPosMtxIdx);
                        fflush(stdout);
                        total = pmidx = nan_bank = snap_ok = snap_nan = snap_none = 0;
                    }
                }
            }
'''.replace('@@n', BS + 'n')
    t = t.replace(anchor, diag + anchor, 1)
    if '#include <cmath>' not in t:
        t = '#include <cmath>\n' + t
    p.write_bytes(t.replace('\n', nl).encode('utf-8'))
    print('GX matrix diagnostic staged')
