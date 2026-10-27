#pragma once
// Ordinary RAM reads let the compiler retain guest register values across loads.
// Non-RAM reads use the MMU so hardware side effects remain visible to it.
#include "runtime/cpu_context.h"
#include <cstdint>
#include <cstring>
namespace native_fastmem {
using nwii::runtime::MMU;
inline constexpr uintptr_t base=0x20000000u;
__attribute__((always_inline)) inline bool ram(uint32_t physical) {
  return physical<MMU::kMem1Limit || physical-0x10000000u<MMU::kMem2Limit;
}
__attribute__((always_inline)) inline uint8_t read8(MMU& mmu,uint32_t address) {
  const uint32_t physical=address&0x3fffffffu;
  if(__builtin_expect(!ram(physical),0))return mmu.read8(address);
  uint8_t value;
  std::memcpy(&value,reinterpret_cast<const void*>(base+physical),sizeof(value));
  return value;
}
__attribute__((always_inline)) inline uint16_t read16(MMU& mmu,uint32_t address) {
  const uint32_t physical=address&0x3fffffffu;
  if(__builtin_expect(!ram(physical),0))return mmu.read16(address);
  uint16_t value;
  std::memcpy(&value,reinterpret_cast<const void*>(base+physical),sizeof(value));
  return __builtin_bswap16(value);
}
// NATIVE_FASTMEM_READ32_FOLD_V1: physical 0..15 leaves the one-compare MEM1 range, so the 0x0C special case
// costs nothing on ordinary reads; that range, MMIO and unmapped addresses keep their exact results.
__attribute__((noinline,cold)) inline uint32_t read32_edge(MMU& mmu,uint32_t address,uint32_t physical) {
  if(physical>=16u)return mmu.read32(address);
  if(physical==12u)return 0;
  uint32_t value;
  std::memcpy(&value,reinterpret_cast<const void*>(base+physical),sizeof(value));
  return __builtin_bswap32(value);
}
__attribute__((always_inline)) inline uint32_t read32(MMU& mmu,uint32_t address) {
  const uint32_t physical=address&0x3fffffffu;
  if(__builtin_expect(physical-16u<MMU::kMem1Limit-16u || physical-0x10000000u<MMU::kMem2Limit,1)) {
    uint32_t value;
    std::memcpy(&value,reinterpret_cast<const void*>(base+physical),sizeof(value));
    return __builtin_bswap32(value);
  }
  return read32_edge(mmu,address,physical);
}
__attribute__((always_inline)) inline uint64_t read64(MMU& mmu,uint32_t address) {
  const uint32_t physical=address&0x3fffffffu;
  if(__builtin_expect(!ram(physical),0))return mmu.read64(address);
  uint64_t value;
  std::memcpy(&value,reinterpret_cast<const void*>(base+physical),sizeof(value));
  return __builtin_bswap64(value);
}
__attribute__((always_inline)) inline float read_f32(MMU& mmu,uint32_t address) {
  uint32_t bits=read32(mmu,address);float value;std::memcpy(&value,&bits,4);return value;
}
__attribute__((always_inline)) inline double read_f64(MMU& mmu,uint32_t address) {
  uint64_t bits=read64(mmu,address);double value;std::memcpy(&value,&bits,8);return value;
}
}
