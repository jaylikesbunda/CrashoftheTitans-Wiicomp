"""Preserve PPC fall-through across imported function extents.

Patches the emitter and existing text output equivalently, avoiding a full
re-emission for this one control-flow correction. No original binary changes.
"""
from pathlib import Path
import json,re
PORT=Path(__file__).resolve().parents[1]
needle='    emit_instruction(out, inst, func);\n  }\n\n  out << "}\\n\\n";'
insert='''    emit_instruction(out, inst, func);
  }

  // Imported boundaries may split multi-entry compiler save/restore helpers.
  // Reaching a boundary advances the guest PC; it is not a guest return.
  if (!func.instructions.empty()) {
    uint32_t word = func.instructions.back().opcode;
    uint32_t op = word >> 26, xo = (word >> 1) & 1023, bo = (word >> 21) & 31;
    bool terminal = (op == 18 && !(word & 1)) ||
        (op == 19 && xo == 50) ||
        (op == 19 && (xo == 16 || xo == 528) && (bo & 20) == 20 && !(word & 1));
    if (!terminal)
      out << "    ctx.pc = 0x" << std::hex << std::uppercase << func.end_address
          << std::dec << "; // mapped boundary fall-through\\n";
  }

  out << "}\\n\\n";'''
pattern=re.compile(r'(void func_[0-9A-F]+\((?:nwii::runtime::)?CPUContext& ctx\) \{\n|void func_[0-9A-F]+\(NWII_CTX_PARAM ctx\) \{\n)(.*?)(^\})',re.S|re.M)

def repair_fallthrough(text):
    """Repair nonterminal extent ends, including freshly emitted gap functions."""
    count=0
    def fix(m):
        nonlocal count
        body=m[2]
        instructions=re.findall(r'// 0x([0-9A-F]{8}): ([0-9A-F]{8})',body)
        if not instructions:return m[0]
        pc,word=map(lambda x:int(x,16),instructions[-1])
        op=word>>26;xo=(word>>1)&1023;bo=(word>>21)&31
        terminal=(op==18 and not(word&1)) or (op==19 and xo==50) or (op==19 and xo in (16,528) and bo&20==20 and not(word&1))
        if terminal:return m[0]
        if '// mapped boundary fall-through' in body:
            if f'ctx.pc = 0x{pc+4:X}; // mapped boundary fall-through' not in body:
                raise ValueError(f'Incorrect boundary fall-through after 0x{pc:08X}')
            return m[0]
        count+=1
        return m[1]+body+f'    ctx.pc = 0x{pc+4:X}; // mapped boundary fall-through\n'+m[3]
    return pattern.sub(fix,text),count

def main():
    p=PORT/'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp'
    s=p.read_text()
    if needle in s:p.write_text(s.replace(needle,insert))
    elif '// Imported boundaries may split' not in s:raise SystemExit('Emitter patch context changed')
    changed=0;file_count=0;counts=[];patched=0
    for p in sorted((PORT/'build/crash-generated').glob('output_*.cpp')):
        original=p.read_text()
        updated,count=repair_fallthrough(original)
        patched+=updated.count('// mapped boundary fall-through')
        if updated!=original:
            p.write_text(updated);file_count+=1;changed+=count;counts.append({'chunk':p.name,'functions':count})
    result={'changed_functions':changed,'changed_chunks':file_count,'patched_functions':patched,'chunks':counts}
    report=PORT/'reports/fallthrough-patch.json'
    if changed or not report.exists():report.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='chunks'},indent=2))

if __name__=='__main__':main()
