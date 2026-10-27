#pragma once
#include <atomic>
#include <cstdint>
#include <cstdlib>
namespace native_guest_regions {
inline std::atomic<uint64_t> fast_calls{0},legacy_calls{0};
inline bool Enabled(){static const bool enabled=[](){
 const char* blocked[]={"NWII_NO_GUEST_REGIONS","NWII_SAMPLE","NWII_PEEK","NWII_STARTUP_PROFILE","NWII_LOOPTRACE","NWII_RESCHED"};
 for(auto name:blocked)if(std::getenv(name))return false;return true;}();return enabled;}
inline void ResetStats(){fast_calls.store(0,std::memory_order_relaxed);legacy_calls.store(0,std::memory_order_relaxed);}
}
