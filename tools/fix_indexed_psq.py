"""Emit indexed quantized paired-single loads/stores using the tested CPU helpers."""
from pathlib import Path
import json,re
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp';s=p.read_text()
needle='    if (xo == 21) {\n      out << "    ctx.ps_add('
replacement='''    uint32_t xo6 = (inst.opcode >> 1) & 0x3F;
    if (xo6 == 6 || xo6 == 7 || xo6 == 38 || xo6 == 39) {
      uint32_t W = (inst.opcode >> 10) & 1, I = (inst.opcode >> 7) & 7;
      out << "    { uint32_t ea = " << (frA ? "ctx.gpr[" + std::to_string(frA) + "]" : "0u")
          << " + ctx.gpr[" << frB << "];\\n";
      out << "      ctx.psq_" << ((xo6 == 6 || xo6 == 38) ? "load" : "store")
          << "(" << frD << ", ea, " << W << ", " << I << ");\\n";
      if (xo6 >= 38) out << "      ctx.gpr[" << frA << "] = ea;\\n";
      out << "    } // indexed quantized paired-single\\n";
    } else if (xo == 21) {
      out << "    ctx.ps_add('''
if needle in s:s=s.replace(needle,replacement)
elif '// indexed quantized paired-single' not in s:raise SystemExit('Indexed PSQ patch context changed')
if p.read_text()!=s:p.write_text(s)
pattern=re.compile(r'(    // 0x[0-9A-F]{8}: ([0-9A-F]{8})\n)    std::cerr << "UNIMPLEMENTED Opcode 4[^\n]*\n    ctx\.fpr\[\d+\] = 0\.0;\n    ctx\.ps1\[\d+\] = 0\.0;\n')
counts=[];total=0
for p in sorted((PORT/'build/crash-generated').glob('output_*.cpp')):
    original=p.read_text();count=[0]
    def fix(m):
        w=int(m[2],16);xo=(w>>1)&63
        if w>>26!=4 or xo not in (6,7,38,39):return m[0]
        fd=(w>>21)&31;ra=(w>>16)&31;rb=(w>>11)&31;W=(w>>10)&1;I=(w>>7)&7
        base=f'ctx.gpr[{ra}]' if ra else '0u';method='load' if xo in (6,38) else 'store'
        code=f'    {{ uint32_t ea = {base} + ctx.gpr[{rb}];\n      ctx.psq_{method}({fd}, ea, {W}, {I});\n'
        if xo>=38:code+=f'      ctx.gpr[{ra}] = ea;\n'
        count[0]+=1
        return m[1]+code+'    } // indexed quantized paired-single\n'
    updated=pattern.sub(fix,original)
    if updated!=original:p.write_text(updated);total+=count[0];counts.append({'chunk':p.name,'instructions':count[0]})
result={'changed_instructions':total,'changed_chunks':len(counts),'chunks':counts}
report=PORT/'reports/indexed-psq-patch.json'
if total or not report.exists():report.write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
