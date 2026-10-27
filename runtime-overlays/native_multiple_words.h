#pragma once
#include "runtime/cpu_context.h"
#include "runtime/native_perf_census.h"
namespace native_multiple_words {
inline const bool enabled = std::getenv("NWII_NO_MULTIPLE_WORD_SPAN") == nullptr;
__attribute__((always_inline)) inline uint8_t* Span(nwii::runtime::MMU& memory,
                                                   uint32_t address, unsigned bytes) {
  native_perf_census::GuestAdd(native_perf_census::MultipleRequests);
  if (!enabled) return nullptr;
  const uint32_t physical = address & 0x3fffffffu;
  const intptr_t bias = memory.page_bias[physical >> 16];
  if (bias <= 1 || (physical & 65535u) > 65536u - bytes ||
      nwii::runtime::native_watch_span_may_hit(address, bytes)) return nullptr;
  native_perf_census::GuestAdd(native_perf_census::MultipleFast);
  native_perf_census::GuestAdd(native_perf_census::MultipleWords, bytes / 4u);
  return reinterpret_cast<uint8_t*>(bias + physical);
}
__attribute__((always_inline)) inline uint32_t Load(const uint8_t* pointer) {
  uint32_t bits; std::memcpy(&bits, pointer, 4); return __builtin_bswap32(bits);
}
__attribute__((always_inline)) inline void Store(uint8_t* pointer, uint32_t value) {
  value = __builtin_bswap32(value); std::memcpy(pointer, &value, 4);
}
}
