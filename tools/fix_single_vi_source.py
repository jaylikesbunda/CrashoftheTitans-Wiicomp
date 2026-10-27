"""One VI interrupt source at the exact field rate in wall-clock mode (opt-in: NWII_SINGLE_VI=1).

Capture frame-cost-20261006-121918-356500 (20.41 FPS): 623 vi.signal events in the capture, mean spacing 9.4 ms
(about 106 retraces/s), 150 of the intervals at 17 ms and 130 at 0-4 ms. Two independent sources raise the guest's
VI interrupt: the EventScheduler recurring event at tb_freq/60 and the host loop's vblank_pending flag, which
fires once per host iteration (14-34 ms apart once drains run in the field wait). A PAL title expects 50 per
second at even spacing; bursts of two retraces within a few ms and an irregular beat are a candidate cause of
judder and of retrace-counted timing running fast.

With NWII_WALLTB=1 and NWII_SINGLE_VI=1: the scheduler event runs at the region's field rate (PAL 50 Hz,
NTSC 60000/1001 Hz) and only marks vblank_pending; the existing safe-point branch in the poll then raises the
interrupt (coalescing if one is still pending). The host loop stops setting vblank_pending but still wakes the
guest. Virtual-clock mode and the default behavior are unchanged.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime/src'
marker='NATIVE_SINGLE_VI_SOURCE_V1'
f=rt/'hle/ios.cpp';s=f.read_text()
if marker not in s:
    old='          uint64_t vi_period = ctx.tb_freq / 60;\n'
    assert s.count(old)==1
    new='''          uint64_t vi_period = ctx.tb_freq / 60;
          // NATIVE_SINGLE_VI_SOURCE_V1
          static const bool single_vi = std::getenv("NWII_SINGLE_VI") && std::getenv("NWII_WALLTB");
          if (single_vi) {
              const auto &vi_id = nwii::runtime::Config::get().game_id;
              const char vi_region = vi_id.size() >= 4 ? vi_id[3] : 'E';
              const bool vi_pal = vi_region == 'P' || vi_region == 'D' || vi_region == 'F' || vi_region == 'I' ||
                  vi_region == 'S' || vi_region == 'H' || vi_region == 'U' || vi_region == 'X' ||
                  vi_region == 'Y' || vi_region == 'Z';
              vi_period = vi_pal ? ctx.tb_freq / 50 : ctx.tb_freq * 1001 / 60000;
          }
'''
    s=s.replace(old,new,1)
    old='''              vi_period, [](CPUContext& c, uint64_t) {
'''
    assert s.count(old)==1
    new='''              vi_period, [](CPUContext& c, uint64_t) {
                  if (static const bool single = std::getenv("NWII_SINGLE_VI") && std::getenv("NWII_WALLTB"); single) {
                      c.vblank_pending = true; // raised at the next safe poll; coalesces while pending
                      nwii::runtime::native_idle::notify();
                      return;
                  }
'''
    s=s.replace(old,new,1);f.write_text(s)
f=rt/'core/main.cpp';s=f.read_text()
if marker not in s:
    old='ctx->vblank_pending = true; nwii::runtime::native_idle::notify(); // NATIVE_IDLE_HOST_WAKE_V1'
    assert s.count(old)==2
    new='{static const bool single_vi=std::getenv("NWII_SINGLE_VI")&&std::getenv("NWII_WALLTB"); /* NATIVE_SINGLE_VI_SOURCE_V1 */ if(!single_vi)ctx->vblank_pending = true; nwii::runtime::native_idle::notify();} // NATIVE_IDLE_HOST_WAKE_V1'
    f.write_text(s.replace(old,new))
print('Single VI source installed (opt-in NWII_SINGLE_VI=1 with NWII_WALLTB=1).')
