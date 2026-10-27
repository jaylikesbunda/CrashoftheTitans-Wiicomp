"""Three small guest-thread hot spots from capture frame-cost-20261006-180351-850400 (guest thread 33.1 ms/frame).

1. native_wgp_bulk: std::vector<uchar>::resize 0.59 ms/frame exclusive (70 samples; native_gx_publish inclusive 2.8%). The staging
   vector already has capacity >= 4104 on the fast path, so each guest FIFO store zero-filled via an out-of-line resize and then
   overwrote the same bytes. Now each byte is appended with an inlined push_back. Bytes and order are identical.
2. Dispatcher native_wpad_dispatch: 0.37 ms/frame + native_wpad_state 0.22 ms. It is called with every dispatch target but only
   handles 0x803EC364..0x803EEF0C. A range test at the call site skips the call (and its function-local-static guard) elsewhere.
(Poll credit default 16 -> 64 lives in fix_poll_credit.py: polling + clock reads were 2.8 ms/frame; NWII_POLL_CREDIT=1 restores every site;
an event can be observed up to 63 backward branches later, a few microseconds.)
All idempotent; requires the stages it patches to have run.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'

f=rt/'src/hle/gx.cpp';s=f.read_text()
if 'NATIVE_FIFO_PUSH_INLINE_V1' not in s:
    old='''        if (g_cpu_fifo.capacity() < 4104) g_cpu_fifo.reserve(4104);
        g_cpu_fifo.resize(first + bytes);
        const uint64_t be = __builtin_bswap64(value << (8 * (8 - bytes)));
        std::memcpy(g_cpu_fifo.data() + first, &be, bytes);
        return;'''
    new='''        // NATIVE_FIFO_PUSH_INLINE_V1: no out-of-line resize and no zero-fill; same bytes in the same order.
        if (__builtin_expect(g_cpu_fifo.capacity() < 4104, 0)) g_cpu_fifo.reserve(4104);
        for (unsigned i = 0; i < bytes; ++i) g_cpu_fifo.push_back(uint8_t(value >> (8 * (bytes - 1 - i))));
        return;'''
    assert s.count(old)==1
    f.write_text(s.replace(old,new))

f=p/'build/crash-generated/main_output.cpp';s=f.read_text(errors='surrogateescape')
old='if (nwii::runtime::native_wpad_dispatch(ctx,target)) continue;'
new='if (target-0x803EC364u<=0x803EEF0Cu-0x803EC364u && nwii::runtime::native_wpad_dispatch(ctx,target)) continue; // NATIVE_WPAD_RANGE_V1'
if 'NATIVE_WPAD_RANGE_V1' not in s:
    assert s.count(old)==1
    f.write_text(s.replace(old,new),errors='surrogateescape')

print('cheap guest hot spots: FIFO push inline, WPAD range test')
