// Link-time specialization of MinGW libstdc++ steady_clock. Same QPC epoch;
// verified10MHz counters have an exact100ns conversion and need no POSIX layer.
//
// NATIVE_TSC_STEADY_CLOCK_V1: the guest polls steady_clock about 33,000 times per
// frame (capture 20261006-102126: 399 guest stacks, ntdll QPC plus this wrapper).
// With an invariant TSC the clock is extrapolated from the TSC between QPC
// anchors, refreshed at most every 500 microseconds of TSC time. The anchor never
// moves backward and is re-fit against the first anchor, so extrapolation error
// stays in the tens of nanoseconds. Without an invariant TSC, a non-10MHz QPC,
// or NWII_NO_TSC_CLOCK, the original QPC path is used. NWII_NO_FAST_CLOCK keeps
// disabling both specializations.
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <cpuid.h>
#include <x86intrin.h>
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
extern "C" std::chrono::steady_clock::time_point __real__ZNSt6chrono3_V212steady_clock3nowEv();
namespace {
inline int64_t qpc_ns(bool& ok) {
  LARGE_INTEGER counter{};
  ok=QueryPerformanceCounter(&counter) && counter.QuadPart>=0 &&
     counter.QuadPart<=std::numeric_limits<int64_t>::max()/100;
  return ok?counter.QuadPart*100:0;
}
// Sequence-locked anchor shared by every thread. Writers hold `refreshing`.
struct TscClock {
  std::atomic<uint32_t> seq{0};
  std::atomic<bool> refreshing{false};
  // Relaxed atomics keep the sequence-locked snapshot free of data races.
  std::atomic<uint64_t> base_tsc{0}, refresh_tsc{0}, mult{0};   // mult: nanoseconds per tick * 2^32
  std::atomic<int64_t> base_ns{0};
  uint64_t first_tsc=0; int64_t first_ns=0;
  uint64_t window_ticks=0, fit_ticks=0;
  bool ready=false;
};
TscClock g_clock;

inline uint64_t scaled(uint64_t ticks,uint64_t mult) {
  return uint64_t((static_cast<unsigned __int128>(ticks)*mult)>>32);
}
// QPC read bracketed by TSC reads; the midpoint removes most call latency.
bool anchor_sample(uint64_t& tsc,int64_t& ns) {
  // A preemption between the two TSC reads widens the bracket and skews the pair by the stall, so
  // retry until the bracket is within 4x the narrowest one seen (callers hold the refresh lock).
  static uint64_t narrowest=~0ull;
  uint64_t best_width=~0ull;bool have=false;
  for(unsigned attempt=0;attempt<6;++attempt){
    unsigned aux=0;
    const uint64_t a=__rdtscp(&aux);bool ok=false;const int64_t q=qpc_ns(ok);const uint64_t b=__rdtscp(&aux);
    if(!ok||b<a)continue;
    const uint64_t width=b-a;
    if(width<best_width){best_width=width;tsc=a+width/2;ns=q;have=true;}
    if(width<narrowest)narrowest=width;
    if(best_width<=narrowest*4+64)break;
  }
  return have;
}
bool invariant_tsc() {
  unsigned eax=0,ebx=0,ecx=0,edx=0;
  if(!__get_cpuid(0x80000000u,&eax,&ebx,&ecx,&edx)||eax<0x80000007u)return false;
  __cpuid(0x80000007u,eax,ebx,ecx,edx);
  return (edx&(1u<<8))!=0;
}
bool initialize() {
  uint64_t tsc0=0,tsc1=0;int64_t ns0=0,ns1=0;
  if(!anchor_sample(tsc0,ns0))return false;
  // Spin about five milliseconds of QPC time for the initial rate estimate.
  do{if(!anchor_sample(tsc1,ns1))return false;}while(ns1-ns0<5000000);
  if(tsc1<=tsc0)return false;
  const double ns_per_tick=double(ns1-ns0)/double(tsc1-tsc0);
  if(!(ns_per_tick>0.02&&ns_per_tick<4.0))return false;      // 0.25 to 50 GHz
  g_clock.mult.store(uint64_t(ns_per_tick*4294967296.0),std::memory_order_relaxed);
  g_clock.first_tsc=tsc1;g_clock.first_ns=ns1;
  g_clock.base_tsc.store(tsc1,std::memory_order_relaxed);g_clock.base_ns.store(ns1,std::memory_order_relaxed);
  g_clock.window_ticks=uint64_t(500000.0/ns_per_tick);        // 500 us of TSC time
  g_clock.fit_ticks=uint64_t(50000000.0/ns_per_tick);          // 50 ms baseline before re-fitting
  g_clock.refresh_tsc.store(tsc1+g_clock.window_ticks,std::memory_order_relaxed);
  g_clock.ready=true;return true;
}
void refresh(uint64_t observed_tsc) {
  if(g_clock.refreshing.exchange(true,std::memory_order_acquire))return;
  uint64_t tsc=0;int64_t ns=0;
  const uint64_t old_tsc=g_clock.base_tsc.load(std::memory_order_relaxed),old_mult=g_clock.mult.load(std::memory_order_relaxed);
  const int64_t old_ns=g_clock.base_ns.load(std::memory_order_relaxed);
  if(anchor_sample(tsc,ns)&&tsc>old_tsc) {
    uint64_t mult=old_mult;
    if(tsc-g_clock.first_tsc>=g_clock.fit_ticks&&ns>g_clock.first_ns) {
      const unsigned __int128 fitted=(static_cast<unsigned __int128>(uint64_t(ns-g_clock.first_ns))<<32)/(tsc-g_clock.first_tsc);
      if(fitted<(static_cast<unsigned __int128>(4)<<32))mult=uint64_t(fitted);
    }
    const int64_t extrapolated=old_ns+int64_t(scaled(tsc-old_tsc,old_mult));
    g_clock.seq.fetch_add(1,std::memory_order_acq_rel);          // odd: writing
    g_clock.base_tsc.store(tsc,std::memory_order_relaxed);
    g_clock.base_ns.store(ns>extrapolated?ns:extrapolated,std::memory_order_relaxed);
    g_clock.mult.store(mult,std::memory_order_relaxed);
    g_clock.refresh_tsc.store(tsc+g_clock.window_ticks,std::memory_order_relaxed);
    g_clock.seq.fetch_add(1,std::memory_order_release);          // even: stable
  } else {
    // Keep the old anchor but avoid retrying on every call.
    g_clock.seq.fetch_add(1,std::memory_order_acq_rel);
    g_clock.refresh_tsc.store(observed_tsc+g_clock.window_ticks,std::memory_order_relaxed);
    g_clock.seq.fetch_add(1,std::memory_order_release);
  }
  g_clock.refreshing.store(false,std::memory_order_release);
}
bool tsc_now(int64_t& out) {
  for(;;) {
    const uint32_t before=g_clock.seq.load(std::memory_order_acquire);
    if(before&1u)continue;
    const uint64_t base_tsc=g_clock.base_tsc.load(std::memory_order_relaxed),refresh_tsc=g_clock.refresh_tsc.load(std::memory_order_relaxed),
      mult=g_clock.mult.load(std::memory_order_relaxed);
    const int64_t base_ns=g_clock.base_ns.load(std::memory_order_relaxed);
    // NATIVE_TSC_RDTSC_V1: a plain (non-serializing) TSC read. The sequence lock
    // and the acquire fence below already order the snapshot; the aux/processor
    // id from __rdtscp is unused, and its serialization dominated the hot path.
    const uint64_t tsc=__rdtsc();
    std::atomic_thread_fence(std::memory_order_acquire);
    if(g_clock.seq.load(std::memory_order_relaxed)!=before)continue;
    const uint64_t delta=tsc>base_tsc?tsc-base_tsc:0;
    out=base_ns+int64_t(scaled(delta,mult));
    if(tsc>=refresh_tsc)refresh(tsc);
    return true;
  }
}
}
extern "C" std::chrono::steady_clock::time_point __wrap__ZNSt6chrono3_V212steady_clock3nowEv() {
  static const int mode=[] {
    LARGE_INTEGER frequency{};
    if(std::getenv("NWII_NO_FAST_CLOCK")||!QueryPerformanceFrequency(&frequency)||frequency.QuadPart!=10000000)return 0;
    if(!std::getenv("NWII_NO_TSC_CLOCK")&&invariant_tsc()&&initialize())return 2;
    return 1;
  }();
  if(mode==2) {
    int64_t ns=0;
    if(tsc_now(ns))return std::chrono::steady_clock::time_point(std::chrono::nanoseconds(ns));
  }
  if(mode!=0) {
    bool ok=false;const int64_t ns=qpc_ns(ok);
    if(ok)return std::chrono::steady_clock::time_point(std::chrono::nanoseconds(ns));
  }
  return __real__ZNSt6chrono3_V212steady_clock3nowEv();
}
