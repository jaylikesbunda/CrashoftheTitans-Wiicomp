"""Slimmer generated code: no per-call trace test, one instruction-count update per straight-line run.

Measured on output_71.cpp (11,836 guest instructions): .text is ~106 bytes per guest instruction and the whole executable has 139 MB of
.text, so the hot set does not fit Zen 2's 32 KiB L1I / 512 KiB L2 / 16 MiB L3 slices and instruction fetch dominates.

1. NWII_TRACE_CALL(addr, ctx): every function began with `if (g_trace_calls && ctx.pc == ADDR) trace_call(ADDR, ctx);` (a global load,
   a test and a compare at every call). It is now a macro that is empty unless the build defines NWII_TRACE_CALLS.
2. Instruction count. Original semantics: each guest instruction is `++ctx.inst_count; loc_X: ;`, so the increment runs when control
   falls into a label from the previous instruction (and at function entry) and is skipped when the label is entered by a goto or the
   entry switch. Each straight-line run (the run start plus the following instructions that are neither goto/kept-entry targets nor
   run-ending) now keeps the run start's own `++` and adds `ctx.inst_count += N-1;` right after the run start's label for the rest.
   A run ends after any instruction that can leave it or observe the count (goto, return, a call, polling, ctx.pc writes, interpret,
   timebase reads, native_ helpers). The one exception is the FP-unavailable early return `if (ctx.require_fpu(A)) return;`: it
   stays inside a run and subtracts the instructions that were pre-counted but not executed. The count is therefore exact whenever
   control leaves a run or the count is observed. It is only ahead of the original inside a run (by fewer than one run length) for an
   MMIO side effect in virtual-clock mode (NWII_WALLTB unset) or for a guest exception that unwinds with longjmp.

Run after fix_prune_entry_cases.py. A snapshot of each chunk as it was before this stage is written to build/slim-before for
tools/check_codegen_slim.py. output_74.cpp (pinned bounded blocks) is not edited.
"""
from pathlib import Path
import re,shutil
p=Path(__file__).resolve().parents[1];gen=p/'build/crash-generated';snap=p/'build/slim-before'
rt=p/'build/nwii-full-source/nWiiRuntime'
FUNC=re.compile(r'^void (func_[0-9A-F]{8})\(NWII_CTX_PARAM ctx\) \{\n',re.M)
UNIT=re.compile(r'    \+\+ctx\.inst_count;\nloc_([0-9A-F]{8}): ;\n')
TRACE=re.compile(r'    if \(nwii::runtime::g_trace_calls && ctx\.pc == (0x[0-9A-F]{8})\) nwii::runtime::trace_call\(\1, ctx\);\n')
FPRET=re.compile(r'if \(ctx\.require_fpu\((0x[0-9A-F]{8})\)\) return;')
FPLINE=re.compile(r'^ *if \(ctx\.require_fpu\(0x[0-9A-F]{8}\)\) return;[^\n]*\n',re.M)
BREAK=('goto ','return','func_','native_','process_pending','interpret','inst_count','ctx.pc','read_timebase','switch','trace','queue','throw','setjmp','longjmp','msr')
NL=chr(10)
def code_of(text):
    return NL.join(line.split('//',1)[0] for line in text.split(NL))
def unit_kind(text):
    """'plain' stays inside a run, 'fp' is plain except for the FP-unavailable early return, 'break' ends the run."""
    code=code_of(text)
    if any(token in code for token in BREAK if token!='return'):return 'break'
    if 'return' not in code:return 'plain'
    return 'fp' if 'return' not in FPRET.sub('',code) else 'break'
def batch_function(body):
    units=list(UNIT.finditer(body))
    if len(units)<2:return body,0
    targets=set(re.findall(r'goto loc_([0-9A-F]{8})',body))
    count=len(units)
    starts=[m.start() for m in units]+[len(body)]
    heads=[body[m.start():m.end()] for m in units]
    stmts=[body[m.end():starts[i+1]] for i,m in enumerate(units)]
    labels=[m[1] for m in units]
    kinds=[unit_kind(stmts[i]) for i in range(count)]
    for i in range(count):
        if re.search(r'^loc_',stmts[i],re.M):kinds[i]='break'   # a label inside a statement would make the batching non-invertible (unslim)
    # Bounded-block regions are pinned by hash (fix_bounded_blocks.py): every unit between a region's first label and its resume
    # label stays byte-for-byte as it was, so each is forced to be its own run.
    for i in range(count):
        if 'NATIVE_BOUNDED_BLOCKS_V1' in stmts[i]:
            target=re.search(r'goto loc_([0-9A-F]{8});',stmts[i])
            if target:
                j=i+1
                while j<count and labels[j]!=target[1]:
                    kinds[j]='break';j+=1
    out=[body[:starts[0]]];runs=0;i=0
    while i<count:
        j=i
        while kinds[j]!='break' and j+1<count and labels[j+1] not in targets:j+=1
        n=j-i+1
        out.append(heads[i])                       # the run start keeps its own '++' (skipped on a goto, exactly as before)
        if n>1:out.append('    ctx.inst_count += '+str(n-1)+';'+NL)
        fp_checked=False
        for k in range(n):
            text=stmts[i+k];remaining=n-1-k
            if kinds[i+k]=='fp':
                if fp_checked:
                    # MSR.FP cannot change inside a run (no call, poll, msr access or ctx.pc write), and the first FP
                    # instruction of the run already proved it set: the later per-instruction tests are redundant.
                    text=FPLINE.sub('',text)
                else:
                    fp_checked=True
                    if remaining>0:
                        text=FPRET.sub(lambda m:'if (ctx.require_fpu('+m[1]+')) { ctx.inst_count -= '+str(remaining)+'; return; }',text)
            if k:out.append('loc_'+labels[i+k]+': ;'+NL)
            out.append(text)
        runs+=1;i=j+1
    return ''.join(out),runs
SLIM_GUARD=re.compile(r'if \(ctx\.require_fpu\((0x[0-9A-F]{8})\)\) \{ ctx\.inst_count -= \d+; return; \}')
LABEL_LINE=re.compile(r'loc_[0-9A-F]{8}: ;')
BATCH_LINE=re.compile(r'    ctx\.inst_count \+= (\d+);')
def unslim(text):
    """Exact inverse of the batching: a run start is `++` + label + `ctx.inst_count += M;` and the M labels that follow lost their own `++`
    (runs never contain other labels, see batch_function), so restore it for exactly those, and turn the subtracting FP guard back into
    the plain one. Staging twice therefore gives the same text, and the pre-slim snapshot is the true original."""
    lines=text.split(NL);out=[];pending=0;i=0
    while i<len(lines):
        line=lines[i]
        if pending and LABEL_LINE.fullmatch(line):
            out.append('    ++ctx.inst_count;');pending-=1
        m=BATCH_LINE.fullmatch(line)
        if m and out and LABEL_LINE.fullmatch(out[-1]) and len(out)>1 and out[-2]=='    ++ctx.inst_count;':
            pending=int(m[1]);i+=1;continue
        out.append(line);i+=1
    return SLIM_GUARD.sub(lambda m:'if (ctx.require_fpu('+m[1]+')) return;',NL.join(out))
def process(text):
    stats={'trace':0,'functions':0,'units':0,'runs':0}
    text,n=TRACE.subn(lambda m:'    NWII_TRACE_CALL('+m[1]+', ctx);'+NL,text);stats['trace']=n
    pieces=[];pos=0
    for m in FUNC.finditer(text):
        z=text.find(NL+'}'+NL,m.end())
        if z<0:continue
        body=text[m.end():z+1]
        new,runs=batch_function(body)
        found=len(UNIT.findall(body))
        stats['functions']+=1;stats['units']+=found;stats['runs']+=runs if runs else found
        pieces.append(text[pos:m.end()]);pieces.append(new);pos=z+1
    pieces.append(text[pos:])
    return ''.join(pieces),stats
if __name__=='__main__':
    h=rt/'include/runtime/cpu_context.h';s=h.read_text()
    if 'NWII_TRACE_CALL' not in s:
        anchor='bool process_pending_callbacks(CPUContext &ctx);'+NL
        assert s.count(anchor)==1
        s=s.replace(anchor,anchor+'// NATIVE_CODEGEN_SLIM_V1: the per-call trace test is compiled out unless NWII_TRACE_CALLS is defined.'+NL+
          '#ifdef NWII_TRACE_CALLS'+NL+
          '#define NWII_TRACE_CALL(address, context) do { if (nwii::runtime::g_trace_calls && (context).pc == (address)) nwii::runtime::trace_call((address), (context)); } while (0)'+NL+
          '#else'+NL+'#define NWII_TRACE_CALL(address, context) ((void)0)'+NL+'#endif'+NL);h.write_text(s)
    shutil.rmtree(snap,ignore_errors=True);snap.mkdir(parents=True)
    total={'trace':0,'functions':0,'units':0,'runs':0};changed=0
    for f in sorted(gen.glob('output_*.cpp'))+[gen/'main_output.cpp']:
        if f.name=='output_74.cpp':continue
        current=f.read_text(errors='surrogateescape');s=unslim(current)
        (snap/f.name).write_text(s,errors='surrogateescape')
        t,st=process(s)
        for k in total:total[k]+=st[k]
        if t!=current:f.write_text(t,errors='surrogateescape');changed+=1
    print('Codegen slimming: '+str(total['trace'])+' trace tests removed; '+str(total['units'])+' instruction-count updates -> '+str(total['runs'])+' runs across '+str(total['functions'])+' functions; '+str(changed)+' chunks rewritten.')
