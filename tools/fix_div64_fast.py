"""Host 64-bit divide for the MSL software quotient func_80393790 (signed).

Capture frame-cost-20261006-180351-850400: func_80393790 is 0.472 ms/frame on
the guest thread (90 self samples). It is a bit-by-bit sign-magnitude shift/
subtract loop. For all DEFINED inputs (divisor != 0 and not INT64_MIN/-1) the
PowerPC semantics are truncation toward zero, exactly C++ int64_t `/`. Divisor
0 and INT64_MIN/-1 are architecturally undefined and fall through to the
untouched translated body, so exotic behavior is preserved.

Entry fast path (ctx.pc is the function entry and NWII_EXACT_DIV64 is unset):
r3:r4 = signed quotient, pc = lr, instruction count advanced by a typical 80.
The dead 16-byte frame below r1 and the volatile scratch (r0, r5-r10, CTR,
cr0, XER.CA) are not reproduced; under the PowerPC EABI none are live across
the call. NWII_EXACT_DIV64=1 restores the original code path exactly.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
path = p / 'build/crash-generated/output_71.cpp'
s = path.read_text(errors='surrogateescape')
marker = 'NATIVE_DIV64_FAST_V1'
if marker not in s:
    a = s.index('void func_80393790(')
    anchor = '    ++ctx.inst_count;\nloc_80393790: ;'
    i = s.index(anchor, a)
    fast = '''    { // NATIVE_DIV64_FAST_V1
        static const bool exact_div = std::getenv("NWII_EXACT_DIV64") != nullptr;
        if (!exact_div && ctx.pc == 0x80393790) {
            const int64_t n = int64_t((uint64_t(ctx.gpr[3]) << 32) | ctx.gpr[4]);
            const int64_t d = int64_t((uint64_t(ctx.gpr[5]) << 32) | ctx.gpr[6]);
            if (d != 0 && !(n == INT64_MIN && d == -1)) {
                const uint64_t q = uint64_t(n / d);
                ctx.gpr[3] = uint32_t(q >> 32); ctx.gpr[4] = uint32_t(q);
                ctx.inst_count += 80; ctx.pc = ctx.lr; return;
            }
        }
    }
'''
    s = s[:i] + fast + s[i:]
    path.write_text(s, errors='surrogateescape')
for inc in ('#include <cstdint>', '#include <cstdlib>', '#include <cstring>'):
    if inc not in s:
        s = path.read_text(errors='surrogateescape')
        if '#include <cmath>' in s:
            s = s.replace('#include <cmath>', '#include <cmath>\n' + inc, 1)
        else:
            s = inc + '\n' + s
        path.write_text(s, errors='surrogateescape')
print('64-bit signed divide fast path installed in func_80393790')
