"""Resolve one-shot BP write masks using renderer-owned chronological state."""
from pathlib import Path

PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
s=p.read_text();marker='// NATIVE_BP_WRITE_MASK_V1\n'
overlay=(PORT/'runtime-overlays/bp_write_mask.inc').read_text()
end_marker='// NATIVE_TEV_REGISTER_BANKS_V1'
start=s.index(marker) if marker in s else s.index(end_marker)
end=s.index(end_marker,start)
s=s[:start]+marker+overlay+'\n'+s[end:]
needle='  void Render(const std::vector<GXCommand> &commands) override {\n'
# A later stage (NATIVE_DRAW_SUBMISSION_REUSE_V1) changes the parameter to CommandStream; keep the
# file's own spelling so the restore is exact.
alt='  void Render(const CommandStream &commands) override {\n'
if needle not in s and alt in s:needle=alt
new=needle+'    NativeRestoreBpSnapshot();\n'
# Tracing may separate the anchor from this call on a later build. Keep exactly
# one restore rather than accumulating another full BP copy per restaging.
s=s.replace('    NativeRestoreBpSnapshot();\n','')
if new not in s:
    if s.count(needle)!=1:raise SystemExit('Render context changed')
    s=s.replace(needle,new,1)
a=s.find('      } else if (cmd.type == GXCommandType::BPRegister) {')
b=s.find('      } else if (cmd.type == GXCommandType::DrawPrimitive)',a) if a>=0 else -1
if a>=0 and b>a:
 block=s[a:b]
 if 'const uint32_t bp_value = NativeBpRegisterValue' not in block:
    block=block.replace('cmd.val','bp_value')
    needle='        if (cmd.reg == 0x52) NativeEfbProbe("pre_copy");'
    if needle not in block:raise SystemExit('BP observer context changed')
    block=block.replace(needle,'        const uint32_t bp_value = NativeBpRegisterValue((uint8_t)cmd.reg, cmd.val);\n'+needle,1)
    s=s[:a]+block+s[b:]
# else: a later stage rewrote the BP replay block; the marker above proves this stage has run.
if p.read_text()!=s:p.write_text(s)
print('One-shot BP masks and chronological render state staged.')
