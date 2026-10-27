// NATIVE_RAM_WRITE_WATCH_V1: guest RAM allocated with MEM_WRITE_WATCH and per-page write generations.
#include "runtime/cpu_context.h"
#include <windows.h>
#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <vector>
namespace nwii::runtime {
namespace {
std::atomic<uint64_t> g_generation{1};
struct Track { const uint8_t* base=nullptr; size_t pages=0; bool ok=true; std::vector<uint64_t> page_generation; };
std::mutex g_track_mutex;
Track g_tracks[8];
Track* track_for(const uint8_t* base, size_t bytes) {
  for (auto& t : g_tracks) if (t.base == base) return &t;
  for (auto& t : g_tracks) if (!t.base) {t.base = base; t.pages = (bytes + 4095) / 4096; t.page_generation.assign(t.pages, 0); return &t;}
  return nullptr;
}
}
// native_ram_allocate / native_ram_release are header-only (cpu_context.h); the tracker below keys pages by bank base address.
uint64_t native_ram_generation() { return g_generation.load(std::memory_order_acquire); }
// Newest write generation among the pages of [base+offset, base+offset+bytes) after folding in every write the OS saw since the last
// query (the query resets the OS dirty bits and stamps those pages with a new generation). UINT64_MAX = not tracked, caller compares bytes.
uint64_t native_ram_newest_write(const uint8_t* base, size_t bank_bytes, size_t offset, size_t bytes) {
  if (!base || !bytes) return UINT64_MAX;
  std::lock_guard<std::mutex> lock(g_track_mutex);
  Track* track = track_for(base, bank_bytes);
  if (!track || !track->ok) return UINT64_MAX;
  const size_t first = offset >> 12, last = (offset + bytes - 1) >> 12;
  if (last >= track->pages) return UINT64_MAX;
  static thread_local std::vector<PVOID> dirty;
  dirty.resize(last - first + 1);
  ULONG_PTR count = dirty.size();
  DWORD granularity = 0;
  if (GetWriteWatch(WRITE_WATCH_FLAG_RESET, const_cast<uint8_t*>(base) + (first << 12), (last - first + 1) << 12, dirty.data(), &count, &granularity) != 0 || granularity != 4096) {
    track->ok = false; return UINT64_MAX;
  }
  if (count) {
    const uint64_t stamp = g_generation.fetch_add(1, std::memory_order_acq_rel) + 1;
    for (ULONG_PTR i = 0; i < count; ++i) track->page_generation[(static_cast<const uint8_t*>(dirty[i]) - base) >> 12] = stamp;
  }
  uint64_t newest = 0;
  for (size_t page = first; page <= last; ++page) newest = std::max(newest, track->page_generation[page]);
  return newest;
}
}
