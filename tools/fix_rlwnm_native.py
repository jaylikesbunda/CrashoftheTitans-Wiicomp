"""Emit variable rotate/mask directly, preserving the former micro-interpreter PC."""
from pathlib import Path
import json,re
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp';s=f.read_text()
if 'NATIVE_RLWNM_V1' not in s:
    anchor='  } else if (ppc_inst.opcode() == 31) {'
    new=r'''  } else if (ppc_inst.opcode() == 23) { // NATIVE_RLWNM_V1
    const unsigned rs=ppc_inst.rs(),ra=ppc_inst.ra(),rb=ppc_inst.rb();
    const unsigned mb=(inst.opcode>>6)&31u,me=(inst.opcode>>1)&31u;
    uint32_t mask=0;
    for(unsigned bit=0;bit<32;++bit)
      if(mb<=me ? (bit>=mb && bit<=me) : (bit>=mb || bit<=me))mask|=1u<<(31-bit);
    out << "    ctx.gpr[" << ra << "] = std::rotl(ctx.gpr[" << rs
        << "], int(ctx.gpr[" << rb << "] & 31u)) & 0x" << std::hex
        << mask << std::dec << "u; // native rlwnm\n";
    if(inst.opcode&1u){
      out << "    ctx.cr[0].lt = (int32_t(ctx.gpr[" << ra << "]) < 0);\n";
      out << "    ctx.cr[0].gt = (int32_t(ctx.gpr[" << ra << "]) > 0);\n";
      out << "    ctx.cr[0].eq = (ctx.gpr[" << ra << "] == 0);\n";
      out << "    ctx.cr[0].so = (ctx.xer >> 31) & 1;\n";
    }
    out << "    ctx.pc = 0x" << std::hex << (inst.address+4) << std::dec << "u;\n";
'''+anchor
    assert s.count(anchor)==1;s=s.replace(anchor,new,1);f.write_text(s)
pat=re.compile(r'    nwii::runtime::micro_interpret\(ctx, 0x([0-9A-Fa-f]+), 0x([0-9A-Fa-f]+)\);\n    if \(ctx.pc != 0x[0-9A-Fa-f]+\) return;')
sites=[]
def replace(m):
    word=int(m[1],16);pc=int(m[2],16)
    if word>>26!=23:return m[0]
    rs=(word>>21)&31;ra=(word>>16)&31;rb=(word>>11)&31;mb=(word>>6)&31;me=(word>>1)&31
    mask=sum(1<<(31-bit) for bit in range(32) if (mb<=bit<=me if mb<=me else bit>=mb or bit<=me))
    text=f'    ctx.gpr[{ra}] = std::rotl(ctx.gpr[{rs}], int(ctx.gpr[{rb}] & 31u)) & 0x{mask:X}u; // native rlwnm\n'
    if word&1:text+=f'    ctx.cr[0].lt = (int32_t(ctx.gpr[{ra}]) < 0);\n    ctx.cr[0].gt = (int32_t(ctx.gpr[{ra}]) > 0);\n    ctx.cr[0].eq = (ctx.gpr[{ra}] == 0);\n    ctx.cr[0].so = (ctx.xer >> 31) & 1;\n'
    text+=f'    ctx.pc = 0x{pc+4:X}u;';sites.append(dict(pc=f'{pc:08X}',word=f'{word:08X}'));return text
for f in (p/'build/crash-generated').glob('output_*.cpp'):
    old=f.read_text();s=pat.sub(replace,old)
    if s!=old:f.write_text(s)
if sites:(p/'reports/rlwnm-native-stage.json').write_text(json.dumps(dict(sites=sites,count=len(sites)),indent=2)+'\n')
print('Native rlwnm stage:',len(sites),'new sites; native form retains record flags and PC progression.')
