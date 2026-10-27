"""Restore the interrupted OSContext and preserve hardware DEC deadlines.

CurrentThread and CurrentContext are distinct during SDK interrupt callbacks.
Only a change of CurrentThread proves a scheduler handoff occurred.
"""
from pathlib import Path
P=Path(__file__).resolve().parents[1]
stage=P/'build/nwii-full-source/nWiiRuntime'
p=stage/'include/runtime/cpu_context.h';s=p.read_text()
marker='NATIVE_CALLBACK_RETURN_OWNER_V1'
if marker not in s:
    needle='  uint32_t dispatch_saved_ctx = 0;'
    assert s.count(needle)==1
    s=s.replace(needle,needle+'\n  uint32_t dispatch_saved_thread = 0; // '+marker,1)
if p.read_text()!=s:p.write_text(s)
p=stage/'src/hle/ios.cpp';s=p.read_text()
if marker not in s:
    old='      if (current_thread != 0 && current_thread != ctx.dispatch_saved_ctx) {'
    assert s.count(old)==1
    s=s.replace(old,'      // '+marker+': an ISR OSContext need not equal CurrentThread.\n      if (current_thread != 0 && current_thread != ctx.dispatch_saved_thread) {',1)
    old='      ctx.dec_written_tb = ctx.read_timebase();'
    assert s.count(old)==1
    s=s.replace(old,'      // Returning from a callback is not mtdec; retain the programmed deadline.',1)
    # DEC, direct legacy IRQ and queued synthetic callbacks each own one saved slot.
    needle='ctx.dispatch_saved_ctx = current_ctx;'
    assert s.count(needle)==3
    s=s.replace(needle,needle+'\n          ctx.dispatch_saved_thread = ctx.mmu.read32(0x800000E4);')
assert s.count('ctx.dispatch_saved_thread = ctx.mmu.read32(0x800000E4);')==3
assert 'current_thread != ctx.dispatch_saved_thread' in s
assert 'ctx.dec_written_tb = ctx.read_timebase();' not in s
if p.read_text()!=s:p.write_text(s)
p=P/'tools/build_native.py';s=p.read_text()
if "run('native-callback-return-owner-stage'" not in s:
    needle="run('native-game-build',"
    assert s.count(needle)==1
    s=s.replace(needle,"run('native-callback-return-owner-stage',[sys.executable,PORT/'tools/fix_callback_return_owner.py'])\n"+needle,1)
if p.read_text()!=s:p.write_text(s)
print('Callback return retains DEC deadline and compares saved thread identity.')
