#pragma once
#include <cstdint>
#include <cstring>
#include <bit>
#include <immintrin.h>

namespace native_guest_batch {
// Preserve the original float -> double -> float path for exceptional encodings
// and DAZ-sensitive subnormals. Normal F32 and signed zero transport is exact.
inline bool StoreSafe(float value) {
    const uint32_t bits=std::bit_cast<uint32_t>(value), exponent=bits&0x7f800000u;
    return (exponent && exponent!=0x7f800000u) || !(bits&0x7fffffffu);
}
inline bool Finite(double value) {return (std::bit_cast<uint64_t>(value)&0x7ff0000000000000ull)!=0x7ff0000000000000ull;}
inline bool PackedInput(double value,unsigned exponent) {
    return (std::bit_cast<uint64_t>(value)&0x7fffffffffffffffull)<=((uint64_t(1023+exponent))<<52);
}
}
