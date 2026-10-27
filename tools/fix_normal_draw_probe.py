"""Stage bounded read-only normal snapshots for lit draws after the file gate."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
renderer=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
s=renderer.read_text()
start='  // NATIVE_NORMAL_DRAW_PROBE_BEGIN\n'
end='  // NATIVE_NORMAL_DRAW_PROBE_END\n'
while start in s:
    a=s.index(start);b=s.index(end,a)+len(end);s=s[:a]+s[b:]
    renderer.write_text(s)
if '#include "runtime/gx/fifo_parser.h"' not in s:
    renderer.write_text('#include "runtime/gx/fifo_parser.h"\n'+s)
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/fifo_parser.cpp'
s=p.read_text()
while start in s:
    a=s.index(start);b=s.index(end,a)+len(end);s=s[:a]+s[b:]
probe=r'''  // NATIVE_NORMAL_DRAW_PROBE_BEGIN
  static const bool normal_probe = std::getenv("NWII_NORMAL_DRAW_PROBE") != nullptr;
  static const char *normal_arm = std::getenv("NWII_EFB_PROBE_ARM_FILE");
  static unsigned normal_count = 0;
  if (normal_probe && normal_count < 300 && !c.vertices.empty() &&
      r.vat.nrmMask != VtxAttrMask::None) {
    FILE *armed = normal_arm ? std::fopen(normal_arm,"rb") : nullptr;
    if (!normal_arm || armed) {
      if (armed) std::fclose(armed);
      const auto &q=c.vertices.front();
      size_t zero=0,declared=0;
      for(const auto &x:c.vertices){declared+=x.has_norm;zero+=x.norm[0]==0 && x.norm[1]==0 && x.norm[2]==0;}
      const unsigned nb=3u*(q.posMtxIdx&31u);
      const float *m = r.snap_nrm_bank.empty() || nb+9>r.snap_nrm_bank.size() ? r.snap_nrm_mtx : &r.snap_nrm_bank[nb];
      printf("[NORMAL draw] sample=%u verts=%zu declared=%zu zero=%zu vat=%u hint=%u base=%08x stride=%u pre=%u idx=%u normal=%g,%g,%g snap=%u bank=%zu matrix=%g,%g,%g,%g,%g,%g,%g,%g,%g\n",
        ++normal_count,c.vertices.size(),declared,zero,unsigned(r.vat.nrmMask),unsigned(r.need_normal),r.arrayBase[1],r.arrayStride[1],unsigned(q.pre_xf),unsigned(q.posMtxIdx),q.norm[0],q.norm[1],q.norm[2],unsigned(r.have_snap_mtx),r.snap_nrm_bank.size(),
        m[0],m[1],m[2],m[3],m[4],m[5],m[6],m[7],m[8]);
    }
  }
  // NATIVE_NORMAL_DRAW_PROBE_END
'''
needle='    c.raw.reset(); // decoded: drop the raw copy'
expected=2 if 'NATIVE_INDEXED_DRAW_DECODE_V1' in s else 1
if s.count(needle)!=expected:raise SystemExit('Deferred release context changed')
if expected==2:
    declaration='static unsigned g_native_normal_probe_count=0; // NATIVE_NORMAL_SHARED_LIMIT_V1\n'
    if declaration not in s:
        anchor='// NATIVE_VECTOR_DECODE_PLAN_V1:'
        if s.count(anchor)!=1:raise SystemExit('Normal probe shared-limit context changed')
        s=s.replace(anchor,declaration+anchor,1)
    probe=probe.replace('  static unsigned normal_count = 0;', '  auto& normal_count=g_native_normal_probe_count;')
s=s.replace(needle,probe+needle)
if s!=p.read_text():p.write_text(s)
print('Bounded normal snapshot probe staged.')
