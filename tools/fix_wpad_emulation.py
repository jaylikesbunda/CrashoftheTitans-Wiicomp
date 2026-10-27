"""Connect opt-in retail WPAD emulation at SDK entries, preserving original KPAD."""
from pathlib import Path
import json
import re


def ensure_wpad_tail(source):
    """Keep one tail poll, respecting the fast path's already-polled parameter."""
    needle = '  if (ctx.pending_cb_count.load(std::memory_order_relaxed) == 0)\n'
    if source.count(needle) != 1:
        raise RuntimeError(f'WPAD callback-tail anchor count {source.count(needle)}')
    start = source.index(needle)
    prefix = source[:start]
    polls = re.search(r'(?:  (?:if \(!wpad_polled\) )?native_wpad_poll\(ctx\);\n)+$', prefix)
    conditional = 'native_process_callbacks_slow(CPUContext &ctx, bool poll_clock_due, bool wpad_polled)' in source
    poll = '  if (!wpad_polled) native_wpad_poll(ctx);\n' if conditional else '  native_wpad_poll(ctx);\n'
    if polls:
        prefix = prefix[:polls.start()]
    return prefix + poll + source[start:]


PORT=Path(__file__).resolve().parents[1]
addresses=(0x803EC524,0x803ECE80,0x803EC5B0,0x803EC618,0x803EC680,0x803EC6E8,0x803EC730,0x803ED020,0x803EC364,0x803EEE98,0x803EEF0C,0x803EC7E0,0x803EC898)
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp';s=p.read_text()
begin='// NATIVE_WPAD_BEGIN\n';end='// NATIVE_WPAD_END\n'
block=begin+(PORT/'runtime-overlays/wpad_emulation.inc').read_text()+end
# Include standard headers at global scope, not inside the runtime namespace.
block=block.replace('#include <fstream>\n#include <sstream>\n','')
for header in ('fstream','sstream','vector','chrono'):
    if f'#include <{header}>' not in s:s=f'#include <{header}>\n'+s
if '#include "input/input_manager.h"' not in s:s='#include "input/input_manager.h"\n'+s
if begin in s:
    a=s.index(begin);b=s.index(end,a)+len(end);s=s[:a]+block+s[b:]
else:
    needle='bool process_pending_callbacks(CPUContext &ctx) {'
    assert s.count(needle)==1;s=s.replace(needle,block+'\n'+needle,1)
s=ensure_wpad_tail(s)
if p.read_text()!=s:p.write_text(s)
declaration='namespace nwii::runtime { bool native_wpad_dispatch(CPUContext&, uint32_t); }\n'
old='        const auto dispatch = find_dispatch_target(target);\n'
new='        if (target-0x803EC364u<=0x803EEF0Cu-0x803EC364u && nwii::runtime::native_wpad_dispatch(ctx,target)) continue; // NATIVE_WPAD_RANGE_V1\n'+old
p=PORT/'build/crash-generated/main_output.cpp';s=p.read_text()
s=s.replace(declaration,'')
marker='// --- Entry Point Wrapper ---\n'
assert s.count(marker)==1;s=s.replace(marker,declaration+marker,1)
if '&& nwii::runtime::native_wpad_dispatch(ctx,target)) continue;' not in s:
    assert s.count(old)==1;s=s.replace(old,new,1)
if p.read_text()!=s:p.write_text(s)
def emit(t):return ''.join('    out << "'+line.replace('\\','\\\\').replace('"','\\"').replace('\n','\\n')+'";\n' for line in t.splitlines(True))
p=PORT/'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp';s=p.read_text()
preamble='    out << "using namespace nwii::runtime;\\n\\n";\n'
s=s.replace(emit(declaration)+preamble,preamble)
needle=emit(marker)
assert s.count(needle)==2
if emit(declaration)+needle not in s:s=s.replace(needle,emit(declaration)+needle)
if emit(new) not in s:
    assert s.count(emit(old))==1;s=s.replace(emit(old),emit(new),1)
if p.read_text()!=s:p.write_text(s)
sites=0
for p in (PORT/'build/crash-generated').glob('output_*.cpp'):
    s=p.read_text();old_s=s
    for address in addresses:
        needle=f'void func_{address:08X}(CPUContext& ctx) {{\n'
        guard=f'    if (ctx.pc == 0x{address:08X} && nwii::runtime::native_wpad_dispatch(ctx,0x{address:08X})) return; // NATIVE_WPAD_ENTRY\n'
        if needle in s:
            sites+=1
            if guard not in s:s=s.replace(needle,declaration+needle+guard,1)
    if s!=old_s:p.write_text(s)
assert sites==len(addresses),sites
entry_marker='  // NATIVE_WPAD_ENTRY_EMITTER\n'
p=PORT/'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp';s=p.read_text()
if entry_marker in s:
    import re
    condition=' || '.join(f'func.start_address==0x{a:08X}' for a in addresses)
    s,n=re.subn(r'  const bool native_wpad_entry = [^;]+;',f'  const bool native_wpad_entry = {condition};',s)
    assert n==1
    if p.read_text()!=s:p.write_text(s)
else:
    needle='  out << "void " << func_name << "(CPUContext& ctx) {\\n";\n'
    assert s.count(needle)==1
    condition=' || '.join(f'func.start_address==0x{a:08X}' for a in addresses)
    replacement=entry_marker+f'  const bool native_wpad_entry = {condition};\n'
    replacement+='  if (native_wpad_entry) {\n'+emit(declaration)+'  }\n'+needle
    replacement+='  if (native_wpad_entry) {\n    out << "    if (ctx.pc == 0x" << std::hex << std::uppercase << func.start_address\n'
    replacement+='        << " && nwii::runtime::native_wpad_dispatch(ctx,0x" << func.start_address << std::dec\n'
    replacement+='        << ")) return; // NATIVE_WPAD_ENTRY\\n";\n  }\n'
    s=s.replace(needle,replacement,1);p.write_text(s)
(PORT/'reports/wpad-entry-sites.json').write_text(json.dumps({'guarded_sdk_entries':sites,'addresses':[f'{a:08X}' for a in addresses],'scope':'Direct translated calls and dispatcher calls; only exact SDK entry is intercepted. Interior continuations remain original.'},indent=2)+'\n')
print('Opt-in WPAD script bridge staged; original KPAD and guest code retained.')
