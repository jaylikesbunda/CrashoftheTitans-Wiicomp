"""Implement encountered addze, including OE/Rc and carry/overflow state."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/interpreter.cpp';s=p.read_text()
needle='        case 266: ctx.gpr[rD] = ctx.gpr[rA] + ctx.gpr[rB]; if (rc) set_cr0(ctx, (int32_t)ctx.gpr[rD]); break;'
addition='''
        case 202: case 714: { // INTERP_ADDZE_V1
            const uint32_t a = ctx.gpr[rA];
            const uint32_t carry = (ctx.xer >> 29) & 1u;
            const uint64_t sum = uint64_t(a) + carry;
            ctx.gpr[rD] = uint32_t(sum);
            ctx.xer = (ctx.xer & ~(1u << 29)) | (uint32_t(sum >> 32) << 29);
            if (xo & 512) {
                const uint32_t overflow = a == 0x7FFFFFFFu && carry;
                ctx.xer = (ctx.xer & ~(1u << 30)) | (overflow << 30) | (overflow << 31);
            }
            if (rc) set_cr0(ctx, int32_t(ctx.gpr[rD]));
            break;
        }'''
if 'INTERP_ADDZE_V1' not in s:
    if needle not in s:raise SystemExit('Interpreter insertion context changed')
    p.write_text(s.replace(needle,needle+addition,1))
print('Interpreter addze/OE/Rc staged.')
