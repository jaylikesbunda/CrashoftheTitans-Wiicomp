"""Stage bounded pre-clear/post-draw GL observations without guest changes."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
s=p.read_text()
overlay=(PORT/'runtime-overlays/draw_capture.inc').read_text()+'\n'+(PORT/'runtime-overlays/efb_probe.inc').read_text()
if '// NATIVE_EFB_PROBE_V1' in s:
    a=s.index('// NATIVE_EFB_PROBE_V1');b=s.index('static void FlushBatch() {',a)
    updated=s[:a]+'// NATIVE_EFB_PROBE_V1\n'+overlay+'\n'+s[b:]
    if updated!=s:p.write_text(updated)
    print('EFB observer refreshed.');raise SystemExit(0)
needle='static void FlushBatch() {'
if s.count(needle)!=1:raise SystemExit('FlushBatch context changed')
s=s.replace(needle,'// NATIVE_EFB_PROBE_V1\n'+overlay+'\n'+needle)
needle='  glDrawArrays(GL_TRIANGLES, 0, s_batch.size());'
if s.count(needle)!=1:raise SystemExit('Draw context changed')
s=s.replace(needle,needle+'\n  NativeEfbProbe("post_draw");')
needle='        ApplyBPRegister((uint8_t)cmd.reg, cmd.val);'
if s.count(needle)!=1:raise SystemExit('BP copy context changed')
s=s.replace(needle,'        if (cmd.reg == 0x52) NativeEfbProbe("pre_copy");\n'+needle)
needle='glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);'
if s.count(needle)!=2:raise SystemExit('Clear contexts changed')
s=s.replace(needle,'NativeEfbProbe("pre_clear");\n      '+needle)
p.write_text(s)
print('Bounded EFB observer staged at post-draw, pre-copy and pre-clear.')
