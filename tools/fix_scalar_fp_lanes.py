"""Replicate scalar-single results to PS1, matching Gekko behaviour observed in Dolphin.
Bounded arithmetic/alias witnesses: check_scalar_fp_lanes.py.
"""
from pathlib import Path
import re,json
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp';s=p.read_text()
old='out << "    ctx.fpr[" << fD << "] = '
new='out << "    ctx.fpr[" << fD << "] = ctx.ps1[" << fD << "] = '
a=s.index('  } else if (ppc_inst.opcode() == 59) {');b=s.index('  } else if (ppc_inst.opcode() == 63)',a)
region=s[a:b]
if old not in region and new not in region:raise SystemExit('Single FP emitter context changed')
s=s[:a]+region.replace(new,old).replace(old,new)+s[b:]
a=s.index('    } else if (xo == 12) {',s.index('ppc_inst.opcode() == 63'));b=s.index('    } else if',a+1)
region=s[a:b]
if old not in region and new not in region:raise SystemExit('frsp emitter context changed')
s=s[:a]+region.replace(new,old).replace(old,new)+s[b:]
if p.read_text()!=s:p.write_text(s)
pattern=re.compile(r'ctx\.fpr\[(\d+)\] = (?!ctx\.ps1)([^\n]*); // (fadds|fsubs|fmuls|fdivs|fsqrts|fres|fmadds|fmsubs|fnmadds|fnmsubs|frsp)\b')
def repl(m):return f'ctx.fpr[{m[1]}] = ctx.ps1[{m[1]}] = {m[2]}; // {m[3]}'
changes=[]
for p in sorted((PORT/'build/crash-generated').glob('output_*.cpp')):
    s=p.read_text();new,n=pattern.subn(repl,s)
    if n:p.write_text(new);changes.append({'file':p.name,'sites':n})
report=PORT/'reports/scalar-fp-lane-patch.json'
if changes or not report.exists():report.write_text(json.dumps({'chunks':changes,'sites':sum(x['sites'] for x in changes)},indent=2)+'\n')
print('Single FP lane replication staged:',sum(x['sites'] for x in changes),'new sites')
