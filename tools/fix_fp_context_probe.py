"""Stage opt-in read-only FPR/context history around the loading search."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp'
s=p.read_text(); begin='// NATIVE_FP_CONTEXT_BEGIN\n'; end='// NATIVE_FP_CONTEXT_END\n'
block=begin+(PORT/'runtime-overlays/fp_context_probe.inc').read_text()+end
if begin in s:
    a=s.index(begin);b=s.index(end,a)+len(end);s=s[:a]+block+s[b:]
else:
    needle='static void hle_save_context_to_guest(CPUContext &ctx, uint32_t c) {'
    assert s.count(needle)==1;s=s.replace(needle,block+'\n'+needle)
for needle, replacement in [
 ('static void hle_save_context_to_guest(CPUContext &ctx, uint32_t c) {', 'static void hle_save_context_to_guest(CPUContext &ctx, uint32_t c) {\n  native_fp_context_probe(ctx,"save",c);'),
 ('  uint32_t c = ctx.mmu.read32(0x800000D4);\n  if (c == 0)', '  uint32_t c = ctx.mmu.read32(0x800000D4);\n  native_fp_context_probe(ctx,"load-before",c);\n  if (c == 0)'),
 ('  ctx.mmu.write16(c + 0x1A2, ctx.mmu.read16(c + 0x1A2) & ~0x0002);\n  return true;', '  ctx.mmu.write16(c + 0x1A2, ctx.mmu.read16(c + 0x1A2) & ~0x0002);\n  native_fp_context_probe(ctx,"load-after",c);\n  return true;'),
 ('bool process_pending_callbacks(CPUContext &ctx) {', 'bool process_pending_callbacks(CPUContext &ctx) {\n  native_fp_context_probe(ctx,"poll");')]:
    if replacement not in s:
        assert s.count(needle)==1,needle;s=s.replace(needle,replacement,1)
# Other staging passes may insert text at the function entry, breaking the
# adjacent-text test above. Keep exactly one poll observer across restaging.
call='  native_fp_context_probe(ctx,"poll");\n'
s=s.replace(call,'')
needle='bool process_pending_callbacks(CPUContext &ctx) {\n'
assert s.count(needle)==1
s=s.replace(needle,needle+call,1)
if p.read_text()!=s:p.write_text(s)
print('Read-only floating-point context observer staged.')
