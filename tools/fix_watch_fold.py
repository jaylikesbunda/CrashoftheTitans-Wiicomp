"""NATIVE_WATCH_FOLD_V1: make the per-store guest-RAM watch test a compile-time constant in shipping builds.

With the recorder opt-in (NATIVE_SCHED_RECORDER_OPT_IN_V2) the watch is 0 in every normal launch, but g_watch_addr is
a variable, so each inline guest store still loads it: native_guest_fast::StackSpan calls native_watch_span_may_hit on
every stack store, and MMU::write8/16/32/f32 test `g_watch_addr == native_v13_watch_snapshot` and the write-gather watch.
This stage adds runtime/native_watch_fold.h (NWII_WATCH_FOLDED) and, when it is 1:
  * native_watch_span_may_hit returns false before reading g_watch_addr (reaches StackSpan, the NWII_WATCH_CLEAR macro,
    gather/bulk/region/proof guards and the vertex emit kernel);
  * the four V13 inline stores skip the snapshot test and the write-gather watch predicate;
  * the g_watch_addr initialiser returns 0 and tells the user once if NWII_WATCH / NWII_SCHED_RECORDER was requested.
NWII_BUILD_WATCH_FOLD=0 (or --restore) removes every edit; the staged text is then byte-identical to the unfolded
staging. Idempotent; each edit is applied independently so a replay that restored only the V13 blocks heals.
Usage: fix_watch_fold.py [--restore] [--runtime <nWiiRuntime dir>]
"""
from pathlib import Path
import argparse, os

PORT = Path(__file__).resolve().parents[1]
INCLUDE = '#include "runtime/native_watch_fold.h" // NATIVE_WATCH_FOLD_V1\n'
SPAN_OLD = '    if(!g_watch_addr || !bytes)return false;\n'
SPAN_NEW = '    if(NWII_WATCH_FOLDED)return false; // NATIVE_WATCH_FOLD_V1\n' + SPAN_OLD
SNAP_OLD = 'if(__builtin_expect(g_watch_addr==native_v13_watch_snapshot,1))'
SNAP_NEW = 'if(__builtin_expect(NWII_WATCH_FOLDED || g_watch_addr==native_v13_watch_snapshot,1))'
WG_OLD = '!((native_launch_flags::wg_watch_possible || g_watch_addr!=native_launch_flags::watch) && native_v13_wg_watch('
WG_NEW = '!((!NWII_WATCH_FOLDED && (native_launch_flags::wg_watch_possible || g_watch_addr!=native_launch_flags::watch)) && native_v13_wg_watch('
INIT_ANCHOR = 'uint32_t g_watch_addr = []() -> uint32_t {\n  const char *env = std::getenv("NWII_WATCH");\n'
INIT_NEW = ('uint32_t g_watch_addr = []() -> uint32_t {\n  const char *env = std::getenv("NWII_WATCH");\n'
            '  if (NWII_WATCH_FOLDED) { // NATIVE_WATCH_FOLD_V1\n'
            '    if (env || std::getenv("NWII_SCHED_RECORDER"))\n'
            '      std::fputs("[watch] NWII_WATCH / NWII_SCHED_RECORDER ignored: this build compiles the guest watch out '
            '(rebuild with NWII_BUILD_WATCH_FOLD=0)\\n", stderr);\n'
            '    return 0u;\n  }\n')


def swap(text, old, new, restore):
    """Apply old->new (or back); report whether the text changed. Counts are asserted so an unknown shape fails loudly."""
    src, dst = (new, old) if restore else (old, new)
    if not restore and old in new and new in text:  # already applied (the span edit's new text contains its old text)
        return text, 0
    if src in text:
        return text.replace(src, dst), text.count(src)
    return text, 0


def apply(runtime, restore, fold):
    header = runtime / 'include/runtime/cpu_context.h'
    stubs = runtime / 'src/core/os_hle_stubs.cpp'
    overlay = (PORT / 'runtime-overlays/native_watch_fold.h').read_text()
    staged_overlay = overlay.replace('#define NWII_WATCH_FOLDED 1 //', '#define NWII_WATCH_FOLDED %d //' % (1 if fold else 0))
    target = runtime / 'include/runtime/native_watch_fold.h'
    t = header.read_text(encoding='utf-8', errors='surrogateescape')
    counts = {}
    off = restore or not fold
    anchor = 'extern uint32_t g_watch_addr;\n'
    assert t.count(anchor) == 1, 'g_watch_addr declaration anchor'
    if off:
        t = t.replace(INCLUDE, '')
    elif INCLUDE not in t:
        t = t.replace(anchor, INCLUDE + anchor, 1)
    # The span edit applies to the one definition; V13 edits to the four stores (they are inside restorable blocks).
    t, counts['span'] = swap(t, SPAN_OLD, SPAN_NEW, off)
    t, counts['snapshot'] = swap(t, SNAP_OLD, SNAP_NEW, off)
    t, counts['write_gather'] = swap(t, WG_OLD, WG_NEW, off)
    if not off:
        assert t.count('NATIVE_WATCH_FOLD_V1') >= 1 and t.count('NWII_WATCH_FOLDED') >= 1
        assert counts['span'] in (0, 1), counts
    header.write_text(t, encoding='utf-8', errors='surrogateescape')
    s = stubs.read_text(encoding='utf-8', errors='surrogateescape')
    assert s.count(INIT_ANCHOR) == 1 or INIT_NEW in s, 'g_watch_addr initialiser anchor'
    s, counts['initialiser'] = swap(s, INIT_ANCHOR, INIT_NEW, off)
    stubs.write_text(s, encoding='utf-8', errors='surrogateescape')
    if off:
        if target.exists():
            target.unlink()
    elif not target.exists() or target.read_text() != staged_overlay:
        target.write_text(staged_overlay)
    return counts


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--runtime', type=Path, default=PORT / 'build/nwii-full-source/nWiiRuntime')
    ap.add_argument('--fold', choices=('0', '1'), default=os.environ.get('NWII_BUILD_WATCH_FOLD', '1'))
    a = ap.parse_args()
    c = apply(a.runtime, a.restore, a.fold != '0')
    off = a.restore or a.fold == '0'
    print('Watch fold %s (%s).' % ('removed' if off else 'staged', ', '.join('%s=%d' % kv for kv in c.items())))
