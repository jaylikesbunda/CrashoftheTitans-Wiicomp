"""Include real interrupt source bits in existing throttled dispatch logs."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp';s=p.read_text()
old='<< dc << " pc=0x" << std::hex << ctx.pc'
new='<< dc << " sources=0x" << std::hex << active_ints << " pc=0x" << ctx.pc'
if old in s:p.write_text(s.replace(old,new,1))
elif new not in s:raise SystemExit('IRQ source observer context changed')
print('Existing throttled IRQ logs now identify real pending source bits.')
