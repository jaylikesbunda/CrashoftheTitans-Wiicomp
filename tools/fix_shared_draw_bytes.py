"""Avoid encoded-stream copies when cloning live display-list matrix snapshots."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/include/runtime/gx/fifo_parser.h';s=f.read_text()
if 'NATIVE_SHARED_DRAW_BYTES_V1' not in s:
    baseline=p/'runtime-overlays/shared-draw-before.h'
    if not baseline.exists():baseline.write_text(s)
    assert s.count('    std::vector<uint8_t> bytes;')==1
    a=s.index('struct DrawRaw {');s=s[:a]+(p/'runtime-overlays/shared_draw_bytes.inc').read_text()+'\n'+s[a:]
    s=s.replace('    std::vector<uint8_t> bytes;','    NativeDrawBytes bytes;',1)
    f.write_text(s)
else:
    a=s.index('// NATIVE_SHARED_DRAW_BYTES_V1:');b=s.index('struct DrawRaw {',a)
    s=s[:a]+(p/'runtime-overlays/shared_draw_bytes.inc').read_text()+'\n'+s[b:]
    if f.read_text()!=s:f.write_text(s)
print('Encoded draw streams shared across snapshot copies; all byte mutations detach.')
