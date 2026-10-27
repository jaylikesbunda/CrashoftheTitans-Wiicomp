"""NATIVE_GATHER_GUARD_OUTLINE_V1: hoist launch-fixed diagnostics/probe flags out of the hot WGPIPE write path.

native_gather_redirect_span, native_wgp_diagnostics and native_ordinary_fifo_fast each carry a
function-local `static const bool ... = std::getenv(...)` evaluated on every call (native_gather_redirect_span
runs per WGPIPE write when the game uses a gather FIFO: ~1.4% of the guest thread). Moving the
initializers to namespace scope removes the guard load/branch from every call; the values are
identical for a fixed launch environment (same pattern as NATIVE_POLL_GUARD_OUTLINE_V1).
Build rollback: NWII_BUILD_GATHER_GUARD_OUTLINE=0. Idempotent.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_GATHER_GUARD_OUTLINE_V1'

REPLACEMENTS = [
    (
        """static inline int native_gather_redirect_span(uint64_t value, unsigned bytes) {
    static const bool probe=std::getenv("NWII_GX_REDIRECT_PROBE")!=nullptr;
    if(probe)return -1;""",
        """// NATIVE_GATHER_GUARD_OUTLINE_V1: launch-fixed probe flag at namespace scope.
static const bool native_gather_redirect_probe=std::getenv("NWII_GX_REDIRECT_PROBE")!=nullptr;
static inline int native_gather_redirect_span(uint64_t value, unsigned bytes) {
    if(native_gather_redirect_probe)return -1;""",
    ),
    (
        """static bool native_wgp_diagnostics() {
    static const bool enabled = std::getenv("NWII_GX_DESYNC_PROBE") ||
        std::getenv("NWII_GX_THREAD_PROBE") || std::getenv("NWII_GXDUMP") ||
        std::getenv("NWII_WGSEG") || std::getenv("NWII_WGTRACE") ||
        NativeLaunchFlag_NWII_SAMPLE();
    return enabled;
}""",
        """// NATIVE_GATHER_GUARD_OUTLINE_V1: launch-fixed diagnostics flag at namespace scope.
static const bool native_wgp_diagnostics_flag = std::getenv("NWII_GX_DESYNC_PROBE") ||
    std::getenv("NWII_GX_THREAD_PROBE") || std::getenv("NWII_GXDUMP") ||
    std::getenv("NWII_WGSEG") || std::getenv("NWII_WGTRACE") ||
    NativeLaunchFlag_NWII_SAMPLE();
static bool native_wgp_diagnostics() { return native_wgp_diagnostics_flag; }""",
    ),
    (
        """static inline bool native_ordinary_fifo_fast() {
    static const bool enabled = !std::getenv("NWII_GX_REDIRECT_PROBE") && !native_wgp_diagnostics() &&
        !std::getenv("NWII_NO_GATHER_ORDINARY_FAST");
    if (!enabled || g_wpar_redirect || !nwii::runtime::g_mmu) return false;""",
        """// NATIVE_GATHER_GUARD_OUTLINE_V1: launch-fixed ordinary-FIFO flag at namespace scope.
static const bool native_ordinary_fifo_fast_enabled = !std::getenv("NWII_GX_REDIRECT_PROBE") &&
    !native_wgp_diagnostics_flag && !std::getenv("NWII_NO_GATHER_ORDINARY_FAST");
static inline bool native_ordinary_fifo_fast() {
    if (!native_ordinary_fifo_fast_enabled || g_wpar_redirect || !nwii::runtime::g_mmu) return false;""",
    ),
]


def apply(s):
    if MARK in s:
        return s
    out = s
    for old, new in REPLACEMENTS:
        n = out.count(old)
        if n != 1:
            raise SystemExit('gather guard anchor matched %d times (expected 1): %r' % (n, old[:60]))
        out = out.replace(old, new, 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_GATHER_GUARD_OUTLINE', '1') == '0':
        print('gather guard outline disabled')
        return
    path = args.port / 'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp'
    before = path.read_text()
    after = apply(before)
    if before != after:
        path.write_text(after)
    print('gather guard outline staged')


if __name__ == '__main__':
    main()
