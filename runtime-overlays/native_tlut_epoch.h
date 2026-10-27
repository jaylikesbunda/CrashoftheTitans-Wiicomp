#pragma once
#include <atomic>
#include <cstdint>
// All palette writers and state restoration invalidate the shared epoch.
// Readers sample before comparing/decoding, never after a source read.
namespace native_tlut {
inline std::atomic<uint64_t> epoch{1};
inline std::atomic<uint64_t> efb_serial{1};
inline uint64_t Read(){return epoch.load(std::memory_order_acquire);}
inline void Changed(){epoch.fetch_add(1,std::memory_order_release);}
}
