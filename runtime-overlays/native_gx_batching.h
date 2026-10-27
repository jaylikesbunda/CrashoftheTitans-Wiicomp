#pragma once
#include <atomic>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include "runtime/native_gx_work_metrics.h"
namespace native_gx_batching {
enum class Mode {Legacy, Adaptive4, Adaptive8, Fixed4, Fixed8};
inline Mode Parse(const char* value){
 if(!value || !std::strcmp(value,"legacy"))return Mode::Legacy;
 if(!std::strcmp(value,"adaptive4"))return Mode::Adaptive4;
 if(!std::strcmp(value,"adaptive8"))return Mode::Adaptive8;
 if(!std::strcmp(value,"fixed4"))return Mode::Fixed4;
 if(!std::strcmp(value,"fixed8"))return Mode::Fixed8;
 return Mode::Legacy;
}
// NATIVE_GX_BATCH_SELECTED_HOIST_V1: launch-fixed mode hoisted to namespace scope
// so every publish/skip decision is a load, not a function-local-static guard.
inline const Mode kSelected=std::getenv("NWII_NO_ADAPTIVE_GX_BATCHING")?Mode::Legacy:Parse(std::getenv("NWII_GX_BATCHING"));
inline Mode Selected(){return kSelected;}
inline const char* Name(){switch(Selected()){case Mode::Adaptive4:return "adaptive4";case Mode::Adaptive8:return "adaptive8";case Mode::Fixed4:return "fixed4";case Mode::Fixed8:return "fixed8";default:return "legacy";}}
inline std::atomic<bool> parked{false};
inline bool Adaptive(Mode mode){return mode==Mode::Adaptive4 || mode==Mode::Adaptive8;}
inline size_t Threshold(Mode mode,bool idle){
 switch(mode){case Mode::Fixed4:return 4096;case Mode::Fixed8:return 8192;case Mode::Adaptive4:return idle?4096:16384;case Mode::Adaptive8:return idle?8192:16384;default:return 16384;}
}
// Paired SC fences close the publish-before-park store/load race across the
// pending-byte and parked atomics. Both cannot miss the other's preceding store.
// The producer already published its bytes before calling this helper.
inline bool ShouldRequest(size_t bytes){
 const auto mode=Selected();
 if(bytes>=16384)return true;
 if(bytes<4096 || bytes<Threshold(mode,true))return false;
 if(!Adaptive(mode))return bytes>=Threshold(mode,false);
 std::atomic_thread_fence(std::memory_order_seq_cst);
 return parked.load(std::memory_order_relaxed);
}
struct Park {
 bool active=Adaptive(Selected());
 Park(){if(active){parked.store(true,std::memory_order_relaxed);std::atomic_thread_fence(std::memory_order_seq_cst);}}
 ~Park(){if(active)parked.store(false,std::memory_order_release);}
};
inline bool NeedArm(size_t bytes){return Adaptive(Selected()) && bytes>=Threshold(Selected(),true);}
enum Counter {EarlyRequests,ParkRearms,SmallSuppressed,Count};
inline std::atomic<unsigned long long> totals[Count]{};
inline unsigned long long baseline[Count]{};
inline void Record(Counter key){if(native_gx_work::capture.load(std::memory_order_relaxed))totals[key].fetch_add(1,std::memory_order_relaxed);}
inline unsigned long long Value(Counter key){return native_gx_work::Delta(totals[key].load(std::memory_order_relaxed),baseline[key]);}
inline void Reset(){for(unsigned i=0;i<Count;++i)baseline[i]=totals[i].load(std::memory_order_relaxed);}
}
