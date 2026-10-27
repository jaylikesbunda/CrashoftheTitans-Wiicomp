#pragma once
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdlib>
#if defined(__x86_64__) || defined(_M_X64)
#include <immintrin.h>
#endif

namespace native_handoff {
using Clock=std::chrono::steady_clock;
inline unsigned budget(const char* name,unsigned fallback) {
    const char* text=std::getenv(name);
    if(!text || !*text)return fallback;
    char* end=nullptr;long value=std::strtol(text,&end,10);
    if(end==text || *end)return fallback;
    return static_cast<unsigned>(std::clamp(value,0l,1000l));
}
template<class Pending>
inline bool spin(Clock::time_point deadline,unsigned microseconds,Pending pending) {
    if(!microseconds)return false;
    const auto until=std::min(deadline,Clock::now()+std::chrono::microseconds(microseconds));
    while(Clock::now()<until) {
        if(pending())return true;
#if defined(__x86_64__) || defined(_M_X64)
        _mm_pause();_mm_pause();_mm_pause();_mm_pause();
#else
        std::atomic_signal_fence(std::memory_order_seq_cst);
#endif
    }
    return pending();
}
}
