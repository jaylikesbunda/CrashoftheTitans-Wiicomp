"""Explicit single-rounding double multiply-add; finite witnesses in check_fp_witness.py."""
from pathlib import Path
import re,json
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp';s=p.read_text()
for name,xo in [('fmadd',29),('fmsub',28),('fnmadd',31),('fnmsub',30)]:
    start=s.index(f'    }} else if ((xo & 0x1F) == {xo}) {{',s.index('ppc_inst.opcode() == 63'))
    end=s.index('    } else if',start+1)
    neg='-' if name.startswith('fn') else ''
    minus='-' if name in ('fmsub','fnmsub') else ''
    block=f'''    }} else if ((xo & 0x1F) == {xo}) {{
      out << "    ctx.fpr[" << fD << "] = {neg}std::fma(ctx.fpr[" << fA
          << "], ctx.fpr[" << ((ppc_inst.value() >> 6) & 0x1F)
          << "], {minus}ctx.fpr[" << fB << "]); // {name}\\n";
'''
    s=s[:start]+block+s[end:]
if p.read_text()!=s:p.write_text(s)
changes=[]
pattern=re.compile(r'ctx\.fpr\[(\d+)\] = (\-?)(\(?)ctx\.fpr\[(\d+)\] \* ctx\.fpr\[(\d+)\] ([+-]) ctx\.fpr\[(\d+)\](\)?); // (fmadd|fmsub|fnmadd|fnmsub)\b')
def replace(m):
    dest,neg,_,a,c,op,b,_,name=m.groups()
    return f'ctx.fpr[{dest}] = {neg}std::fma(ctx.fpr[{a}], ctx.fpr[{c}], '+('-' if op=='-' else '')+f'ctx.fpr[{b}]); // {name}'
for p in sorted((PORT/'build/crash-generated').glob('output_*.cpp')):
    s=p.read_text();new,n=pattern.subn(replace,s)
    if n:p.write_text(new);changes.append({'file':p.name,'sites':n})
report=PORT/'reports/fp-fusion-patch.json'
if changes or not report.exists():report.write_text(json.dumps({'chunks':changes,'sites':sum(x['sites'] for x in changes)},indent=2)+'\n')
print('Explicit double fusion staged:',sum(x['sites'] for x in changes),'new sites')
