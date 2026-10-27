"""Prevent disabled VI interrupt comparators from asserting PI."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hw/hw_vi.cpp';s=p.read_text()
overlay=PORT/'runtime-overlays/hw_vi.cpp'
if overlay.exists():
    updated=overlay.read_text()
    if s!=updated:p.write_text(updated)
    print('VI endian register views and interrupt masks staged.')
    raise SystemExit(0)
s=s.replace('if (vi_di[i] & 0x80000000)', 'if ((vi_di[i] & 0x90000000u) == 0x90000000u)')
old='''    vi_di[0] |= 0x80000000;
    vi_di[1] |= 0x80000000;'''
new='''    // VI_MASK_V1: disabled comparators cannot assert the interrupt line.
    for (int i = 0; i < 2; ++i)
        if (vi_di[i] & 0x10000000u) vi_di[i] |= 0x80000000u;'''
if old in s:
    s=s.replace(old,new,1)
    start=s.index('// VI_MASK_V1')
    pos=s.index('trigger_pi_interrupt(0x00000100);',start)
    s=s[:pos]+s[pos:].replace('trigger_pi_interrupt(0x00000100);','vi_update_interrupt();',1)
elif 'VI_MASK_V1' not in s:raise SystemExit('VI trigger mask context changed')
overlay=PORT/'runtime-overlays/hw_vi.cpp'
if overlay.exists():s=overlay.read_text()
if p.read_text()!=s:p.write_text(s)
print('VI comparator interrupt masks staged.')
