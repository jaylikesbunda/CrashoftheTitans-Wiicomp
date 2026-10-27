#pragma once
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <atomic>
#include <bit>
#include <cstdint>
#include "runtime/native_hud.h"
namespace native_frame_limit {
inline std::atomic<uint64_t> timestep_count{0},first_us{0},last_us{0};
inline std::atomic<double> timestep_seconds{0},last_dt{0};
inline void ResetTimestep(){timestep_count=0;first_us=0;last_us=0;timestep_seconds=0;last_dt=0;}
inline void RecordTimestep(double dt){
 if(!native_hud::on.load(std::memory_order_relaxed) || !std::isfinite(dt) || dt<0 || dt>10)return;
 const uint64_t now=native_hud::now_us();
 if(!timestep_count.fetch_add(1,std::memory_order_relaxed))first_us=now;
 else timestep_seconds.fetch_add(dt,std::memory_order_relaxed);
 last_us=now;last_dt=dt;
}
inline double ObservedSeconds(){auto first=first_us.load(),last=last_us.load();return last>=first?(last-first)/1000000.0:0;}

inline std::chrono::nanoseconds Period(bool pal,double turbo,const char* custom){
 const int64_t regional=int64_t((pal?20000000:16683333)/turbo);
 if(!custom || !*custom)return std::chrono::nanoseconds(regional);
 char* end=nullptr;const double fps=std::strtod(custom,&end);
 if(end==custom || *end || !std::isfinite(fps) || fps<10 || fps>1000)return std::chrono::nanoseconds(regional);
 return std::chrono::nanoseconds(int64_t(std::llround(1000000000.0/fps)));
}
inline uint64_t GuestPeriodTicks(bool pal,uint64_t frequency,const char* custom){
 return uint64_t(std::llround(frequency*(Period(pal,1.0,custom).count()/1000000000.0)));
}
// NATIVE_GUEST_FRAME_LIMIT_V1: the original manager independently gates its
// update/render work at 1/60 s. Read the launch setting once; preserve its real
// elapsed dt, accumulator, maximum-dt clamp and all nonstandard guest values.
inline float ParseMinimumUpdateSeconds(const char* custom){
 if(!custom || !*custom)return 0.0f;
 char* end=nullptr;const double fps=std::strtod(custom,&end);
 if(end==custom || *end || !std::isfinite(fps) || fps<10 || fps>1000)return 0.0f;
 return float(1.0/fps);
}
inline const float minimum_update_seconds=ParseMinimumUpdateSeconds(std::getenv("NWII_FPS_LIMIT"));
inline float MinimumUpdateSeconds(float original){
 if(minimum_update_seconds>0 && std::bit_cast<uint32_t>(original)==0x3c888889u)
  return minimum_update_seconds;
 return original;
}
}
