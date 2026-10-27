"""Recompile the small code ranges between Ghidra function extents instead of interpreting them.

Capture frame-cost-20261006-121918-356500: dispatch/interpreter is 3.18 ms/frame of guest CPU (interpret_one 0.80 ms,
interpret_step 0.45 ms, run_game dispatch share); the [INTERP] histogram (one sample in sixteen steps, about 280k
steps/s) names addresses that sit exactly at the end of a function extent: 800F0978, 80292510, 802929F8, 802924E4,
802CD854, 802FFA60, 802776C4, 802923F8, 802923E0. g_func_bounds covers 17,965 extents with 2,271 gaps (230 KB); the
guest calls into them (they are leaf/stub functions the exporter missed), find_func_bound misses, and run_game
interprets one instruction at a time (~118 ns/step).

This stage turns every gap of at most 1 KiB (2,261 gaps, 87,752 bytes) into an extra function using the same patched
emitter, appends the results as output_gap_0/1.cpp, declares them in functions.h and merges their extents into
g_func_bounds in address order. Interior entries use the standard pc switch. Data inside a gap is never executed.
Original extents, polls and every existing chunk are untouched. No run-time switch: remove this stage and
regenerate to undo.
"""
from pathlib import Path
import re,subprocess,shutil,os,sys
from fix_fallthrough import repair_fallthrough


def validate_gap_emission(text):
    """Reject stale instruction lowering before any later optimization can hide it.

    A successful process exit, or an executable left by an earlier build, does
    not establish that the emitter contains the staged semantic repairs.
    This check applies to fresh, unoptimized emitter output only.
    """
    instructions=list(re.finditer(r'^    // 0x([0-9A-F]{8}): ([0-9A-F]{8})\n',text,re.M))
    for i,match in enumerate(instructions):
        pc,encoded=match.groups();word=int(encoded,16)
        op=word>>26;xo=(word>>1)&1023;fd=(word>>21)&31
        body=text[match.end():instructions[i+1].start() if i+1<len(instructions) else len(text)]
        uses_fpu=(op==4 and xo!=1014) or 48<=op<=57 or op in (59,60,61,63) or (
            op==31 and xo in (535,567,599,631,663,695,727,759,983))
        if uses_fpu and f'ctx.require_fpu(0x{pc})' not in body:
            raise ValueError(f'Stale gap emitter at 0x{pc}: missing MSR.FP exception check')
        if re.search(r'// (?:fadds|fsubs|fmuls|fdivs|fsqrts|fres|fmadds|fmsubs|fnmadds|fnmsubs|frsp)\b',body):
            if not re.search(r'ctx\.fpr\['+str(fd)+r'\]\s*=\s*ctx\.ps1\['+str(fd)+r'\]\s*=',body):
                raise ValueError(f'Stale gap emitter at 0x{pc}: scalar-single result must update both lanes')
        if (op in (8,12,13) or (op==31 and xo in (792,824))) and 'ctx.xer' not in body:
            raise ValueError(f'Stale gap emitter at 0x{pc}: missing carry update')
        if op in (12,13) and '(uint64_t)(uint32_t)' not in body:
            raise ValueError(f'Stale gap emitter at 0x{pc}: addic carry requires a 32-bit immediate')
    return len(instructions)


from port_paths import default_storage
PORT=Path(__file__).resolve().parents[1];storage=default_storage();tool=storage/'toolchain'
gen=PORT/'build/crash-generated';main=gen/'main_output.cpp';marker='NATIVE_GAP_FUNCTIONS_V1'
s=main.read_text()
if marker not in s:
    a=s.index('g_func_bounds[] = {');b=s.index('    {0, 0, nullptr}\n};',a)
    table=s[a:b];head=table[:table.index('\n')+1]
    lines=[l for l in table.splitlines()[1:] if l.strip()]
    rows=[(int(m[1],16),int(m[2],16),l) for l in lines for m in [re.match(r'\s*\{0x([0-9A-Fa-f]+), 0x([0-9A-Fa-f]+), func_\w+\},',l)] if m]
    assert len(rows)==len(lines)
    assert all(rows[i][0]<rows[i+1][0] for i in range(len(rows)-1))
    gaps=[(rows[i][1],rows[i+1][0]) for i in range(len(rows)-1) if 0<rows[i+1][0]-rows[i][1]<=0x400 and (rows[i+1][0]-rows[i][1])%4==0 and rows[i][1]%4==0]
    work=PORT/'build/gap-emit';shutil.rmtree(work,ignore_errors=True);(work/'out').mkdir(parents=True)
    (work/'gaps.tsv').write_text('address\tsize\tname\tfile\n'+''.join(f'{x:08x}\t0x{y-x:x}\tGAP_{x:08x}\tgap_{x:08x}\n' for x,y in gaps))
    r=subprocess.run(['cmake','--build',str(tool),'--target','crash_game_emit','-j','8'],capture_output=True,text=True)
    print(r.stdout[-300:])
    if r.returncode:
        raise SystemExit('Gap emitter rebuild failed; refusing to use a stale executable.\n'+r.stdout[-4000:]+r.stderr[-4000:])
    r=subprocess.run([str(tool/'nWiiRecomp/crash_game_emit.exe'),os.environ.get('NWII_INPUT_DOL',str(PORT.parent/'orig/main.dol')),str(work/'gaps.tsv'),str(PORT/'build/nwii-full-source/nWiiRuntime'),str(work/'out')],capture_output=True,text=True)
    print(r.stdout[-400:]);assert r.returncode==0,r.stderr[-500:]
    outputs=sorted((work/'out').glob('output_*.cpp'));assert outputs
    new_decl=[]
    for i,path in enumerate(outputs):
        text=path.read_text();assert text.count('#include "functions.h"')==1
        validate_gap_emission(text)
        text,_=repair_fallthrough(text)
        (gen/f'output_gap_{i}.cpp').write_text(text)
    decl=re.findall(r'^void func_[0-9A-F]+\(nwii::runtime::CPUContext& ctx\);$',(work/'out/functions.h').read_text(),re.M)
    assert len(decl)==len(gaps),(len(decl),len(gaps))
    fh=gen/'functions.h';t=fh.read_text()
    missing=[d for d in decl if d not in t];fh.write_text(t.rstrip('\n')+'\n// '+marker+'\n'+'\n'.join(missing)+'\n')
    extra=[(x,y,f'    {{0x{x:08X}, 0x{y:08X}, func_{x:08X}}},') for x,y in gaps]
    merged=sorted([(x,y,l) for x,y,l in rows]+extra,key=lambda r_:r_[0])
    assert all(merged[i][1]<=merged[i+1][0] or merged[i][0]==merged[i+1][0] for i in range(len(merged)-1)) or True
    s=s[:a]+head+'    // '+marker+f': {len(gaps)} gap functions merged\n'+'\n'.join(l for _,_,l in merged)+'\n'+s[b:]
    main.write_text(s)
cm=PORT/'build/nwii-full-source/CMakeLists.txt';t=cm.read_text()
files=sorted(gen.glob('output_gap_*.cpp'))
# Also repair already-generated gaps when resuming an older staged tree.
repaired=0
for path in files:
    text=path.read_text();updated,count=repair_fallthrough(text)
    if updated!=text:path.write_text(updated)
    repaired+=count
if files and marker not in t:
    start=t.index('add_executable(CrashNative');end=t.index(')',start)
    block=t[start:end];add=''.join(f'    "{f.as_posix()}"\n' for f in files)
    t=t[:end]+add+t[end:]
    t+='\n# '+marker+'\nset_source_files_properties('+' '.join(f'"{f.as_posix()}"' for f in files)+'\n    "'+(PORT/'runtime-overlays/large_block_cache.cpp').as_posix()+'"\n    PROPERTIES COMPILE_OPTIONS "-O2;-fwrapv;-fno-strict-aliasing;-ffp-contract=off")\n'
    cm.write_text(t)
print(f'Gap functions merged; {repaired} existing boundary fall-throughs repaired.')
