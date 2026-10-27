// NATIVE_LARGE_BLOCK_CACHE_V1: keep large operator-new blocks committed instead of returning them to the OS.
//
// Capture frame-cost-20261006-121918-356500, renderer thread (33.6 ms CPU/frame): ntoskrnl samples owned by
// vector<VertexData>::emplace_back (304 stacks), ~vector<GXCommand> (152 + 55 ntdll) and operator new (131)
// are page-fault and heap-trim work, about 15% of the thread. About 198k vertices per frame are decoded into
// 168-byte VertexData vectors; each draw's vector is allocated, grown and freed every frame.
// Blocks of 32 KiB or more are rounded to a size class (four steps per octave), recycled through bounded
// per-class free lists, and never returned to the OS. Smaller blocks use plain malloc/free.
// NWII_NO_LARGE_BLOCK_CACHE=1 disables recycling.
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <mutex>
#include <new>
#include <malloc.h>
namespace {
constexpr size_t kMinBlock = 32u * 1024u, kMaxBlock = 512u << 20, kMaxCachedBytes = 640u << 20;
constexpr unsigned kClasses = 96, kMaxPerClass = 16;
struct Cache {
  std::mutex lock;
  void* free_list[kClasses][kMaxPerClass] = {};
  unsigned count[kClasses] = {};
  size_t cached_bytes = 0;
  bool enabled = std::getenv("NWII_NO_LARGE_BLOCK_CACHE") == nullptr;
};
Cache& cache() { static Cache* instance = new Cache; return *instance; }
// size class: 4 steps per octave starting at 32 KiB (32, 40, 48, 56, 64, 80 ...)
size_t class_size(unsigned index) {
  const unsigned octave = index / 4, step = index % 4;
  return (kMinBlock << octave) + (kMinBlock << octave) / 4 * step;
}
unsigned class_for(size_t bytes) { // smallest class >= bytes, or kClasses
  for (unsigned index = 0; index < kClasses; ++index) if (class_size(index) >= bytes) return index;
  return kClasses;
}
void* allocate(size_t bytes) {
  if (bytes < kMinBlock || bytes > kMaxBlock) { if (!bytes) bytes = 1; return std::malloc(bytes); }
  Cache& c = cache();
  const unsigned index = class_for(bytes);
  if (index >= kClasses) return std::malloc(bytes);
  if (c.enabled) {
    std::lock_guard<std::mutex> guard(c.lock);
    if (c.count[index]) { void* block = c.free_list[index][--c.count[index]]; c.cached_bytes -= class_size(index); return block; }
  }
  return std::malloc(class_size(index));
}
void release(void* block) {
  if (!block) return;
  const size_t size = _msize(block);
  if (size >= kMinBlock && size <= kMaxBlock) {
    Cache& c = cache();
    if (c.enabled) {
      for (unsigned index = 0; index < kClasses; ++index) {
        if (class_size(index) != size) continue;
        std::lock_guard<std::mutex> guard(c.lock);
        if (c.count[index] < kMaxPerClass && c.cached_bytes + size <= kMaxCachedBytes) {
          c.free_list[index][c.count[index]++] = block; c.cached_bytes += size; return;
        }
        break;
      }
    }
  }
  std::free(block);
}
}
void* operator new(size_t bytes) { if (void* p = allocate(bytes)) return p; throw std::bad_alloc(); }
void* operator new[](size_t bytes) { if (void* p = allocate(bytes)) return p; throw std::bad_alloc(); }
void* operator new(size_t bytes, const std::nothrow_t&) noexcept { return allocate(bytes); }
void* operator new[](size_t bytes, const std::nothrow_t&) noexcept { return allocate(bytes); }
void operator delete(void* p) noexcept { release(p); }
void operator delete[](void* p) noexcept { release(p); }
void operator delete(void* p, size_t) noexcept { release(p); }
void operator delete[](void* p, size_t) noexcept { release(p); }
void operator delete(void* p, const std::nothrow_t&) noexcept { release(p); }
void operator delete[](void* p, const std::nothrow_t&) noexcept { release(p); }
