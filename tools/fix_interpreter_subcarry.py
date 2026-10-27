"""Implement encountered subfc/subfe and their OE/Rc forms."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/interpreter.cpp';s=p.read_text()
needle='        case 202: case 714: { // INTERP_ADDZE_V1'
addition='''        case 8: case 520: case 136: case 648: { // INTERP_SUBCARRY_V1
            const uint32_t a = ctx.gpr[rA], b = ctx.gpr[rB];
            const uint32_t carry = (xo & 128) ? ((ctx.xer >> 29) & 1u) : 1u;
            const uint64_t sum = uint64_t(~a) + b + carry;
            ctx.gpr[rD] = uint32_t(sum);
            ctx.xer = (ctx.xer & ~(1u << 29)) | (uint32_t(sum >> 32) << 29);
            if (xo & 512) {
                const int64_t signed_sum = int64_t(int32_t(b)) - int32_t(a) + carry - 1;
                const uint32_t overflow = signed_sum > 2147483647LL || signed_sum < -2147483648LL;
                ctx.xer = (ctx.xer & ~(1u << 30)) | (overflow << 30) | (overflow << 31);
            }
            if (rc) set_cr0(ctx, int32_t(ctx.gpr[rD]));
            break;
        }
'''
if 'INTERP_SUBCARRY_V1' not in s:
    if needle not in s:raise SystemExit('Carry arithmetic insertion changed')
    p.write_text(s.replace(needle,addition+needle,1))
print('Interpreter subfc/subfe/OE/Rc staged.')
