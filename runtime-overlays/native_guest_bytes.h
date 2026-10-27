#pragma once
#include "runtime/cpu_context.h"
#include <algorithm>
#include <vector>
#include <new>
namespace native_guest_bytes {
using nwii::runtime::MMU;
using nwii::runtime::native_watch_span_may_hit;
// NATIVE_GUEST_BYTES_V1: preserve byte observers outside complete ordinary RAM.
inline bool Enabled() {
  static const bool enabled = std::getenv("NWII_NO_IOS_BULK_TRANSFER") == nullptr;
  return enabled;
}
inline uint8_t* OrdinarySpan(MMU& memory, uint32_t address, uint32_t bytes) {
  if (!Enabled() || !bytes || native_watch_span_may_hit(address, bytes)) return nullptr;
  const uint32_t physical = address & 0x3fffffffu;
  if (physical < MMU::kMem1Limit && bytes <= MMU::kMem1Limit - physical)
    return memory.mem1.data() + physical;
  const uint32_t offset = physical - 0x10000000u;
  if (offset < MMU::kMem2Limit && bytes <= MMU::kMem2Limit - offset)
    return memory.mem2.data() + offset;
  return nullptr;
}
inline void Copy(MMU& memory, uint32_t address, const uint8_t* bytes, uint32_t count) {
  if (auto* target = OrdinarySpan(memory, address, count)) {
    // Disc scratch is a separate allocation and cannot alias guest RAM.
    std::memcpy(target, bytes, count); return;
  }
  for (uint32_t i = 0; i < count; ++i) memory.write8(address + i, bytes[i]);
}
inline void Zero(MMU& memory, uint32_t address, uint32_t count) {
  if (auto* target = OrdinarySpan(memory, address, count)) {
    std::memset(target, 0, count); return;
  }
  for (uint32_t i = 0; i < count; ++i) memory.write8(address + i, 0);
}
struct ScratchState { std::vector<uint8_t> bytes; bool busy = false; };
#ifdef _WIN32
inline void NTAPI ReleaseScratch(void* pointer) { delete static_cast<ScratchState*>(pointer); }
inline ScratchState* LocalScratch() {
  // Native FLS owns cleanup at thread/fiber exit; no nontrivial MinGW emulated TLS destructor.
  static const DWORD index = FlsAlloc(ReleaseScratch);
  if (index == FLS_OUT_OF_INDEXES) return nullptr;
  if (auto* state = static_cast<ScratchState*>(FlsGetValue(index))) return state;
  auto* state = new (std::nothrow) ScratchState;
  if (!state) return nullptr;
  if (!FlsSetValue(index, state)) { delete state; return nullptr; }
  return state;
}
#else
inline ScratchState* LocalScratch() { static thread_local ScratchState state; return &state; }
#endif
class ReadBuffer {
  std::vector<uint8_t> local;
  ScratchState* owner = nullptr;
  std::vector<uint8_t>* buffer = &local;
public:
  explicit ReadBuffer(uint32_t count) {
    if (count && Enabled() && count <= 8u * 1024u * 1024u) {
      if (auto* state = LocalScratch(); state && !state->busy) { owner = state; buffer = &state->bytes; }
    }
    const size_t dirty = std::min<size_t>(buffer->size(), count);
    buffer->resize(count);
    // Newly grown bytes are value-initialized; reused bytes must also be zero.
    std::fill(buffer->begin(), buffer->begin() + dirty, uint8_t(0));
    if (owner) owner->busy = true;
  }
  ~ReadBuffer() { if (owner) owner->busy = false; }
  ReadBuffer(const ReadBuffer&) = delete;
  ReadBuffer& operator=(const ReadBuffer&) = delete;
  uint8_t* data() { return buffer->data(); }
};
}
