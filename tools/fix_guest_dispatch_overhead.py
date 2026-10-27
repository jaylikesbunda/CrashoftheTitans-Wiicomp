"""One range-guarded WPAD hook; preserve counters/history, opt-in routine log."""
from pathlib import Path
import re
p=Path(__file__).resolve().parents[1];f=p/'build/crash-generated/main_output.cpp';s=f.read_text();rt=p/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp'
code=rt.read_text();a=code.index('bool native_wpad_dispatch(');b=code.index('// NATIVE_WPAD_END',a);addresses=[int(x,16) for x in re.findall(r'case (0x[0-9A-F]+)',code[a:b])];assert addresses and all(0x803EC364<=addr<=0x803EEF0C for addr in addresses),'WPAD range contract changed'
for qualifier,action in [('nwii::runtime::','continue'),('','return')]:
 old='    if ('+qualifier+'native_wpad_dispatch(ctx,target)) '+action+';'
 guarded='target-0x803EC364u<=0x803EEF0Cu-0x803EC364u && '+qualifier+'native_wpad_dispatch(ctx,target)'
 if old in s:
  assert guarded in s,'Missing guarded hook';s=re.sub(r'^[ \t]*'+re.escape(old.strip())+r'\n','',s,flags=re.M)
old='        if ((++ctx.inst_count % 100000) == 0) std::cout << "Dispatcher PC: 0x" << std::hex << target << std::endl;'
new='        ++ctx.inst_count;\n        if (routine_dispatch_diag && (ctx.inst_count % 100000) == 0) std::cout << "Dispatcher PC: 0x" << std::hex << target << std::endl; // NATIVE_QUIET_DISPATCH_V1'
if old in s:
 anchor='extern "C" void run_game(nwii::runtime::CPUContext& ctx) {';assert s.count(anchor)==1
 s=s.replace(anchor,anchor+'\n    static const bool routine_dispatch_diag=std::getenv("NWII_RUNTIME_DIAG") && !std::getenv("NWII_NO_DIAG");',1).replace(old,new,1)
# Keep scripted input's original initialization time: its state may first be
# touched by a non-WPAD dispatch. Physical input needs only the SDK range.
for signature in ('void native_call_indirect(CPUContext& ctx) {','extern "C" void run_game(nwii::runtime::CPUContext& ctx) {'):
 marker='    static const bool scripted_wpad=std::getenv("NWII_WPAD_SCRIPT") || std::getenv("NWII_WPAD_LIVE");'
 start=s.index(signature);end=s.find('\n}',start)
 if marker not in s[start:end]:s=s[:start]+s[start:].replace(signature,signature+'\n'+marker,1)
s=s.replace('target-0x803EC364u<=0x803EEF0Cu-0x803EC364u &&', '(target-0x803EC364u<=0x803EEF0Cu-0x803EC364u || scripted_wpad) &&')
s=s.replace('pc_history_idx = (pc_history_idx + 1) % 10;', 'if (++pc_history_idx == 10) pc_history_idx = 0; // NATIVE_DISPATCH_HISTORY_V1')
f.write_text(s)
print('Guest dispatcher: one WPAD range hook, unchanged instruction counter/history, routine logging opt-in.')
