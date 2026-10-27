"""Preserve range dispatch semantics while removing the full-map linear scan."""
from pathlib import Path
import re
PORT=Path(__file__).resolve().parents[1]
helper=(PORT/'runtime-overlays/function_lookup.inc').read_text()
old='''                bool found = false;
                for (const auto& fb : g_func_bounds) {
                    if (fb.start == 0) break;
                    if (target >= fb.start && target < fb.end) {
                        ctx.pc = target;
                        fb.func(ctx);
                        found = true;
                        break;
                    }
                }
'''
new='''                const auto* fb = find_func_bound(target);
                bool found = fb != nullptr;
                if (fb) {
                    ctx.pc = target;
                    fb->func(ctx);
                }
'''
p=PORT/'build/crash-generated/main_output.cpp';s=p.read_text()
bounds=[(int(a,16),int(b,16)) for a,b in re.findall(r'\{0x([0-9A-Fa-f]+), 0x([0-9A-Fa-f]+), func_',s)]
if not bounds or any(a>=b for a,b in bounds) or any(bounds[i][0]>=bounds[i+1][0] for i in range(len(bounds)-1)):
    raise SystemExit('Binary range dispatch requires increasing starts and nonempty extents')
if old in s:s=s.replace(old,new,1)
elif new not in s and 'NATIVE_SMALL_DISPATCH_CALL_V1' not in s:raise SystemExit('Dispatcher default context changed')
marker='// --- Entry Point Wrapper ---\n'
if 'NATIVE_BINARY_FUNCTION_LOOKUP_V1' not in s:s=s.replace(marker,helper+'\n'+marker,1)
if p.read_text()!=s:p.write_text(s)
p=PORT/'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp';s=p.read_text()
def emit(text):return ''.join('    out << "'+line.replace('\\','\\\\').replace('"','\\"').replace('\n','\\n')+'";\n' for line in text.splitlines(True))
before=emit(old);after=emit(new)
if before in s:s=s.replace(before,after,1)
elif after not in s and 'NATIVE_SMALL_DISPATCH_EMITTER_V1' not in s:raise SystemExit('Emitter default context changed')
insertion=emit(helper+'\n')
needle='    out << "// --- Entry Point Wrapper ---\\n";\n'
if 'NATIVE_BINARY_FUNCTION_LOOKUP_V1' not in s:
    if needle not in s:raise SystemExit('Emitter wrapper marker changed')
    s=s.replace(needle,insertion+needle,1)
if p.read_text()!=s:p.write_text(s)
print(f'Binary range lookup staged for {len(bounds)} sorted functions, preserving overlap priority and interpreter fallback.')
