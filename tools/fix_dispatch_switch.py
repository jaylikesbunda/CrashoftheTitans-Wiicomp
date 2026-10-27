"""Replace unoptimized giant switch with an equivalent small override lookup."""
from pathlib import Path
import bisect,json,re
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/crash-generated/main_output.cpp';s=p.read_text()
choice=(PORT/'runtime-overlays/dispatch_choice.inc').read_text()
body='''        // NATIVE_SMALL_DISPATCH_CALL_V1
        const auto dispatch = find_dispatch_target(target);
        if (dispatch.func) {
            ctx.pc = target;
            dispatch.func(ctx);
            if (dispatch.hle) ctx.pc = ctx.lr;
        } else {
            nwii::runtime::interpret_step(ctx);
        }
'''
def emit(t):return ''.join('    out << "'+line.replace('\\','\\\\').replace('"','\\"').replace('\n','\\n')+'";\n' for line in t.splitlines(True))
if 'NATIVE_SMALL_DISPATCH_V1' not in s:
    bounds=[(int(a,16),int(b,16),f) for a,b,f in re.findall(r'\{0x([0-9A-Fa-f]+), 0x([0-9A-Fa-f]+), (func_[0-9A-Fa-f]+)\}',s)]
    high=0;ends=[]
    for a,b,f in bounds:high=max(high,b);ends.append(high)
    cases=[(int(pc,16),f,bool(hle)) for pc,f,hle in re.findall(r'case 0x([0-9A-Fa-f]+): ([\w]+)\(ctx\);( ctx.pc = ctx.lr;)? break;',s)]
    if len(cases)<len(bounds):raise SystemExit('Original switch cases missing')
    overrides=[]
    for pc,f,hle in sorted(cases):
        i=bisect.bisect_right(ends,pc)
        owner=bounds[i][2] if i<len(bounds) and bounds[i][0]<=pc else None
        if owner!=f or hle:overrides.append((pc,f,hle))
    base=PORT/'build/lookup-tests';base.mkdir(parents=True,exist_ok=True)
    (base/'original-switch-cases.json').write_text(json.dumps(cases))
    table='// NATIVE_SMALL_DISPATCH_V1\nstruct DispatchChoice { uint32_t pc; void(*func)(CPUContext&); bool hle; };\nstatic const DispatchChoice g_dispatch_overrides[] = {\n'
    table+=''.join(f'    {{0x{pc:X}, {f}, {str(hle).lower()}}},\n' for pc,f,hle in overrides)
    table+='    {0, nullptr, false}\n};\n'+choice+'\n'
    s=s.replace('// --- Entry Point Wrapper ---\n',table+'// --- Entry Point Wrapper ---\n',1)
    a=s.index('        switch (target) {\n');b=s.index('      } else {\n',a)
    s=s[:a]+body+s[b:];p.write_text(s)
    (PORT/'reports/dispatch-switch-inventory.json').write_text(json.dumps({'original_cases':len(cases),'overrides':len(overrides),'functions':len(bounds)},indent=2)+'\n')
p=PORT/'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp';s=p.read_text()
if 'NATIVE_SMALL_DISPATCH_EMITTER_V1' not in s:
    insertion=(PORT/'runtime-overlays/dispatch_emit.inc').read_text()+emit(choice+'\n')
    needle='    out << "// --- Entry Point Wrapper ---\\n";\n'
    if needle not in s:raise SystemExit('Wrapper context changed')
    s=s.replace(needle,insertion+needle,1)
    a=s.index('    out << "        switch (target) {\\n";\n');b=s.index('    out << "      } else {\\n";\n',a)
    s=s[:a]+emit(body)+s[b:];p.write_text(s)
print('Small switch-equivalent dispatch staged; only original case/range differences retained.')
