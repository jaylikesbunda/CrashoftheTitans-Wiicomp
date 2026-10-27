"""NATIVE_MMU_WATCH_SPAN_V1 -- single unsigned compare for the watch-span test.

`native_watch_span_may_hit` is always_inline and replicated into every guest
store site. The original uses two 64-bit comparisons; the equivalent 32-bit
form `d = start + bytes - 1 - watch; return d < bytes + 7;` overlaps the watched
eight-byte block exactly (unsigned wrap handles the before/after cases). The
no-watch guard is kept so a watch of 0 stays a no-op. Header-only, idempotent.
"""
from pathlib import Path

MARKER = 'NATIVE_MMU_WATCH_SPAN_V1'
p = Path(__file__).resolve().parents[1]
f = p / 'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h'
s = f.read_text()
OLD = '''__attribute__((always_inline)) inline bool native_watch_span_may_hit(uint32_t address,uint32_t bytes){
    if(!g_watch_addr || !bytes)return false;
    const uint64_t start=address&0x3fffffffu,watch=g_watch_addr&0x3ffffff8u;
    return start<watch+8u && watch<start+uint64_t(bytes);
}'''
NEW = '''__attribute__((always_inline)) inline bool native_watch_span_may_hit(uint32_t address,uint32_t bytes){
    // NATIVE_MMU_WATCH_SPAN_V1: single unsigned compare; see tools/fix_mmu_watch_span.py.
    if(!g_watch_addr || !bytes)return false;
    const uint32_t d=(address&0x3fffffffu)+bytes-1u-(g_watch_addr&0x3ffffff8u);
    return d<bytes+7u;
}'''
if MARKER not in s:
    assert s.count(OLD) == 1, ('watch-span anchor', s.count(OLD))
    s = s.replace(OLD, NEW, 1)
    f.write_text(s)
print('Watch-span test reduced to one unsigned compare; identical predicate.')
