"""Stage the corrected AI bit layout/counter and unshadow Wii AI registers."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
stage=PORT/'build/nwii-full-source/nWiiRuntime/src/hw'
s=(PORT/'runtime-overlays/hw_ai.cpp').read_text();p=stage/'hw_ai.cpp'
if p.read_text()!=s:p.write_text(s)
p=stage/'hw_ipc.cpp';s=p.read_text()
s=s.replace('0xCD000000, 0xCD00FFFF','0xCD000000, 0xCD0001FF')
s=s.replace('0x0D000000, 0x0D00FFFF','0x0D000000, 0x0D0001FF')
if p.read_text()!=s:p.write_text(s)
print('AI counter/bit-layout overlay and bounded IPC register range staged.')
