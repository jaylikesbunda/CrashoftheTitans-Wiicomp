"""Honor MSR.FP and execute the original guest FPU-unavailable exception path.

No game addresses or guest instruction bytes are changed. HID2 becomes stored
CPU state, enabling the SDK's paired-register save/load conditional. HLE IRQ
context transfers save general registers only; FP state belongs to the SDK's
lazy owner, not necessarily the current thread.
"""
from pathlib import Path
import re,json
from lazy_fpu_header import inject_lazy_fpu_header
PORT=Path(__file__).resolve().parents[1];SRC=PORT/'build/nwii-full-source'
def write(p,s):
    if p.read_text()!=s:p.write_text(s)
p=SRC/'nWiiRuntime/include/runtime/cpu_context.h';s=inject_lazy_fpu_header(p.read_text())
write(p,s)
p=SRC/'nWiiRecomp/src/recompiler.cpp';s=p.read_text()
needle='  if (ppc_inst.opcode() == 14) {'
guard='''  // NATIVE_LAZY_FPU_GUARD
  if (runtime::CPUContext::instruction_uses_fpu(inst.opcode)) {
    out << "    if (ctx.require_fpu(0x" << std::hex << std::uppercase
        << inst.address << std::dec << ")) return; // MSR.FP exception\\n";
  }

'''
# Avoid a new emitter dependency: use the same opcode predicate locally.
guard=guard.replace('runtime::CPUContext::instruction_uses_fpu(inst.opcode)', '''([&](){unsigned op=inst.opcode>>26,xo=(inst.opcode>>1)&1023;
    return (op==4 && xo!=1014) || (op>=48 && op<=57) || op==59 || op==60 || op==61 || op==63 ||
      (op==31 && (xo==535 || xo==567 || xo==599 || xo==631 || xo==663 || xo==695 || xo==727 || xo==759 || xo==983));})()''')
if '// NATIVE_LAZY_FPU_GUARD' not in s:
    assert s.count(needle)==1;s=s.replace(needle,guard+needle,1)
needle='    } else if (spr >= 920 && spr <= 924) {'
if 'ctx.hid2; // mfspr HID2' not in s:
    assert s.count(needle)==1
    s=s.replace(needle,'''    } else if (spr == 920) {
      out << "    ctx.gpr[" << rD << "] = ctx.hid2; // mfspr HID2\\n";
'''+needle,1)
needle='    } else if (spr == 1) {\n      out << "    ctx.xer = ctx.gpr["'
if 'ctx.hid2 = ctx.gpr[' not in s:
    assert s.count(needle)==1
    s=s.replace(needle,'''    } else if (spr == 920) {
      out << "    ctx.hid2 = ctx.gpr[" << rS << "]; // mtspr HID2\\n";
'''+needle,1)
write(p,s)
p=SRC/'nWiiRuntime/src/hle/interpreter.cpp';s=p.read_text()
needle='    uint32_t op = insn >> 26;'
guard='    if (CPUContext::instruction_uses_fpu(insn) && ctx.require_fpu(pc)) return true; // NATIVE_LAZY_FPU_INTERPRETER\n'
if '// NATIVE_LAZY_FPU_INTERPRETER' not in s:
    assert s.count(needle)==1;s=s.replace(needle,guard+needle,1)
if 'else if (spr == 920) ctx.gpr[rD] = ctx.hid2;' not in s:
    s=s.replace('            else ctx.gpr[rD] = 0;','            else if (spr == 920) ctx.gpr[rD] = ctx.hid2;\n            else ctx.gpr[rD] = 0;',1)
if 'else if (spr == 920) ctx.hid2 = ctx.gpr[rD];' not in s:
    needle='            else if (spr >= 912 && spr <= 919) ctx.gqr[spr - 912] = ctx.gpr[rD];'
    assert s.count(needle)==1;s=s.replace(needle,needle+'\n            else if (spr == 920) ctx.hid2 = ctx.gpr[rD];',1)
if 'else if (spr >= 272 && spr <= 275) ctx.gpr[rD] = ctx.sprg[spr-272];' not in s:
    s=s.replace('            else if (spr == 920) ctx.gpr[rD] = ctx.hid2;',
                '            else if (spr >= 272 && spr <= 275) ctx.gpr[rD] = ctx.sprg[spr-272];\n            else if (spr == 920) ctx.gpr[rD] = ctx.hid2;',1)
if 'else if (spr >= 272 && spr <= 275) ctx.sprg[spr-272] = ctx.gpr[rD];' not in s:
    s=s.replace('            else if (spr == 920) ctx.hid2 = ctx.gpr[rD];',
                '            else if (spr >= 272 && spr <= 275) ctx.sprg[spr-272] = ctx.gpr[rD];\n            else if (spr == 920) ctx.hid2 = ctx.gpr[rD];',1)
write(p,s)
p=SRC/'nWiiRuntime/src/hle/ios.cpp';s=p.read_text()
a=s.index('static void hle_save_context_to_guest');b=s.index('int g_trace_after_dec',a)
body=s[a:b]
if '// NATIVE_GENERAL_CONTEXT_ONLY' not in s:
    body,n=re.subn(r'  for \(int i = 0; i < 32; i\+\+\) \{\n    uint64_t d;?[^}]+?\n  \}', '',body)
    assert n==4,n
    body=body.replace('  ctx.mmu.write32(c + 0x194, ctx.fpscr);','')
    body=body.replace('  ctx.fpscr = ctx.mmu.read32(c + 0x194);','')
    body='// NATIVE_GENERAL_CONTEXT_ONLY: original SDK manages lazy physical FP state.\n'+body
    s=s[:a]+body+s[b:]
write(p,s)
def uses(w):
    op=w>>26;xo=(w>>1)&1023
    return (op==4 and xo!=1014) or 48<=op<=57 or op in (59,60,61,63) or (op==31 and xo in (535,567,599,631,663,695,727,759,983))
counts={'guards':0,'hid2_reads':0,'hid2_writes':0,'chunks':0}
for p in (PORT/'build/crash-generated').glob('output_*.cpp'):
    s=p.read_text()
    # Canonicalize guards so repeated staging is idempotent.
    s=re.sub(r'    if \(ctx.require_fpu\(0x[0-9A-F]+\)\) return; // MSR.FP exception\n','',s)
    s=re.sub(r'    if \(ctx.require_fpu\(0x[0-9A-F]+\)\) \{ ctx.inst_count -= \d+; return; \} // MSR.FP exception\n','',s)   # slimmed form (fix_codegen_slim.py)
    def prefix(m):
        pc,w=m.group(1),int(m.group(2),16)
        if uses(w):
            counts['guards']+=1
            return m.group(0)+f'    if (ctx.require_fpu(0x{pc})) return; // MSR.FP exception\n'
        return m.group(0)
    s=re.sub(r'    // 0x([0-9A-F]{8}): ([0-9A-F]{8})\n',prefix,s)
    def hid(m):
        w=int(m.group(1),16);xo=(w>>1)&1023;spr=((w>>16)&31)|(((w>>11)&31)<<5);r=(w>>21)&31
        if w>>26!=31 or spr!=920:return m.group(0)
        if xo==339:
            counts['hid2_reads']+=1;line=f'    ctx.gpr[{r}] = ctx.hid2; // mfspr HID2'
        elif xo==467:
            counts['hid2_writes']+=1;line=f'    ctx.hid2 = ctx.gpr[{r}]; // mtspr HID2'
        else:return m.group(0)
        return m.group(0).split(chr(10))[0]+chr(10)+line+chr(10)
    s=re.sub(r'    // 0x[0-9A-F]{8}: ([0-9A-F]{8})\n[^\n]*\n',hid,s)
    write(p,s);counts['chunks']+=1
(PORT/'reports/lazy-fpu-patch.json').write_text(json.dumps(counts,indent=2)+'\n')
print('Lazy FPU ownership staged:',counts)
