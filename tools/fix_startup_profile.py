"""Opt-in CPU-owned callback-point sampling, bounded stacks and thread waits."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp'
s=p.read_text()
helper=(PORT/'runtime-overlays/startup_profile.inc').read_text()
begin='  // NATIVE_STARTUP_PROFILE_BEGIN\n'
end='  // NATIVE_STARTUP_PROFILE_END\n'
block=begin+helper+'\n'+end
if begin in s:
    a=s.index(begin);b=s.index(end,a)+len(end);s=s[:a]+block+s[b:]
else:
    needle='bool process_pending_callbacks(CPUContext &ctx) {\n'
    if s.count(needle)!=1:raise SystemExit('Callback context changed')
    s=s.replace(needle,needle+block)
for header in ('map','sstream','chrono'):
    inc=f'#include <{header}>\n'
    if inc not in s:s=inc+s
if p.read_text()!=s:p.write_text(s)
print('Opt-in startup callback profile staged.')
