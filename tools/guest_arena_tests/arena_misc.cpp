// Arena behaviours around the edges: store traps (page 0 word 12, watch), kernel reads into trapped pages (HostWriteScope),
// the software write tracker that replaces GetWriteWatch, and the fault-site recorder.
#include "stubs.h"
#include "runtime/native_guest_arena.h"
#include <algorithm>
#include <chrono>
#include <cstring>
#include <vector>
#include <memory>
#include <cstdio>
#include <thread>
#include <fstream>
#include "runtime/native_savestate.h"
using namespace nwii::runtime;
#define CHECK(c) do{ if(!(c)){ std::printf("FAIL %s:%d %s%c",__FILE__,__LINE__,#c,10); std::fflush(stdout); std::exit(1);} }while(0)


// native_ss_ram as written in native_savestate_ios.inc (including the arena scope that tools/fix_guest_arena.py adds).
static void ss_ram(native_ss::Stream& s, uint8_t* base, size_t bytes) {
  native_arena::HostWriteScope arena_scope(base, s.loading ? bytes : 0);
  uint64_t n = bytes; s.pod(n);
  CHECK(!s.loading || n == bytes);
  static const uint8_t zero[4096] = {};
  for (size_t off = 0; off < bytes; off += 4096) {
    const size_t len = std::min<size_t>(4096, bytes - off);
    uint8_t present = s.loading ? 0 : uint8_t(std::memcmp(base + off, zero, len) != 0);
    s.pod(present);
    if (present) s.raw(base + off, len);
    else if (s.loading) std::memset(base + off, 0, len);
  }
}

static size_t count_events(uint8_t kind) { size_t n = 0; for (const Event& e : g_log) n += e.kind == kind; return n; }

int main() {
  setvbuf(stdout, nullptr, _IONBF, 0);
  auto ctx = std::make_unique<CPUContext>(); MMU& m = ctx->mmu; g_mmu = &m; g_ctx_ptr = ctx.get();
  CHECK(native_arena::active());

  // ---- page 0: word 12 drops integer and float word stores, everything else in the page stores ----
  std::memset(m.mem1.data(), 0, 64);
  native_arena::st32(0x80000010u, 0xAABBCCDDu); CHECK(native_arena::ld32(0x80000010u) == 0xAABBCCDDu);
  native_arena::st32(0x8000000Cu, 0x11223344u); CHECK(native_arena::ld32(0x8000000Cu) == 0 && m.mem1[12] == 0);
  native_arena::stf32(0x0000000Cu, 1.5f); CHECK(native_arena::ld32(0x0000000Cu) == 0);
  native_arena::st8(0x8000000Cu, 0x55);   // byte store to the same word is an ordinary store (as in MMU::write8)
  CHECK(m.mem1[12] == 0x55);
  m.mem1[12] = 0;
  // the MMU slow path (host code) writes the page too and is not mistaken for a guest store
  g_log.clear(); m.write32(0x80000020u, 0x01020304u); CHECK(native_arena::ld32(0x80000020u) == 0x01020304u && g_log.empty());
  std::printf("page 0 rules passed%c", 10);

  // ---- kernel reads into trapped pages ----
  {
    const char* path = "arena_misc_tmp.bin";
    { std::ofstream f(path, std::ios::binary); std::vector<char> data(65536, 0x5a); f.write(data.data(), data.size()); }
    std::FILE* f = std::fopen(path, "rb");
    uint8_t* page = m.mem1.data() + 0x100;                 // inside the trapped page 0
    // a 64 KiB fread goes straight to ReadFile: the kernel cannot recover from the read-only page...
    size_t without = std::fread(page, 1, 8192, f);
    std::fseek(f, 0, SEEK_SET);
    size_t with;
    { native_arena::HostWriteScope scope(page, 8192); with = std::fread(page, 1, 8192, f); }
    std::fclose(f);
    std::remove(path);
    std::printf("fread into trapped page: without scope %zu bytes, with scope %zu bytes%c", without, with, 10);
    CHECK(with == 8192 && page[0] == 0x5a && page[4000] == 0x5a);
    // the page is trapped again after the scope
    g_log.clear(); native_arena::st32(0x8000000Cu, 7); CHECK(native_arena::ld32(0x8000000Cu) == 0);
  }


  // ---- save-state RAM round trip through the MMU window, with page 0 and a watch page trapped ----
  {
    g_watch_addr = 0x80010124u; m.native_init_page_bias(); native_arena::sync_watch();
    for (size_t i = 0; i < m.mem1.size(); ++i) m.mem1.data()[i] = uint8_t((i * 2654435761u) >> 13);
    for (size_t i = 0; i < m.mem2.size(); ++i) m.mem2.data()[i] = uint8_t((i * 40503u) >> 5);
    std::memset(m.mem1.data() + 0x40000, 0, 8192);
    std::vector<uint8_t> a1(m.mem1.data(), m.mem1.data() + m.mem1.size()), a2(m.mem2.data(), m.mem2.data() + m.mem2.size());
    native_ss::Stream out; ss_ram(out, m.mem1.data(), m.mem1.size()); ss_ram(out, m.mem2.data(), m.mem2.size());
    std::memset(m.mem1.data(), 0xee, m.mem1.size()); std::memset(m.mem2.data(), 0xee, m.mem2.size());
    native_ss::Stream in; in.loading = true; in.buf = out.buf;
    ss_ram(in, m.mem1.data(), m.mem1.size()); ss_ram(in, m.mem2.data(), m.mem2.size());
    CHECK(!std::memcmp(a1.data(), m.mem1.data(), a1.size()) && !std::memcmp(a2.data(), m.mem2.data(), a2.size()));
    // the sticky pages trap again after the load, and the guest accessors see the loaded bytes
    const uint32_t word12 = uint32_t(a1[12]) << 24 | uint32_t(a1[13]) << 16 | uint32_t(a1[14]) << 8 | a1[15];
    g_log.clear(); native_arena::st32(0x8000000Cu, word12 ^ 0xffffffffu); CHECK(native_arena::ld32(0x8000000Cu) == word12);
    CHECK(native_arena::ld32(0x80000100u) == (uint32_t(a1[0x100]) << 24 | uint32_t(a1[0x101]) << 16 | uint32_t(a1[0x102]) << 8 | a1[0x103]));
    g_watch_addr = 0; m.native_init_page_bias(); native_arena::sync_watch();
    std::printf("save-state RAM round trip passed%c", 10);
  }

  // ---- watch: exactly one hit per guest store, whichever accessor performs it; no hit for other words of the page ----
  g_watch_addr = 0x80010124u; m.native_init_page_bias(); native_arena::sync_watch();
  g_log.clear();
  native_arena::st32(0x80010124u, 1);              // guest accessor
  CHECK(count_events(1) == 1);
  m.write32(0x80010124u, 2);                       // host MMU path: the slow path reports the hit itself
  CHECK(count_events(1) == 2);
  native_arena::st32(0x80010128u, 3); native_arena::st8(0x80010125u, 4); native_arena::st16(0x80010122u, 5);   // 0x80010122..23 is the previous word
  CHECK(count_events(1) == 3);   // only the st8 inside the watched word hits
  native_arena::stf64(0x80010120u, 2.0);           // 8-byte store covering the watched word: doubleword predicate
  CHECK(count_events(1) == 4);
  CHECK(native_arena::ld32(0x80010128u) == 3);
  g_watch_addr = 0; m.native_init_page_bias(); native_arena::sync_watch();
  g_log.clear(); native_arena::st32(0x80010124u, 9); CHECK(g_log.empty());
  std::printf("watch hits passed%c", 10);

  // ---- recorder ----
  {
    for (int i = 0; i < 3; ++i) native_arena::st32(0xCC008000u, 0x100 + i);       // write-gather page
    volatile uint32_t sink = native_arena::ld32(0xCC006C00u); (void)sink;
    native_arena::report_sites("arena_misc_sites.txt");
    std::ifstream in("arena_misc_sites.txt"); std::string line; bool header = false, wg = false, load = false;
    while (std::getline(in, line)) {
      if (line.rfind("# rva", 0) == 0) header = true;
      if (line.find(" store 4 0x") != std::string::npos && line.find("c008000 1 ") != std::string::npos) wg = true;   // mask layout: 0x0c008000
      if (line.find(" load 4 0x") != std::string::npos && line.find("c006c00 0 ") != std::string::npos) load = true;
    }
    in.close(); std::remove("arena_misc_sites.txt");
    CHECK(header && wg && load);
    std::printf("fault-site recorder passed%c", 10);
  }

  // ---- tracker (only with NWII_ARENA_TRACK=1 in the mirrored layouts) ----
  if (const char* track = std::getenv("NWII_ARENA_TRACK"); !track || track[0] != '0') {
    uint8_t* base = m.mem1.data();
    const uint64_t g0 = native_ram_generation();
    uint64_t first = native_ram_newest_write(base, m.mem1.size(), 0x100000, 0x20000);
    CHECK(first != UINT64_MAX && first > g0);
    uint64_t second = native_ram_newest_write(base, m.mem1.size(), 0x100000, 0x20000);
    CHECK(second == first);                                  // armed, nothing written: unchanged
    native_arena::st32(0x80110000u, 1);                      // guest store to an armed page (through the arena)
    uint64_t third = native_ram_newest_write(base, m.mem1.size(), 0x100000, 0x20000);
    CHECK(third > second);
    CHECK(native_ram_newest_write(base, m.mem1.size(), 0x100000, 0x20000) == third);
    base[0x101000] = 7;                                      // host write through the MMU window
    uint64_t fourth = native_ram_newest_write(base, m.mem1.size(), 0x100000, 0x20000);
    CHECK(fourth > third);
    // writes outside the queried range do not disturb it
    native_arena::st32(0x80200000u, 1);
    CHECK(native_ram_newest_write(base, m.mem1.size(), 0x100000, 0x20000) == fourth);
    // a kernel read into an armed page works through the scope
    std::vector<char> data(8192, 3);
    { std::ofstream f("arena_misc_tmp2.bin", std::ios::binary); f.write(data.data(), data.size()); }
    std::FILE* f = std::fopen("arena_misc_tmp2.bin", "rb");
    size_t n;
    { native_arena::HostWriteScope scope(base + 0x102000, 8192); n = std::fread(base + 0x102000, 1, 8192, f); }
    std::fclose(f); std::remove("arena_misc_tmp2.bin");
    CHECK(n == 8192 && base[0x102000] == 3);
    uint64_t fifth = native_ram_newest_write(base, m.mem1.size(), 0x100000, 0x20000);
    CHECK(fifth > fourth);
    // a page written over and over is reported as untracked (the caller compares bytes)
    uint64_t last = 0; bool untracked = false;
    for (int i = 0; i < 64 && !untracked; ++i) {
      native_arena::st32(0x80110000u, i);
      last = native_ram_newest_write(base, m.mem1.size(), 0x100000, 0x20000);
      untracked = last == UINT64_MAX;
    }
    CHECK(untracked);
    std::printf("write tracker passed%c", 10);
  }
  // ---- texture validation cost: tracker clean query versus byte comparison (NWII_TRACK_BENCH=1; measurement only) ----
  if (std::getenv("NWII_TRACK_BENCH") && (!std::getenv("NWII_ARENA_TRACK") || std::getenv("NWII_ARENA_TRACK")[0] != '0')) {
    using clk = std::chrono::steady_clock;
    uint8_t* base = m.mem1.data();
    const uint32_t off = 0x200000;
    const int iters = 40000;
    for (size_t n : {size_t(1024), size_t(2048), size_t(4096), size_t(8192), size_t(16384), size_t(32768), size_t(65536)}) {
      std::memset(base + off, 0x5a, n);
      std::vector<uint8_t> copy(base + off, base + off + n);
      CHECK(native_ram_newest_write(base, m.mem1.size(), off, n) != UINT64_MAX);   // arms the pages
      CHECK(native_ram_newest_write(base, m.mem1.size(), off, n) != UINT64_MAX);   // clean from here on
      uint64_t sink = 0;
      auto t0 = clk::now();
      for (int i = 0; i < iters; ++i) sink += native_ram_newest_write(base, m.mem1.size(), off, n);
      auto t1 = clk::now();
      int equal = 0;
      for (int i = 0; i < iters; ++i) {
        const uint8_t* src = base + off;
        const uint8_t* dst = copy.data();
        asm volatile("" : "+r"(src), "+r"(dst));   // keep the compare inside the loop
        equal += std::memcmp(src, dst, n) == 0;
      }
      auto t2 = clk::now();
      const double query_ns = std::chrono::duration<double, std::nano>(t1 - t0).count() / iters;
      const double cmp_ns = std::chrono::duration<double, std::nano>(t2 - t1).count() / iters;
      std::printf("track-bench bytes=%zu query_ns=%.1f memcmp_ns=%.1f (sink %llu, equal %d)%c", n, query_ns, cmp_ns,
                  static_cast<unsigned long long>(sink & 1), equal, 10);
    }
  }
  const auto s = native_arena::stats();
  CHECK(s.unhandled == 0);
  std::printf("arena misc passed (%llu loads, %llu stores emulated, %llu sticky traps)%c", (unsigned long long)s.loads, (unsigned long long)s.stores, (unsigned long long)s.sticky, 10);
}
