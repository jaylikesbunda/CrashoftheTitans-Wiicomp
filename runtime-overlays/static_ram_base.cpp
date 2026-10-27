// NATIVE_STATIC_RAM_V1: fixed-address guest RAM for the NWII_STATIC_RAM_BASE build (empty otherwise).
#if defined(NWII_STATIC_RAM_BASE) && __has_include("runtime/native_fastmem_allocator.h")
#include "runtime/native_fastmem_allocator.h"
#elif defined(NWII_STATIC_RAM_BASE)
#include "runtime/cpu_context.h"
#include <windows.h>
#include <cstdio>
#include <cstdlib>
namespace nwii::runtime {
namespace {
struct PlacedBank { uintptr_t base; size_t bytes; };
PlacedBank g_placed[4];
unsigned g_placed_count = 0;
}
// Commits one guest RAM bank at its compile-time host address. Records what it placed so a second MMU
// in the same process (test fixtures) re-uses the bank instead of aliasing it. A taken address is not
// recoverable at run time -- the fixed base is a compile-time decision -- so we abort with the
// instruction to use the dynamic-RAM executable (the normal build without NWII_STATIC_RAM_BASE).
void native_static_ram_commit(uintptr_t base, size_t bytes) {
  for (unsigned i = 0; i < g_placed_count; ++i) {
    if (g_placed[i].base == base) return; // bank already placed by an earlier MMU
  }
  void* mapped = VirtualAlloc(reinterpret_cast<void*>(base), bytes, MEM_RESERVE | MEM_COMMIT | MEM_WRITE_WATCH, PAGE_READWRITE);
  if (mapped != reinterpret_cast<void*>(base)) {
    const unsigned long error = static_cast<unsigned long>(GetLastError());
    std::fprintf(stderr,
                 "[StaticRAM] cannot place guest RAM at 0x%llx (error %lu). Another module already owns that "
                 "address; the fixed base is chosen at compile time. Use the dynamic-RAM executable "
                 "(build without NWII_STATIC_RAM_BASE, or set NWII_BUILD_FASTMEM_STATIC_BASE=0).\n",
                 static_cast<unsigned long long>(base), error);
    std::fflush(stderr);
    std::abort();
  }
  if (g_placed_count < 4) {
    g_placed[g_placed_count].base = base;
    g_placed[g_placed_count].bytes = bytes;
    ++g_placed_count;
  }
}
}
#endif
