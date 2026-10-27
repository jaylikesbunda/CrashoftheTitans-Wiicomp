"""Smaller FIFO batches for parked renderers, unchanged cadence and sync wakes."""
from pathlib import Path
import argparse,base64,re
MARK='NATIVE_ADAPTIVE_GX_BATCHING_V1'
BLOCK=re.compile(r'/\* '+MARK+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARK+r' \*/',re.S)
def restore(s):
 s=BLOCK.sub(lambda m:base64.b64decode(m[1]).decode(),s)
 if MARK in s:raise ValueError('Partial adaptive GX batching')
 return s
def replace(s,old,new):
 if s.count(old)!=1:raise ValueError(('Adaptive batching anchor',old[:100],s.count(old)))
 return s.replace(old,'/* '+MARK+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+MARK+' */')
def gx(s):
 s=restore(s)
 s=replace(s,'#include "runtime/native_drain_signal.h"','#include "runtime/native_drain_signal.h"\n#include "runtime/native_gx_batching.h"')
 old='''            if (early && g_gx_pending_bytes.load(std::memory_order_relaxed) >= 16384)
                NativeRequestGxDrain();'''
 new='''            if(early){const size_t bytes=g_gx_pending_bytes.load(std::memory_order_relaxed);
              if(native_gx_batching::ShouldRequest(bytes)){
                native_gx_batching::Record(native_gx_batching::EarlyRequests);NativeRequestGxDrain();
              }else native_gx_batching::Record(native_gx_batching::SmallSuppressed);
            }'''
 s=replace(s,old,new)
 old='bool NativeConsumeGxDrain(){'
 new='''// Caller holds g_gx_drain_mutex. Recheck bytes after announcing parking;
// never acquire the FIFO mutex here (publication takes FIFO before drain mutex).
void NativeArmGxParkLocked(){
 if(!native_gx_batching::NeedArm(g_gx_pending_bytes.load(std::memory_order_relaxed)) ||
    g_gx_drain_request.load(std::memory_order_acquire))return;
 static const bool early=std::getenv("NWII_NO_GX_EARLY_DRAIN")==nullptr;
 if(!early)return;
 static const bool legacy=std::getenv("NWII_LEGACY_DRAIN_WAKE")!=nullptr;
 const bool stats=native_hud::on.load(std::memory_order_relaxed);
 if(legacy)g_gx_drain_request.store(true,std::memory_order_release);
 else NativeDrainSignal().request_locked(stats?native_hud::now_us():0);
 native_gx_batching::Record(native_gx_batching::ParkRearms);
}
bool NativeConsumeGxDrain(){'''
 return replace(s,old,new)
def main_source(s):
 s=restore(s)
 s=replace(s,'#include "runtime/native_hud_gl.h"','#include "runtime/native_gx_batching.h"\n#include "runtime/native_hud_gl.h"')
 s=replace(s,'extern bool NativeConsumeGxDrain();','extern bool NativeConsumeGxDrain(); extern void NativeArmGxParkLocked();')
 old='      std::unique_lock<std::mutex> drain_lock(nwii::runtime::g_gx_drain_mutex);'
 new=old+'''
      native_gx_batching::Park native_park;
      nwii::runtime::NativeArmGxParkLocked();'''
 return replace(s,old,new)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--restore',action='store_true');ap.add_argument('--headers-only',action='store_true');a=ap.parse_args();rt=a.port/'build/nwii-full-source/nWiiRuntime'
 if not a.restore:
  for name in ('native_gx_batching.h','native_render_wait.h','native_drain_signal.h'):
   p=rt/'include/runtime'/name;v=(a.port/'runtime-overlays'/name).read_bytes()
   if not p.exists()or p.read_bytes()!=v:p.write_bytes(v)
 if a.headers_only:return
 edits=[]
 for rel,fn in [('src/hle/gx.cpp',gx),('src/core/main.cpp',main_source)]:
  p=rt/rel;s=p.read_text();edits.append((p,s,restore(s)if a.restore else fn(s)))
 for p,s,n in edits:
  if s!=n:p.write_text(n)
 print('Adaptive GX batching '+('restored'if a.restore else'staged; legacy is the default, explicit synchronization remains immediate'))
if __name__=='__main__':main()
