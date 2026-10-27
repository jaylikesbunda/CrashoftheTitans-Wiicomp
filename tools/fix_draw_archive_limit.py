"""Allow a bounded complete-scene diagnostic batch; guest execution unchanged."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
s=p.read_text()
old='  if (!enabled || s_batch.empty() || sample > 50) return;'
new='''  // NATIVE_DRAW_ARCHIVE_LIMIT_V1: retain enough draws for a whole scene.
  static const unsigned archive_max=[](){const char *e=std::getenv("NWII_EFB_PROBE_MAX");return e?std::min(300u,unsigned(std::strtoul(e,nullptr,10))):50u;}();
  if (!enabled || s_batch.empty() || sample > archive_max) return;'''
if 'NATIVE_DRAW_ARCHIVE_LIMIT_V1' not in s:
    if s.count(old)!=1:raise SystemExit('Archive limit context changed')
    s=s.replace(old,new)
    p.write_text(s)
print('Read-only draw archive bound configurable up to 300.')
