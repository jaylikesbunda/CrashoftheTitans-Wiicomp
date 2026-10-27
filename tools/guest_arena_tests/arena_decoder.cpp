// Decoder exactness: thousands of (instruction form x register x addressing mode) variants run once on RAM (hardware
// semantics) and once on a faulting address (emulated by the arena handler); the results must agree register for register.
#include "stubs.h"
#include "runtime/native_guest_arena.h"
#include "decoder_variants.inc"
#include <memory>
#include <random>
using namespace nwii::runtime;
#define CHECK(c) do{ if(!(c)){ std::printf("FAIL %s:%d %s (variant %u: %s)\n",__FILE__,__LINE__,#c,index,v.text); std::fflush(stdout); std::exit(1);} }while(0)

static uint64_t bswap_n(uint64_t v, unsigned w) { return w == 1 ? v & 0xff : w == 2 ? __builtin_bswap16(uint16_t(v)) : w == 4 ? __builtin_bswap32(uint32_t(v)) : __builtin_bswap64(v); }
static uint64_t mask(unsigned w) { return w >= 8 ? ~0ull : ((1ull << (8 * w)) - 1); }

int main(int argc, char** argv) {
  setvbuf(stdout, nullptr, _IONBF, 0);
  auto ctx = std::make_unique<CPUContext>(); MMU& m = ctx->mmu; g_mmu = &m; g_ctx_ptr = ctx.get();
  std::mt19937_64 rng(argc > 1 ? std::atoll(argv[1]) : 5);
  for (size_t i = 0; i + 8 <= m.mem1.size(); i += 8) { uint64_t v = rng(); std::memcpy(m.mem1.data() + i, &v, 8); }
  const uintptr_t base = native_arena::base();
  auto host = [&](uint32_t guest) -> uint64_t { return base + (NWII_ARENA_MASK ? (guest & 0x3fffffffu) : guest); };
  unsigned done = 0, loads = 0, stores = 0, index = 0;
  for (const Variant& v : g_variants) {
    ++index;
    const unsigned w = v.width;
    const bool is_store = v.kind == 1;
    uint64_t in_ram[16], in_fault[16];
    for (auto& r : in_ram) r = rng();
    std::memcpy(in_fault, in_ram, sizeof in_ram);
    const uint32_t guest_ram = 0x80100000u + uint32_t(rng() % 0x10000u) * 8u;
    uint32_t guest_fault;
    if (is_store) guest_fault = (w == 8) ? 0x017FFFFCu : 0xCC008000u;           // 8-byte stores land in RAM + padding, others in the pipe
    else guest_fault = (w == 8) ? 0x017FFFFEu : 0xCC006800u + uint32_t(rng() % 64) * 4u;
    // address registers: the same index value in both runs, the base register absorbs the difference
    const uint64_t index_value = v.index >= 0 ? (uint64_t)(uint32_t)rng() : 0;
    if (v.index >= 0) { in_ram[v.index] = index_value; in_fault[v.index] = index_value; }
    in_ram[v.base] = host(guest_ram) - index_value * uint64_t(v.scale) - uint64_t(v.disp);
    in_fault[v.base] = host(guest_fault) - index_value * uint64_t(v.scale) - uint64_t(v.disp);
    // a base that is also the index would break the arithmetic above; the generator never produces that
    uint64_t out_ram[16], out_fault[16];
    std::memcpy(out_ram, in_ram, sizeof in_ram);
    std::memcpy(out_fault, in_fault, sizeof in_fault);

    if (!is_store) {
      // the value the slow path returns for the faulting address (big-endian numeric); RAM holds its big-endian bytes
      const uint64_t value = w == 1 ? m.native_read8_slow(guest_fault) : w == 2 ? m.native_read16_slow(guest_fault) : w == 4 ? m.native_read32_slow(guest_fault) : m.native_read64_slow(guest_fault);
      const uint64_t be = bswap_n(value, w);
      std::memcpy(m.mem1.data() + (guest_ram & 0x3fffffffu), &be, w);
      v.fn(out_ram);
      const auto s0 = native_arena::stats();
      v.fn(out_fault);
      const auto s1 = native_arena::stats();
      CHECK(s1.loads == s0.loads + 1);
      for (unsigned i = 0; i < 16; ++i) {
        if (i == 4) continue;
        if ((int)i == v.dest) {
          // a destination that is also an address register started with a different value in the two runs: only the bits
          // the load defines are comparable
          const bool shares = (int)i == v.base || (int)i == v.index;
          const uint64_t cmp = (v.kind == 0 || v.kind == 1) ? mask(w) : ~0ull;
          if (shares && (w < 4 && (v.kind == 0 || v.kind == 2))) {
            const unsigned shift = (w == 1 && v.high) ? 8 : 0;
            CHECK(((out_ram[i] >> shift) & mask(w)) == ((out_fault[i] >> shift) & mask(w)));
          }
          else if (shares && w >= 4) CHECK(out_ram[i] == out_fault[i]);
          else if (!shares) CHECK(out_ram[i] == out_fault[i]);
          (void)cmp;
        } else {
          CHECK(out_ram[i] == in_ram[i]);
          CHECK(out_fault[i] == in_fault[i]);
        }
      }
      ++loads;
    } else {
      const uint64_t cell = rng();
      std::memcpy(m.mem1.data() + (guest_ram & 0x3fffffffu), &cell, 8);
      v.fn(out_ram);
      auto source = [&](const uint64_t* regs) {
        uint64_t s = regs[v.dest];
        if (w == 1 && v.high) s >>= 8;
        return s & mask(w);
      };
      uint8_t bytes[8]; std::memcpy(bytes, m.mem1.data() + (guest_ram & 0x3fffffffu), 8);
      uint64_t got = 0; for (unsigned i = 0; i < w; ++i) got = (got << 8) | bytes[i];
      CHECK(got == source(in_ram));
      g_log.clear();
      const auto s0 = native_arena::stats();
      v.fn(out_fault);
      const auto s1 = native_arena::stats();
      CHECK(s1.stores == s0.stores + 1);
      const uint64_t expected = source(in_fault);
      if (w == 8) {
        uint8_t wb[8]; std::memcpy(wb, m.mem1.data() + 0x017FFFFC, 8);
        uint64_t g = 0; for (unsigned i = 0; i < 8; ++i) g = (g << 8) | wb[i];
        CHECK(g == expected);
      } else {
        CHECK(g_log.size() == 1 && g_log[0].kind == 2 && g_log[0].addr == w && g_log[0].value == expected);
      }
      for (unsigned i = 0; i < 16; ++i) if (i != 4) { CHECK(out_ram[i] == in_ram[i]); CHECK(out_fault[i] == in_fault[i]); }
      ++stores;
    }
    ++done;
  }
  const auto s = native_arena::stats();

  std::printf("arena decoder passed: %u variants (%u loads, %u stores), %llu unhandled faults%c", done, loads, stores, (unsigned long long)s.unhandled, 10);
  return s.unhandled == 0 ? 0 : 1;
}
