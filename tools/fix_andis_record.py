"""andis. is a record-form instruction: it must update CR0 (upstream emitter omitted it)."""
from pathlib import Path
import json, re
PORT = Path(__file__).resolve().parents[1]
p = PORT / 'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp'
s = p.read_text()
old = r'''    out << "    ctx.gpr[" << ppc_inst.ra() << "] = ctx.gpr[" << ppc_inst.rs()
        << "] & " << (ppc_inst.uimm() << 16) << "; // andis.\n";'''
new = old + r'''
    out << "    ctx.cr[0].lt = ((int32_t)ctx.gpr[" << ppc_inst.ra() << "] < 0);\n";
    out << "    ctx.cr[0].gt = ((int32_t)ctx.gpr[" << ppc_inst.ra() << "] > 0);\n";
    out << "    ctx.cr[0].eq = (ctx.gpr[" << ppc_inst.ra() << "] == 0);\n";'''
if new in s: pass
elif old in s: s = s.replace(old, new); p.write_text(s)
else: raise SystemExit('andis emitter context changed')
pat = re.compile(r'^(    ctx\.gpr\[(\d+)\] = ctx\.gpr\[\d+\] & -?\d+; // andis\.\n)(?!    ctx\.cr\[0\]\.lt)', re.M)
fn = lambda m: m[1] + f'    ctx.cr[0].lt = ((int32_t)ctx.gpr[{m[2]}] < 0);\n    ctx.cr[0].gt = ((int32_t)ctx.gpr[{m[2]}] > 0);\n    ctx.cr[0].eq = (ctx.gpr[{m[2]}] == 0);\n'
changes = []
for q in sorted((PORT / 'build/crash-generated').glob('output_*.cpp')):
    t = q.read_text(); t, n = pat.subn(fn, t)
    if n: q.write_text(t); changes.append({'file': q.name, 'sites': n})
report = PORT / 'reports/andis-record-patch.json'
if changes or not report.exists():
    report.write_text(json.dumps({'chunks': changes, 'sites': sum(x['sites'] for x in changes)}, indent=2))
print('andis. CR0 staging:', sum(x['sites'] for x in changes), 'sites')
