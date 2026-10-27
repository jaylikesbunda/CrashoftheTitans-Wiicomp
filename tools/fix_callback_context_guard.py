"""Defer synthetic callbacks while the runtime's single restore slot is occupied."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp';s=p.read_text()
needle='  if ((ctx.msr & 0x8000) == 0)\n    return false;\n'
block='''  // NATIVE_CALLBACK_CONTEXT_GUARD_V1: no nested synthetic context saves.
  // IRQ processing/return above remains active. Keep callbacks queued until
  // the prior handler restores the caller via sentinel/rfi.
  if (ctx.dispatch_saved_ctx != 0 || ctx.in_callback) return false;

'''
if 'NATIVE_CALLBACK_CONTEXT_GUARD_V1' not in s:
    assert s.count(needle)==1;s=s.replace(needle,block+needle,1)
if s!=p.read_text():p.write_text(s)
print('Synthetic callback queue defers while a saved context is in flight.')
