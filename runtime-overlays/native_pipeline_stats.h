#pragma once
#include <atomic>
#include <cstdint>
namespace native_pipeline {
inline std::atomic<uint64_t> serial_batches{0},pool_batches{0},chunk_jobs{0},serial_vertices{0},pool_vertices{0},largest_vertices{0},largest_job_us{0};
inline std::atomic<uint64_t> requests{0},coalesced{0},drains{0},latency_us{0},max_latency_us{0},last_generation{0};
inline std::atomic<uint64_t> effect_fast{0},effect_fallback{0},saved_stores{0},saved_loads{0};
inline void max(std::atomic<uint64_t>& target,uint64_t value){auto old=target.load(std::memory_order_relaxed);while(old<value && !target.compare_exchange_weak(old,value,std::memory_order_relaxed)){} }
inline void Reset(){serial_batches=0;pool_batches=0;chunk_jobs=0;serial_vertices=0;pool_vertices=0;largest_vertices=0;largest_job_us=0;requests=0;coalesced=0;drains=0;latency_us=0;max_latency_us=0;last_generation=0;effect_fast=0;effect_fallback=0;saved_stores=0;saved_loads=0;}
}
