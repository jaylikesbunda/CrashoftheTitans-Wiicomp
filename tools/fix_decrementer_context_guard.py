"""Preserve the caller restore slot when DEC expires during an active handler."""
from pathlib import Path
port=Path(__file__).resolve().parents[1]
path=port/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp'
text=path.read_text()
marker='NATIVE_DEC_CONTEXT_GUARD_V1'
if marker not in text:
    old='  if (poll_clock_due && ctx.dec_expired() && (ctx.msr & 0x8000) && !ctx.in_callback) {'
    assert text.count(old)==1
    new='''  // NATIVE_DEC_CONTEXT_GUARD_V1: this HLE path owns one caller-restore slot.
  // Keep a rearmed DEC pending until the active handler completes instead of
  // overwriting the caller (or its temporary OSContext) with a nested save.
  if (poll_clock_due && ctx.dispatch_saved_ctx == 0 && ctx.dec_expired() &&
      (ctx.msr & 0x8000) && !ctx.in_callback) {'''
    text=text.replace(old,new,1);path.write_text(text)
print('DEC retains pending state while the single saved caller context is occupied.')
