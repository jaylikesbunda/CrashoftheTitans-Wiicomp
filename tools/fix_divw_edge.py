"""divw/divwu edge cases: INT_MIN/-1 raised an x86 divide fault in the native host; divide-by-zero left rD stale.
Use Dolphin/Gekko behaviour for the architecturally undefined results (divw: rD = a<0 ? -1 : 0; divwu: rD = 0)."""
from pathlib import Path
import json, re
PORT = Path(__file__).resolve().parents[1]
p = PORT / 'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp'
s = p.read_text()
old_w = r'''      out << "    if (ctx.gpr[" << rB << "] != 0) ctx.gpr[" << rD
          << "] = (uint32_t)((int32_t)ctx.gpr[" << rA << "] / (int32_t)ctx.gpr["
          << rB << "]); // divw\n";'''
new_w = r'''      out << "    { int32_t a_ = (int32_t)ctx.gpr[" << rA << "], b_ = (int32_t)ctx.gpr[" << rB
          << "]; ctx.gpr[" << rD << "] = (b_ == 0 || (a_ == INT32_MIN && b_ == -1)) ? (a_ < 0 ? 0xFFFFFFFFu : 0u)"
             " : (uint32_t)(a_ / b_); } // divw\n";'''
old_u = r'''      out << "    if (ctx.gpr[" << rB << "] != 0) ctx.gpr[" << rD
          << "] = ctx.gpr[" << rA << "] / ctx.gpr[" << rB << "]; // divwu\n";'''
new_u = r'''      out << "    ctx.gpr[" << rD << "] = ctx.gpr[" << rB << "] ? ctx.gpr[" << rA << "] / ctx.gpr[" << rB
          << "] : 0u; // divwu\n";'''
for name, o, n in (('divw', old_w, new_w), ('divwu', old_u, new_u)):
    if o in s: s = s.replace(o, n)
    elif n not in s: raise SystemExit(f'{name} emitter context changed')
p.write_text(s)
pw = re.compile(r'if \(ctx\.gpr\[(\d+)\] != 0\) ctx\.gpr\[(\d+)\] = \(uint32_t\)\(\(int32_t\)ctx\.gpr\[(\d+)\] / \(int32_t\)ctx\.gpr\[\1\]\); // divw')
pu = re.compile(r'if \(ctx\.gpr\[(\d+)\] != 0\) ctx\.gpr\[(\d+)\] = ctx\.gpr\[(\d+)\] / ctx\.gpr\[\1\]; // divwu')
fw = lambda m: (f'{{ int32_t a_ = (int32_t)ctx.gpr[{m[3]}], b_ = (int32_t)ctx.gpr[{m[1]}]; ctx.gpr[{m[2]}] = (b_ == 0 || (a_ == INT32_MIN && b_ == -1)) ? (a_ < 0 ? 0xFFFFFFFFu : 0u) : (uint32_t)(a_ / b_); }} // divw')
fu = lambda m: f'ctx.gpr[{m[2]}] = ctx.gpr[{m[1]}] ? ctx.gpr[{m[3]}] / ctx.gpr[{m[1]}] : 0u; // divwu'
changes = []
for q in sorted((PORT / 'build/crash-generated').glob('output_*.cpp')):
    t = q.read_text()
    t, a = pw.subn(fw, t); t, b = pu.subn(fu, t)
    if a or b:
        q.write_text(t); changes.append({'file': q.name, 'divw': a, 'divwu': b})
report = PORT / 'reports/divw-edge-patch.json'
if changes or not report.exists():
    report.write_text(json.dumps({'chunks': changes, 'divw_sites': sum(x['divw'] for x in changes), 'divwu_sites': sum(x['divwu'] for x in changes)}, indent=2))
print('divw edge staging:', sum(x['divw'] for x in changes), 'divw,', sum(x['divwu'] for x in changes), 'divwu sites')
