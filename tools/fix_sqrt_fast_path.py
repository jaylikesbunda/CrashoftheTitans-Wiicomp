"""Host sqrt for the MSL software sqrt (func_803A35F0) on positive normal doubles.

Capture frame-cost-20261006-180351-850400: func_803A35F0 is 1.39 ms/frame on the guest thread (165 samples), the second hottest
translated function, after the integer loops were already register-promoted by native-sdk-sqrt-loops-stage. It is fdlibm's
bit-by-bit __ieee754_sqrt, which is correctly rounded in round-to-nearest, so for positive normal doubles it equals the host's
sqrt (data/sdk-sqrt-tests.json: MATH 9971/9971).

Entry fast path (only when ctx.pc is the function entry, MSR.FP is set, NWII_EXACT_SQRT is unset and f1 is a positive normal
double): f1 = sqrt(f1), pc = lr, instruction count advanced by a typical 800. Zero, denormals, negatives, infinities and NaNs take
the original code unchanged (errno writes, special returns).
Deliberately NOT reproduced on the fast path: the volatile scratch registers (r0, r3-r12, cr0/cr1, XER.CA, CTR, FPR f0 and the
FPSCR compare bits) and the dead stack frame below r1 that the original wrote. Under the PowerPC EABI none of those are live
across a call. NWII_EXACT_SQRT=1 restores the original code path exactly.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1]
path=p/'build/crash-generated/output_73.cpp';s=path.read_text(errors='surrogateescape')
marker='NATIVE_SQRT_FAST_V1'
if marker not in s:
    a=s.index('void func_803A35F0(');anchor='    ++ctx.inst_count;\nloc_803A35F0: ;'
    i=s.index(anchor,a)
    fast='''    { // NATIVE_SQRT_FAST_V1
        static const bool exact_sqrt = std::getenv("NWII_EXACT_SQRT") != nullptr;
        uint64_t bits_; std::memcpy(&bits_, &ctx.fpr[1], 8);
        const uint32_t exponent_ = uint32_t(bits_ >> 52);   // sign and exponent: positive normal means 1..0x7FE
        if (!exact_sqrt && ctx.pc == 0x803A35F0 && (ctx.msr & 0x2000) && exponent_ - 1u < 0x7FEu) {
            ctx.fpr[1] = std::sqrt(ctx.fpr[1]);
            ctx.inst_count += 800;
            ctx.pc = ctx.lr; return;
        }
    }
'''
    s=s[:i]+fast+s[i:]
    path.write_text(s,errors='surrogateescape')
if '#include <cstring>' not in s:
    s=path.read_text(errors='surrogateescape');path.write_text(s.replace('#include <cmath>','#include <cmath>\n#include <cstring>',1),errors='surrogateescape')
print('sqrt fast path installed in func_803A35F0')
