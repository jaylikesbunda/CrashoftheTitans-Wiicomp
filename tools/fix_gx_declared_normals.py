"""Decode declared normals independently of stale renderer XF lighting state."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/fifo_parser.cpp'
s=p.read_text()
old='if (r.vat.nrmMask != VtxAttrMask::None && r.need_normal) {'
new='if (r.vat.nrmMask != VtxAttrMask::None) { // NATIVE_DECLARED_NORMALS_V1'
if old in s:s=s.replace(old,new,1)
elif new not in s:raise SystemExit('Normal decoding context changed')
s=s.replace('uses clr0/tex0). Normals are decoded only for lit draws.',
            'uses clr0/tex0). Decode every declared normal attribute; renderer\n        // lighting state has not replayed this stream yet.')
old='''            c.raw->need_normal = ((g_state.xf[0x00E] >> 1) & 1) ||
                                 ((g_state.xf[0x00F] >> 1) & 1);'''
s=s.replace(old,'''            // Parse-time renderer XF state can be from the previous drain.
            // VAT presence is the authoritative attribute contract.
            c.raw->need_normal = vat.nrmMask != VtxAttrMask::None;''')
s=s.replace('''            // Normals only matter when a colour channel is lit (XF 0x100e/0x100f
            // bit1); decoding them otherwise is pure cost.
''','')
if s!=p.read_text():p.write_text(s)
print('Declared normal decoding staged; no renderer-state dependency.')
