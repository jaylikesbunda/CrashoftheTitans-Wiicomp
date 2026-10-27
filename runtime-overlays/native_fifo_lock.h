#pragma once
// NATIVE_FIFO_SRWLOCK_V1: the FIFO publish/drain mutex is taken ~6000 times per
// frame on the guest thread. MinGW std::mutex is winpthreads, whose lock/unlock
// walk the pthread self/TLS path; an SRW lock is a single cmpxchg. Same
// non-recursive, blocking semantics. This mutex is not paired with any condition
// variable (the drain/wake condvars use g_gx_drain_mutex), so it is safe to swap.
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <mutex>
#endif
namespace nwii::runtime {
struct NativeFifoLock {
#ifdef _WIN32
  SRWLOCK lock = SRWLOCK_INIT;
  void acquire() { ::AcquireSRWLockExclusive(&lock); }
  void release() { ::ReleaseSRWLockExclusive(&lock); }
#else
  std::mutex lock;
  void acquire() { lock.lock(); }
  void release() { lock.unlock(); }
#endif
};
struct NativeFifoLockGuard {
  NativeFifoLock& l;
  explicit NativeFifoLockGuard(NativeFifoLock& x) : l(x) { l.acquire(); }
  ~NativeFifoLockGuard() { l.release(); }
  NativeFifoLockGuard(const NativeFifoLockGuard&) = delete;
  NativeFifoLockGuard& operator=(const NativeFifoLockGuard&) = delete;
};
} // namespace nwii::runtime
