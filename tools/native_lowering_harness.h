#pragma once
// NATIVE_SOURCE_LOWERING_V1
//
// Differential-test harness for source-backed native lowerings.
//
// The harness runs the *translated* guest function and the *native* lowering
// on identical input states and compares everything the guest can observe:
//
//   * the ordered list of guest memory accesses (address, width, value),
//   * the ordered list of writes to the GX hardware ports,
//   * the guest RAM bytes in the ranges the two paths touched,
//   * the final guest register file, condition register, LR/CTR/XER and PC,
//   * the retired guest instruction count.
//
// The translated function is extracted from the generated chunk by
// tools/check_native_lowering.py and is compiled with its memory accesses
// routed through the recorder, so both paths produce comparable logs.
//
// What the harness does NOT validate:
//   * behaviour outside the tested entry (interior entries, traced calls and
//     disabled lowerings keep using the translated path by construction),
//   * the guest's own use of the volatile registers the native path does not
//     reproduce - tools/check_native_lowering.py proves statically that no
//     call site reads them,
//   * whole-game behaviour; this is a local differential test of one function.
#include "runtime/cpu_context.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <string>
#include <vector>

namespace nwii::runtime::native_lower_test {

// ---------------------------------------------------------------------------
// Effect log
// ---------------------------------------------------------------------------
struct Effect {
  uint32_t address;
  int width;          // 1, 2, 4 or 8
  uint64_t value;
  bool is_write;
  bool operator==(const Effect& o) const {
    return address == o.address && width == o.width && value == o.value && is_write == o.is_write;
  }
};

struct EffectLog {
  std::vector<Effect> entries;
  void clear() { entries.clear(); }
  void add(uint32_t address, int width, uint64_t value, bool is_write) {
    entries.push_back(Effect{address, width, value, is_write});
  }
};

inline EffectLog g_effects;
inline EffectLog g_ports;
inline bool g_recording = false;

inline void record_effect(uint32_t address, int width, uint64_t value, bool is_write) {
  if (g_recording) g_effects.add(address, width, value, is_write);
}

// A write to a GX hardware port. The port stubs record it, so the ordered
// external effects of the two paths are comparable.
inline void record_port(uint32_t address, int width, uint64_t value) {
  if (g_recording) g_ports.add(address, width, value, true);
}

// ---------------------------------------------------------------------------
// Recorder used by the extracted (translated) guest function.
//
// The extracted text is rewritten by the driver so that every guest memory
// access goes through these helpers instead of calling the MMU directly. The
// helpers perform the real access first, so the external effect and the
// recorded order are the same for both paths.
// ---------------------------------------------------------------------------
inline void rec_write8(MMU& mmu, uint32_t a, uint8_t v) {
  mmu.write8(a, v);
  record_effect(a, 1, v, true);
}
inline void rec_write16(MMU& mmu, uint32_t a, uint16_t v) {
  mmu.write16(a, v);
  record_effect(a, 2, v, true);
}
inline void rec_write32(MMU& mmu, uint32_t a, uint32_t v) {
  mmu.write32(a, v);
  record_effect(a, 4, v, true);
}
inline void rec_write64(MMU& mmu, uint32_t a, uint64_t v) {
  mmu.write64(a, v);
  record_effect(a, 8, v, true);
}
inline void rec_write_f32(MMU& mmu, uint32_t a, float v) {
  mmu.write_f32(a, v);
  uint32_t bits;
  std::memcpy(&bits, &v, 4);
  record_effect(a, 4, bits, true);
}
inline void rec_write_f64(MMU& mmu, uint32_t a, double v) {
  mmu.write_f64(a, v);
  uint64_t bits;
  std::memcpy(&bits, &v, 8);
  record_effect(a, 8, bits, true);
}
inline uint8_t rec_read8(MMU& mmu, uint32_t a) {
  uint8_t v = mmu.read8(a);
  record_effect(a, 1, v, false);
  return v;
}
inline uint16_t rec_read16(MMU& mmu, uint32_t a) {
  uint16_t v = mmu.read16(a);
  record_effect(a, 2, v, false);
  return v;
}
inline uint32_t rec_read32(MMU& mmu, uint32_t a) {
  uint32_t v = mmu.read32(a);
  record_effect(a, 4, v, false);
  return v;
}
inline uint64_t rec_read64(MMU& mmu, uint32_t a) {
  uint64_t v = mmu.read64(a);
  record_effect(a, 8, v, false);
  return v;
}
inline float rec_read_f32(MMU& mmu, uint32_t a) {
  float v = mmu.read_f32(a);
  uint32_t bits;
  std::memcpy(&bits, &v, 4);
  record_effect(a, 4, bits, false);
  return v;
}
inline double rec_read_f64(MMU& mmu, uint32_t a) {
  double v = mmu.read_f64(a);
  uint64_t bits;
  std::memcpy(&bits, &v, 8);
  record_effect(a, 8, bits, false);
  return v;
}

// A paired-single store. The guest's GQR state drives the conversion, so the
// store goes through the CPUContext; the effect is visible in the guest RAM
// and in the port log.
inline void rec_psq_store(CPUContext& ctx, uint32_t reg, uint32_t address, int w, int i) {
  ctx.psq_store(reg, address, w, i);
}

// ---------------------------------------------------------------------------
// Register snapshot
// ---------------------------------------------------------------------------
struct State {
  uint32_t gpr[32];
  double fpr[32];
  double ps1[32];
  bool cr_lt[8], cr_gt[8], cr_eq[8], cr_so[8];
  uint32_t lr, ctr, xer, pc, msr, fpscr;
  uint64_t inst_count;
};

inline State capture(CPUContext& ctx) {
  State s{};
  for (int i = 0; i < 32; ++i) {
    s.gpr[i] = ctx.gpr[i];
    s.fpr[i] = ctx.fpr[i];
    s.ps1[i] = ctx.ps1[i];
  }
  for (int i = 0; i < 8; ++i) {
    s.cr_lt[i] = ctx.cr[i].lt;
    s.cr_gt[i] = ctx.cr[i].gt;
    s.cr_eq[i] = ctx.cr[i].eq;
    s.cr_so[i] = ctx.cr[i].so;
  }
  s.lr = ctx.lr; s.ctr = ctx.ctr; s.xer = ctx.xer; s.pc = ctx.pc;
  s.msr = ctx.msr; s.fpscr = ctx.fpscr; s.inst_count = ctx.inst_count;
  return s;
}

// Registers the native path is allowed to leave different. The PowerPC EABI
// makes r0-r3, f0-f13 and CR0 volatile across a call, so a conforming caller
// cannot observe them; the qualification proves that statically.
struct VolatileSet {
  bool gpr[32] = {false};
  bool fpr[32] = {false};
  bool cr[8] = {false};
  bool xer = false;
  void allow_gpr(int i) { if (i >= 0 && i < 32) gpr[i] = true; }
  void allow_fpr(int i) { if (i >= 0 && i < 32) fpr[i] = true; }
  void allow_cr(int i) { if (i >= 0 && i < 8) cr[i] = true; }
};

struct Difference {
  bool any = false;
  std::string text;
  void note(const std::string& s) {
    any = true;
    if (text.size() < 4000) text += s + "\n";
  }
};

inline bool same_bits(double a, double b) {
  uint64_t x, y;
  std::memcpy(&x, &a, 8);
  std::memcpy(&y, &b, 8);
  return x == y;
}

inline Difference compare(const State& a, const State& b, const VolatileSet& vol) {
  Difference d;
  for (int i = 0; i < 32; ++i) {
    if (!vol.gpr[i] && a.gpr[i] != b.gpr[i]) {
      char buf[160];
      std::snprintf(buf, sizeof(buf), "gpr[%d]: translated=%08X native=%08X", i, a.gpr[i], b.gpr[i]);
      d.note(buf);
    }
    if (!vol.fpr[i] && !same_bits(a.fpr[i], b.fpr[i])) {
      char buf[160];
      std::snprintf(buf, sizeof(buf), "fpr[%d]: translated=%.17g native=%.17g", i, a.fpr[i], b.fpr[i]);
      d.note(buf);
    }
    if (!vol.fpr[i] && !same_bits(a.ps1[i], b.ps1[i])) {
      char buf[160];
      std::snprintf(buf, sizeof(buf), "ps1[%d]: translated=%.17g native=%.17g", i, a.ps1[i], b.ps1[i]);
      d.note(buf);
    }
  }
  for (int i = 0; i < 8; ++i) {
    if (vol.cr[i]) continue;
    if (a.cr_lt[i] != b.cr_lt[i] || a.cr_gt[i] != b.cr_gt[i] ||
        a.cr_eq[i] != b.cr_eq[i] || a.cr_so[i] != b.cr_so[i]) {
      char buf[160];
      std::snprintf(buf, sizeof(buf), "cr[%d] differs", i);
      d.note(buf);
    }
  }
  if (a.lr != b.lr) d.note("lr differs");
  if (a.ctr != b.ctr) d.note("ctr differs");
  if (!vol.xer && a.xer != b.xer) {
    char buf[160];
    std::snprintf(buf, sizeof(buf), "xer: translated=%08X native=%08X", a.xer, b.xer);
    d.note(buf);
  }
  if (a.pc != b.pc) d.note("pc differs");
  if (a.msr != b.msr) d.note("msr differs");
  if (a.fpscr != b.fpscr) d.note("fpscr differs");
  if (a.inst_count != b.inst_count) {
    char buf[160];
    std::snprintf(buf, sizeof(buf), "inst_count: translated=%llu native=%llu",
                  (unsigned long long)a.inst_count, (unsigned long long)b.inst_count);
    d.note(buf);
  }
  return d;
}

// A guest RAM range, in guest physical addresses.
struct Region {
  uint32_t lo, hi;
};

inline const uint8_t* ram_bytes(MMU& mmu, uint32_t paddr) {
  if (paddr < MMU::kMem1Limit) return mmu.mem1.data() + paddr;
  if (paddr - 0x10000000u < MMU::kMem2Limit) return mmu.mem2.data() + (paddr - 0x10000000u);
  return nullptr;
}

// Compare the guest RAM in the given physical ranges.
inline Difference compare_ram(MMU& a, MMU& b, const std::vector<Region>& regions) {
  Difference d;
  size_t shown = 0;
  for (const Region& region : regions) {
    for (uint32_t p = region.lo; p < region.hi && shown < 16; ++p) {
      const uint8_t* x = ram_bytes(a, p);
      const uint8_t* y = ram_bytes(b, p);
      if (!x || !y) continue;
      if (*x == *y) continue;
      char buf[160];
      std::snprintf(buf, sizeof(buf), "ram[%08X]: translated=%02X native=%02X", p, *x, *y);
      d.note(buf);
      ++shown;
    }
  }
  return d;
}

// The RAM ranges the two paths touched, widened so an off-by-one write is
// still caught.
inline std::vector<Region> touched_regions(const std::vector<Effect>& a,
                                            const std::vector<Effect>& b) {
  std::vector<Region> regions;
  const std::vector<Effect>* lists[2] = {&a, &b};
  for (const std::vector<Effect>* list : lists) {
    for (const Effect& e : *list) {
      const uint32_t p = e.address & 0x3fffffffu;
      if (p >= MMU::kMem1Limit && p - 0x10000000u >= MMU::kMem2Limit) continue;
      uint32_t lo = p > 16 ? p - 16 : 0;
      uint32_t hi = p + e.width + 16;
      regions.push_back(Region{lo, hi});
    }
  }
  return regions;
}

inline Difference compare_effects(const std::vector<Effect>& a, const std::vector<Effect>& b,
                                   const char* label) {
  Difference d;
  if (a.size() != b.size()) {
    char buf[160];
    std::snprintf(buf, sizeof(buf), "%s count: translated=%zu native=%zu", label, a.size(), b.size());
    d.note(buf);
  }
  size_t n = a.size() < b.size() ? a.size() : b.size();
  size_t shown = 0;
  for (size_t i = 0; i < n && shown < 12; ++i) {
    if (a[i] == b[i]) continue;
    char buf[200];
    std::snprintf(buf, sizeof(buf),
                  "%s[%zu]: translated {addr=%08X w=%d v=%llX} native {addr=%08X w=%d v=%llX}",
                  label, i, a[i].address, a[i].width, (unsigned long long)a[i].value,
                  b[i].address, b[i].width, (unsigned long long)b[i].value);
    d.note(buf);
    ++shown;
  }
  return d;
}

// ---------------------------------------------------------------------------
// Result reporting
// ---------------------------------------------------------------------------
inline int g_failures = 0;
inline int g_cases = 0;

inline void report(bool ok, const std::string& name, const Difference& d) {
  ++g_cases;
  if (ok) return;
  ++g_failures;
  std::printf("FAIL %s\n%s", name.c_str(), d.text.c_str());
}

inline int summary(const char* label) {
  std::printf("%s %d/%d\n", label, g_cases - g_failures, g_cases);
  return g_failures == 0 ? 0 : 1;
}

}  // namespace nwii::runtime::native_lower_test

namespace nwii::runtime::native_lower::native_lower_detail {
// The support header calls this when the test build records memory effects.
void trace(uint32_t address, int width, uint64_t value, bool is_write) {
  native_lower_test::record_effect(address, width, value, is_write);
}
}  // namespace nwii::runtime::native_lower::native_lower_detail
