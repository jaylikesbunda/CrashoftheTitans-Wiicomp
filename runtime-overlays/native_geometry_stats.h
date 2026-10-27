#pragma once
#include <atomic>
namespace native_geometry {
inline std::atomic<unsigned long long> decoded_hits{0},decoded_misses{0},gpu_hits{0},gpu_misses{0},validated_bytes{0},saved_upload_bytes{0};
inline std::atomic<unsigned long long> format_draws{0},retained_rejects{0};
inline void Reset(){decoded_hits=0;decoded_misses=0;gpu_hits=0;gpu_misses=0;validated_bytes=0;saved_upload_bytes=0;format_draws=0;retained_rejects=0;}
}
