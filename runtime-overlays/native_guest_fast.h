#pragma once
#include "runtime/cpu_context.h"
#include <cstdio>
#include <iostream>

namespace native_guest_fast {
inline const bool scripted=std::getenv("NWII_WPAD_SCRIPT") || std::getenv("NWII_WPAD_LIVE");
inline const bool verify_trim=std::getenv("NWII_VERIFY_RELOAD_TRIM")!=nullptr;
inline uint64_t trim_checks=0,trim_mismatches=0;
// The NWII_VERIFY_RELOAD_TRIM work runs out of line: the promoted code keeps only the verify_trim test and a call on its
// cold side, and the local is passed and returned by value, so it never has its address taken.
template<class T> __attribute__((noinline,cold)) T Checked(T local,T value) {
  ++trim_checks;
  if(std::memcmp(&local,&value,sizeof(T))!=0) {
    ++trim_mismatches;local=value;
    if(trim_mismatches<=8)std::fprintf(stderr,"[Guest] repaired reload-trim mismatch %llu\n",static_cast<unsigned long long>(trim_mismatches));
  }
  return local;
}
template<class T,class U> __attribute__((always_inline)) inline void Check(T& local,const U& context) {
  local=Checked<T>(local,static_cast<T>(context));
}
// The poll-site "Spinning at PC" console line (tools/fix_poll_spin_outline.py), out of line so a poll site carries only
// its inst_count test.
__attribute__((noinline,cold)) inline void Spinning(uint32_t pc,uint32_t lr) {
  std::cout << "Spinning at PC: 0x" << std::hex << pc << " LR: 0x" << std::hex << lr << std::dec << std::endl;
}
inline unsigned tail_depth=0;
__attribute__((always_inline)) inline bool TailEnter() {
  if(tail_depth>=512 || nwii::runtime::native_poll_due())return false;
  ++tail_depth;return true;
}
__attribute__((always_inline)) inline void TailLeave(){--tail_depth;}
using nwii::runtime::MMU;
__attribute__((always_inline)) inline bool StackSpan(uint32_t physical,unsigned bytes) {
  return (physical-0x10000u<MMU::kMem1Limit-0x10000u && !nwii::runtime::native_watch_span_may_hit(physical,bytes)) ||
         physical-0x10000000u<MMU::kMem2Limit;
}
__attribute__((always_inline)) inline void write8(MMU& mmu,uint32_t address,uint8_t value) {
  const uint32_t p=address&0x3fffffffu;
  if(__builtin_expect(StackSpan(p,1),1)){std::memcpy(reinterpret_cast<void*>(uintptr_t(0x20000000u)+p),&value,1);return;}
  mmu.write8(address,value);
}
__attribute__((always_inline)) inline void write16(MMU& mmu,uint32_t address,uint16_t value) {
  const uint32_t p=address&0x3fffffffu;
  if(__builtin_expect(StackSpan(p,2),1)){const uint16_t bits=__builtin_bswap16(value);std::memcpy(reinterpret_cast<void*>(uintptr_t(0x20000000u)+p),&bits,2);return;}
  mmu.write16(address,value);
}
__attribute__((always_inline)) inline void write32(MMU& mmu,uint32_t address,uint32_t value) {
  const uint32_t p=address&0x3fffffffu;
  if(__builtin_expect(StackSpan(p,4),1)){const uint32_t bits=__builtin_bswap32(value);std::memcpy(reinterpret_cast<void*>(uintptr_t(0x20000000u)+p),&bits,4);return;}
  mmu.write32(address,value);
}
__attribute__((always_inline)) inline void write64(MMU& mmu,uint32_t address,uint64_t value) {
  const uint32_t p=address&0x3fffffffu;
  if(__builtin_expect(StackSpan(p,8),1)){const uint64_t bits=__builtin_bswap64(value);std::memcpy(reinterpret_cast<void*>(uintptr_t(0x20000000u)+p),&bits,8);return;}
  mmu.write64(address,value);
}
__attribute__((always_inline)) inline void write_f32(MMU& mmu,uint32_t address,float value) {
  const uint32_t p=address&0x3fffffffu;
  if(__builtin_expect(StackSpan(p,4),1)){uint32_t bits;std::memcpy(&bits,&value,4);bits=__builtin_bswap32(bits);std::memcpy(reinterpret_cast<void*>(uintptr_t(0x20000000u)+p),&bits,4);return;}
  mmu.write_f32(address,value);
}
__attribute__((always_inline)) inline void write_f64(MMU& mmu,uint32_t address,double value) {
  const uint32_t p=address&0x3fffffffu;
  if(__builtin_expect(StackSpan(p,8),1)){uint64_t bits;std::memcpy(&bits,&value,8);bits=__builtin_bswap64(bits);std::memcpy(reinterpret_cast<void*>(uintptr_t(0x20000000u)+p),&bits,8);return;}
  mmu.write_f64(address,value);
}
}

namespace nwii::runtime {
void native_dispatch_nested(CPUContext&);
#ifdef NWII_V13_INLINE_INDIRECT
struct NativeIndirectSite {uint32_t pc=0;NativeGuestFnPtr func=nullptr;bool hle=false;};
extern NativeIndirectSite g_indirect_sites[];
void native_call_indirect_cached(CPUContext&,uint32_t);
void native_call_indirect_due(CPUContext&,uint32_t);
__attribute__((always_inline)) inline void native_call_indirect_fast(CPUContext& ctx,uint32_t index) {
  const auto& site=g_indirect_sites[index];const uint32_t target=ctx.pc;
  // Cached guest targets have already passed PC normalization, HLE and WPAD dispatch.
  if(!native_guest_fast::scripted && site.pc==target && site.func && !site.hle &&
      target-0x80000000u<0x20000000u && ctx.is_running) {
    if(native_poll_due()){native_call_indirect_due(ctx,index);return;}
    ++ctx.inst_count;site.func(ctx);return;
  }
  native_call_indirect_cached(ctx,index);
}
#endif
}
