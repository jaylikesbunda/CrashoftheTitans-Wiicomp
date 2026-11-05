"""Implement the condition-register logical ops (crand/cror/crxor/...) in the fallback interpreter.

Without them opcode 19 falls through to pc+4, so every cror/crnor/creqv runs as a no-op. MWCC
emits `fcmpo; cror eq,gt,eq; beq` for float `>=`, which then evaluates false whenever the
operands are not exactly equal. Crash's on-foot fight tree (USA 0x80084DE0..0x8008C72C) runs
in this interpreter, and its inlined CDamageableBehaviour::CanBlock (shield >= 1.0) failed,
so the Aku Aku shield (BlockStart, state 0x160) could never start.
"""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/interpreter.cpp';s=p.read_text()
needle='        if (xo == 0) { // mcrf'
addition='''        if (xo == 33 || xo == 129 || xo == 193 || xo == 225 || xo == 257 || xo == 289 ||
            xo == 417 || xo == 449) { // INTERP_CRLOGIC_V1: crnor crandc crxor crnand crand creqv crorc cror
            auto get = [&](uint32_t b) {
                const auto& f = ctx.cr[b >> 2];
                switch (b & 3) { case 0: return f.lt; case 1: return f.gt; case 2: return f.eq; default: return f.so; }
            };
            const bool a = get(rA), b = get(rB);
            bool d = false;
            switch (xo) {
            case 33:  d = !(a || b); break;
            case 129: d = a && !b; break;
            case 193: d = a != b; break;
            case 225: d = !(a && b); break;
            case 257: d = a && b; break;
            case 289: d = a == b; break;
            case 417: d = a || !b; break;
            default:  d = a || b; break;
            }
            auto& f = ctx.cr[rD >> 2];
            switch (rD & 3) { case 0: f.lt = d; break; case 1: f.gt = d; break; case 2: f.eq = d; break; default: f.so = d; break; }
            ctx.pc = pc + 4;
            return true;
        }
'''
if 'INTERP_CRLOGIC_V1' not in s:
    if needle not in s:raise SystemExit('Interpreter CR-logical insertion context changed')
    p.write_text(s.replace(needle,addition+needle,1))
print('Interpreter CR logical ops staged.')
