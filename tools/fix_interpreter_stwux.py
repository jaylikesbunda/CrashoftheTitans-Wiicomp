"""Implement encountered fallback stwux; keep the original store and RA update."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/interpreter.cpp';s=p.read_text()
needle='        case 151: ctx.mmu.write32(ea_ra0(0) + ctx.gpr[rB], ctx.gpr[rD]); break;'
addition='''
        case 183: { // INTERP_STWUX_V1
            uint32_t ea = ctx.gpr[rA] + ctx.gpr[rB];
            ctx.mmu.write32(ea, ctx.gpr[rD]);
            ctx.gpr[rA] = ea;
            break;
        }'''
if 'INTERP_STWUX_V1' not in s:
    if needle not in s:raise SystemExit('Interpreter stwux insertion context changed')
    p.write_text(s.replace(needle,needle+addition,1))
print('Interpreter stwux staged.')
