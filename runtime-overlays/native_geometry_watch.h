#pragma once
#include "runtime/cpu_context.h"
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include "runtime/native_gx_work_metrics.h"
namespace nwii::runtime {extern MMU* g_mmu;}
// Shared OS dirty generations, never a drain-level clean snapshot: every hit
// queries again so a write between draws is visible. Small spans keep memcmp.
namespace native_geometry_watch {
struct Stats {uint64_t eligible_queries=0,clean_hits=0,bytes_compare_avoided=0,dirty_compares=0,fallback_compares=0;};
inline std::atomic<uint64_t> totals[5]{};
inline Stats baseline{};
inline Stats GetStats(){
 return {native_gx_work::Delta(totals[0].load(std::memory_order_relaxed),baseline.eligible_queries),
 native_gx_work::Delta(totals[1].load(std::memory_order_relaxed),baseline.clean_hits),
 native_gx_work::Delta(totals[2].load(std::memory_order_relaxed),baseline.bytes_compare_avoided),
 native_gx_work::Delta(totals[3].load(std::memory_order_relaxed),baseline.dirty_compares),
 native_gx_work::Delta(totals[4].load(std::memory_order_relaxed),baseline.fallback_compares)};
}
inline void ResetBaseline(){baseline={totals[0].load(std::memory_order_relaxed),totals[1].load(std::memory_order_relaxed),totals[2].load(std::memory_order_relaxed),totals[3].load(std::memory_order_relaxed),totals[4].load(std::memory_order_relaxed)};}
inline void Record(bool eligible,bool clean,size_t bytes){
 if(!native_gx_work::capture.load(std::memory_order_relaxed))return;
 if(eligible)totals[0].fetch_add(1,std::memory_order_relaxed);
 if(clean){totals[1].fetch_add(1,std::memory_order_relaxed);totals[2].fetch_add(bytes,std::memory_order_relaxed);}
 else totals[eligible?3:4].fetch_add(1,std::memory_order_relaxed);
}
inline uint64_t Newest(const uint8_t* source,size_t bytes){
 static const bool enabled=!std::getenv("NWII_NO_UNIFIED_WATCH") && !std::getenv("NWII_NO_WRITE_WATCH");
 if(!enabled || bytes<65536 || !nwii::runtime::g_mmu)return UINT64_MAX;
 auto& m=*nwii::runtime::g_mmu;const uintptr_t address=reinterpret_cast<uintptr_t>(source);
 auto query=[&](const auto& bank){const uintptr_t base=reinterpret_cast<uintptr_t>(bank.data());
  if(address<base || address-base>=bank.size() || bytes>bank.size()-(address-base))return UINT64_MAX;
  return nwii::runtime::native_ram_newest_write(bank.data(),bank.size(),size_t(address-base),bytes);};
 uint64_t newest=query(m.mem1);return newest!=UINT64_MAX?newest:query(m.mem2);
}
inline bool Unchanged(const uint8_t* source,const uint8_t* saved,size_t bytes,uint64_t* verified){
 const uint64_t newest=Newest(source,bytes);
 const uint64_t old=__atomic_load_n(verified,__ATOMIC_RELAXED);
 if(newest!=UINT64_MAX && old && newest<=old){Record(true,true,bytes);return true;}
 Record(newest!=UINT64_MAX,false,bytes);
 if(std::memcmp(source,saved,bytes))return false;
 // The observation PRECEDES the read. Never stamp with the global generation
 // after reading: another lane may fold a concurrent write in that interval.
 if(newest!=UINT64_MAX)__atomic_store_n(verified,newest,__ATOMIC_RELAXED);
 return true;
}
}
