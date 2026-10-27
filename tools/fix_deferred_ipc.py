"""Correct ACK-only handling of an IOS request that intentionally has no reply."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hw/hw_ipc.cpp'
s=p.read_text()
old='  ipc_arm_ctrl = 0x00000003; '
new='  ipc_arm_ctrl = (result == -0x70000001) ? 0x00000001 : 0x00000003; // deferred request: ACK only'
if old in s:s=s.replace(old,new)
elif new not in s:raise SystemExit('IPC ARM-control patch context changed')
old='''                        ipc_ppc_ctrl &= ~0x01;
                        ipc_ppc_ctrl |= 0x2C;'''
new='''                        // SDK tests ACK with mask 0x22 and reply with 0x14.
                        // A deferred STM/USB request must not run its callback.
                        ipc_ppc_ctrl &= ~0x05; // clear send and reply flags
                        ipc_ppc_ctrl |= 0x22; // acknowledge, retaining interrupt enable'''
if old in s:s=s.replace(old,new)
elif new not in s and 'retain unrelated asynchronous replies' not in s:raise SystemExit('IPC ACK patch context changed')
if p.read_text()!=s:p.write_text(s)
print('Deferred IOS requests acknowledge receipt without generating a reply.')
