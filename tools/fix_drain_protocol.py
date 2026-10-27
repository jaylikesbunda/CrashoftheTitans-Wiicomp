"""Reliable coalesced requests; unchanged field deadline/FIFO delivery order."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
for name in ('native_pipeline_stats.h','native_render_wait.h','native_drain_signal.h'):
 (rt/'include/runtime'/name).write_text((p/'runtime-overlays'/name).read_text())
f=rt/'src/hle/gx.cpp';s=f.read_text()
if 'NATIVE_DRAIN_PROTOCOL_V2' not in s:
 s='#include "runtime/native_drain_signal.h"\n#include "runtime/native_pipeline_stats.h"\n'+s
 anchor='std::condition_variable g_gx_drain_cv;'
 code='''
// NATIVE_DRAIN_PROTOCOL_V2: lock only publication/consumption, never FIFO work.
static native_drain::Signal& NativeDrainSignal(){static native_drain::Signal signal(g_gx_drain_request,g_gx_drain_mutex,g_gx_drain_cv);return signal;}
void NativeRequestGxDrain(){
 const bool stats=native_hud::on.load(std::memory_order_relaxed);const uint64_t stamp=stats?native_hud::now_us():0;
 static const bool legacy=std::getenv("NWII_LEGACY_DRAIN_WAKE")!=nullptr;
 const bool first=legacy?!g_gx_drain_request.exchange(true,std::memory_order_acq_rel):NativeDrainSignal().request(stamp);
 if(legacy && first)g_gx_drain_cv.notify_one();
 if(stats){native_pipeline::requests.fetch_add(1,std::memory_order_relaxed);if(!first)native_pipeline::coalesced.fetch_add(1,std::memory_order_relaxed);}
}
bool NativeConsumeGxDrain(){
 static const bool legacy=std::getenv("NWII_LEGACY_DRAIN_WAKE")!=nullptr;
 if(legacy)return g_gx_drain_request.exchange(false,std::memory_order_acq_rel);
 const bool stats=native_hud::on.load(std::memory_order_relaxed);auto receipt=NativeDrainSignal().consume(stats?native_hud::now_us():0);
 if(receipt.pending && stats){native_pipeline::drains.fetch_add(1,std::memory_order_relaxed);native_pipeline::latency_us.fetch_add(receipt.latency,std::memory_order_relaxed);native_pipeline::max(native_pipeline::max_latency_us,receipt.latency);native_pipeline::last_generation=receipt.generation;}
 return receipt.pending;
}
'''
 assert s.count(anchor)==1;s=s.replace(anchor,anchor+code,1)
 old='''            if (early && g_gx_pending_bytes.load(std::memory_order_relaxed) >= 16384 &&
                !g_gx_drain_request.exchange(true, std::memory_order_acq_rel))
                g_gx_drain_cv.notify_one();'''
 new='''            if (early && g_gx_pending_bytes.load(std::memory_order_relaxed) >= 16384)
                NativeRequestGxDrain();'''
 assert s.count(old)==1;s=s.replace(old,new,1);f.write_text(s)
f=rt/'src/hle/ios.cpp';s=f.read_text()
if 'NATIVE_IDLE_DRAIN_PROTOCOL_V2' not in s:
 anchor='extern std::condition_variable g_gx_drain_cv;';assert s.count(anchor)==1;s=s.replace(anchor,anchor+'\nextern void NativeRequestGxDrain();',1)
 old='''    if(idle_drain && ctx.pc==0x803BBFA0u && g_gx_pending_bytes.load(std::memory_order_relaxed)>0 &&
       !g_gx_drain_request.exchange(true,std::memory_order_acq_rel))g_gx_drain_cv.notify_one(); }'''
 new='''    if(idle_drain && ctx.pc==0x803BBFA0u && g_gx_pending_bytes.load(std::memory_order_relaxed)>0)
       NativeRequestGxDrain(); } // NATIVE_IDLE_DRAIN_PROTOCOL_V2'''
 assert s.count(old)==1;s=s.replace(old,new,1);f.write_text(s)
f=rt/'src/core/main.cpp';s=f.read_text()
if 'NATIVE_HOST_DRAIN_PROTOCOL_V2' not in s:
 anchor='extern std::condition_variable g_gx_drain_cv;';assert s.count(anchor)==1;s=s.replace(anchor,anchor+' extern bool NativeConsumeGxDrain();',1)
 old='nwii::runtime::g_gx_drain_request.exchange(false, std::memory_order_acq_rel)';assert s.count(old)==1;s=s.replace(old,'nwii::runtime::NativeConsumeGxDrain()',1)
 s=s.replace('const uint64_t late=!requested && after>wait_deadline?', 'const uint64_t late=after>wait_deadline?',1)
 s=s.replace('// NATIVE_WAIT_DETAILS_V1','// NATIVE_WAIT_DETAILS_V1 / NATIVE_HOST_DRAIN_PROTOCOL_V2',1);f.write_text(s)
print('Drain publication/consumption share wait mutex; all wake categories report lateness; field cadence unchanged.')
