"""Observed wake categories, timed oversleep and bounded sampled guest wait context."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
for src,dst in [('native_hud.h','native_hud.h'),('native_stutter.h','native_stutter.h'),('native_hud_gl.h','native_hud_gl.h'),('idle_wait.h','native_idle_wait.h')]:
 target=rt/'include/runtime'/dst;value=(p/'runtime-overlays'/src).read_text()
 if not target.exists() or target.read_text()!=value:target.write_text(value)
f=rt/'src/core/main.cpp';s=f.read_text()
s=s.replace('const uint64_t late=!requested && after>wait_deadline?', 'const uint64_t late=after>wait_deadline?')
old='''      nwii::runtime::g_gx_drain_cv.wait_until(drain_lock, std::min(field_deadline, wait_now + std::chrono::milliseconds(2)),
                                              [] { return nwii::runtime::g_gx_drain_request.load(std::memory_order_acquire); });'''
new='''      const auto wait_deadline=std::min(field_deadline,wait_now+std::chrono::milliseconds(2));
      const uint64_t wait_start=native_hud::on.load(std::memory_order_relaxed)?native_hud::now_us():0;
      bool requested;
      {native_trace::Scope sleep("host.field_sleep"); // NATIVE_WAIT_DETAILS_V1
       requested=nwii::runtime::g_gx_drain_cv.wait_until(drain_lock,wait_deadline,
                 [] { return nwii::runtime::g_gx_drain_request.load(std::memory_order_acquire); });}
      if(wait_start){const auto after=std::chrono::steady_clock::now();
       const uint64_t late=after>wait_deadline?uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(after-wait_deadline).count()):0;
       native_hud::record_wait(false,requested?0u:after>=field_deadline?2u:1u,native_hud::now_us()-wait_start,late);}'''
if new not in s and "NATIVE_HOST_DRAIN_PROTOCOL_V2" not in s:assert s.count(old)==1;s=s.replace(old,new,1)
for anchor,name in [('        while (SDL_PollEvent(&e)) {','host.event_poll'),('      nwii::runtime::input::InputManager::get().update();','host.input_update')]:
 if name in s:continue
 if 'while' in anchor:
  # Scope ends with the enclosing !headless block; covers the actual event pump.
  replacement='        native_trace::Scope events("'+name+'");\n'+anchor
 else:replacement='      {native_trace::Scope inputs("'+name+'");nwii::runtime::input::InputManager::get().update();}'
 assert s.count(anchor) in (1,2),(anchor,s.count(anchor));s=s.replace(anchor,replacement)
if s!=f.read_text():f.write_text(s)
f=rt/'src/hle/ios.cpp';s=f.read_text();anchor='        native_idle::wait(ticks, ctx.tb_freq, pending);'
if 'NATIVE_GUEST_WAIT_CONTEXT_V1' not in s:
 block='''
        // NATIVE_GUEST_WAIT_CONTEXT_V1: sample only mapped MEM1 on the guest owner.
        // State/PC/queue and pending flags are observations, not claimed causes.
        if(native_hud::on.load(std::memory_order_relaxed)){
          static uint64_t next_context=0;const uint64_t stamp=native_hud::now_us();
          if(stamp>=next_context){next_context=stamp+50000;
            native_stutter::GuestContext sample;sample.stamp=stamp;sample.pc=ctx.pc;sample.lr=ctx.lr;
            sample.irq=hw::pi_intsr.load(std::memory_order_acquire)&hw::pi_intmr.load(std::memory_order_relaxed);
            sample.flags=(!ctx.is_running.load()?1u:0u)|(ctx.vblank_pending.load()?2u:0u)|(ctx.pending_cb_count.load()?4u:0u)|(sample.irq?8u:0u);
            auto read=[&](uint32_t address){const uint8_t* bytes=ctx.mmu.get_ptr(address);return bytes?(uint32_t(bytes[0])<<24)|(uint32_t(bytes[1])<<16)|(uint32_t(bytes[2])<<8)|bytes[3]:0u;};
            uint32_t thread=read(0x800000dcu);
            while(sample.count<sample.threads.size() && thread>=0x80000000u && thread<=0x817ffd00u && !(thread&3u)){
              sample.threads[sample.count++]={thread,read(thread+0x2c8)>>16,read(thread+0x2dc),read(thread+0x198)};
              const uint32_t next=read(thread+0x2fc);if(next==thread)break;thread=next;
            }
            native_hud::observe_guest_context(sample);
          }
        }'''
 assert s.count(anchor)==1;s=s.replace(anchor,anchor+block,1)
if s!=f.read_text():f.write_text(s)
print('Field/guest wakes, actual/max/late timing, event/input scopes and sampled thread wait queues/PCs staged; deadlines unchanged.')
