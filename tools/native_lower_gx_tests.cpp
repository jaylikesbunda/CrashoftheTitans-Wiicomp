// NATIVE_SOURCE_LOWERING_V1
//
// Differential test for the source-backed GX native lowerings
// (native-port/runtime-overlays/native_lower_gx.h).
//
// The test runs the *translated* guest function (extracted from the generated
// chunk by the coordinator's driver into guest_lowering_extracted.h) and the
// *native* lowering on identical guest states and compares
//
//   * the ordered guest memory accesses (the harness effect log),
//   * the ordered writes to the GX hardware ports (the harness port log),
//   * the guest RAM bytes in the ranges the two paths touched,
//   * the final guest register file, CR, LR, CTR, XER, PC and the retired
//     instruction count.
//
// The volatile registers the native path does not reproduce (r0-r13, f0-f13
// and CR0, plus CR6/CR7 which the SDK float helper clobbers) are declared in
// the harness' VolatileSet; tools/check_native_lowering.py proves statically
// that no call site reads them.
//
// The guest's dead stack spills are not reproduced by the native path (it has
// no frame), so the callee's stack window is excluded from the compared RAM
// ranges.

#include "runtime/cpu_context.h"
#include "runtime/native_guest_fast.h"
#include "runtime/native_fastmem.h"
#include "native_lowering_harness.h"
using nwii::runtime::CPUContext;
// The bounded blocks the generated guest code calls (the same overlay the
// shipped runtime uses, so the test exercises the shipped code path). The
// driver writes guest_lowering_extracted.h with the overlay's include, so
// the overlay is pulled in exactly once, from the generated tree's own path.
NWII_GUEST_ABI void func_80393518(NWII_CTX_PARAM ctx);
#include "guest_lowering_extracted.h"
#include "native_lower_support.h"
#include "native_lower_gx.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
#include <memory>
#include <random>
#include <vector>

namespace nl = nwii::runtime::native_lower_test;
namespace nlx = nwii::runtime::native_lower;

// ---------------------------------------------------------------------------
// The runtime services the generated guest code needs.
// ---------------------------------------------------------------------------

static bool g_interpreter_used = false;

namespace nwii::runtime {

void GX_WGPIPE_Write8(uint8_t v) { nl::record_port(0xCC008000u, 1, v); }
void GX_WGPIPE_Write16(uint16_t v) { nl::record_port(0xCC008000u, 2, v); }
void GX_WGPIPE_Write32(uint32_t v) { nl::record_port(0xCC008000u, 4, v); }
void GX_WGPIPE_WriteF32(float v) {
  uint32_t b;
  std::memcpy(&b, &v, 4);
  nl::record_port(0xCC008000u, 4, b);
}
void GX_WGPIPE_WriteF64(double v) {
  uint64_t b;
  std::memcpy(&b, &v, 8);
  nl::record_port(0xCC008000u, 8, b);
}
void GX_DL_Snapshot(uint32_t, uint32_t) {}
bool GX_DL_Fetch(uint32_t, uint32_t, std::vector<uint8_t>&) { return false; }
bool native_gx_emitter_ready() { return false; }
uint32_t HW_Reg_Read32(uint32_t) { return 0; }
uint16_t HW_Reg_Read16(uint32_t) { return 0; }
void HW_Reg_Write16(uint32_t, uint16_t) {}
void HW_Reg_Write32(uint32_t, uint32_t) {}
bool g_native_fifo_ok = false;
int32_t g_native_poll_credit = 1 << 30;
// When set, the guest's callback handling asks for a context switch, exactly
// like a pending callback that wants the thread to yield.
bool g_callback_requests_switch = false;
bool g_callback_escape = false;
struct CallbackEscape {};
bool process_pending_callbacks(CPUContext&) {
  // An exception models the production longjmp without depending on the
  // host's setjmp ABI: neither path returns through the native helper.
  if (g_callback_escape) throw CallbackEscape{};
  return g_callback_requests_switch;
}
void watch_hit(uint32_t, uint32_t, int) {}
uint32_t g_watch_addr = 0;
bool g_trace_calls = false;
void trace_call(uint32_t, CPUContext&) {}
void interpret_step(CPUContext&) { g_interpreter_used = true; }

// The only indirect calls the lowerings under test make are the switch tables
// of GXSetVtxAttrFmt and GXSetTexCoordGen2; their targets are interior entries
// of those functions.  The generated tree dispatches them through the same
// overlay, so the test mirrors it.
void native_dispatch_nested(CPUContext& ctx) {
  switch (ctx.pc) {
    case 0x803C9784: case 0x803C979C: case 0x803C97D8: case 0x803C97EC:
    case 0x803C9800: case 0x803C9818: case 0x803C9830: case 0x803C9848:
    case 0x803C9860: case 0x803C9880: case 0x803C9898: case 0x803C98B0:
    case 0x803C98C4:
      func_803C9754(ctx);
      return;
    case 0x803C9BB4: case 0x803C9BC0: case 0x803C9BCC: case 0x803C9BD8:
    case 0x803C9BE4: case 0x803C9BEC: case 0x803C9BF4: case 0x803C9BFC:
    case 0x803C9C04: case 0x803C9C0C: case 0x803C9C14: case 0x803C9C1C:
    case 0x803C9C24: case 0x803C9C2C: case 0x803C9C30:
    case 0x803C9D24: case 0x803C9D34: case 0x803C9D44: case 0x803C9D54:
    case 0x803C9D64: case 0x803C9D74: case 0x803C9D84: case 0x803C9D94:
      func_803C9B88(ctx);
      return;
    default:
      g_interpreter_used = true;
      return;
  }
}

}  // namespace nwii::runtime

// ---------------------------------------------------------------------------
// The SDK float helper (func_80393518) that GXSetFog calls.
//
// It is *not* provided by the test: the driver extracts the translated guest
// body of the helper from the generated tree, so the oracle is the guest's
// own helper (including the instructions it retires, which are part of the
// count the guest sees).
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// The guest fixture
// ---------------------------------------------------------------------------

// The guest's real small data area base (main.dol) and the objects the test
// places in guest RAM.
static constexpr uint32_t kSda = 0x8059FDC0u;
static constexpr uint32_t kState = 0x80600000u;
static constexpr uint32_t kLightObj = 0x80610000u;
static constexpr uint32_t kMatrix = 0x80620000u;
static constexpr uint32_t kFogTable = 0x80630000u;
static constexpr uint32_t kColor = 0x80640000u;
static constexpr uint32_t kStack = 0x8100F000u;
static constexpr uint32_t kStackPhys = kStack & 0x3FFFFFFFu;
static constexpr uint32_t kJumpTable = 0x80576578u;
static constexpr uint32_t kFloatTable = 0x80484950u;

static void put16(CPUContext& ctx, uint32_t a, uint16_t v) {
  uint8_t* p = ctx.mmu.mem1.data() + (a & 0x3FFFFFFFu);
  p[0] = static_cast<uint8_t>(v >> 8);
  p[1] = static_cast<uint8_t>(v);
}
static void put32(CPUContext& ctx, uint32_t a, uint32_t v) {
  uint8_t* p = ctx.mmu.mem1.data() + (a & 0x3FFFFFFFu);
  p[0] = static_cast<uint8_t>(v >> 24);
  p[1] = static_cast<uint8_t>(v >> 16);
  p[2] = static_cast<uint8_t>(v >> 8);
  p[3] = static_cast<uint8_t>(v);
}
static void putf32(CPUContext& ctx, uint32_t a, float v) {
  uint32_t b;
  std::memcpy(&b, &v, 4);
  put32(ctx, a, b);
}
static void putf64(CPUContext& ctx, uint32_t a, double v) {
  uint64_t b;
  std::memcpy(&b, &v, 8);
  put32(ctx, a, static_cast<uint32_t>(b >> 32));
  put32(ctx, a + 4, static_cast<uint32_t>(b));
}

// A deterministic pattern over a range, so the two contexts hold the same
// bytes in the regions the comparison looks at.
static void put_pattern(CPUContext& ctx, uint32_t a, uint32_t bytes, uint32_t seed) {
  for (uint32_t i = 0; i < bytes; i += 4) {
    put32(ctx, a + i, seed * 2654435761u + i);
  }
}

// The guest's own constants: the SDA floats GXSetFog reads, the float helper's
// table and GXSetVtxAttrFmt's switch table.
static void write_constants(CPUContext& ctx) {
  // The neighbourhoods of the touched addresses, so the two contexts agree.
  // (The patterns are written before the constants, so they do not clobber
  // them.)
  put_pattern(ctx, kSda - 14480, 160, 1);
  put_pattern(ctx, kFloatTable - 32, 96, 2);
  put_pattern(ctx, kJumpTable - 32, 128, 3);
  put_pattern(ctx, kColor - 32, 96, 4);

  putf32(ctx, kSda - 14448, 0.0f);
  putf32(ctx, kSda - 14444, 1.0f);
  putf32(ctx, kSda - 14440, 0.5f);
  putf32(ctx, kSda - 14424, 2.0f);
  putf32(ctx, kSda - 14408, 8388638.0f);
  putf64(ctx, kSda - 14432, 1.0);
  putf64(ctx, kSda - 14416, 0.5);
  putf64(ctx, kSda - 14400, 4503601774854144.0);
  put32(ctx, kSda - 14560, kState);

  putf64(ctx, kFloatTable + 0, 0.0);
  putf64(ctx, kFloatTable + 8, 4294967296.0);
  putf64(ctx, kFloatTable + 16, 2147483648.0);

  // GXSetVtxAttrFmt's switch table (orig/main.dol, 0x80576578).
  static const uint32_t kTable[17] = {
      0x803C9784u, 0x803C979Cu, 0x803C97D8u, 0x803C97ECu, 0x803C9800u,
      0x803C9818u, 0x803C9830u, 0x803C9848u, 0x803C9860u, 0x803C9880u,
      0x803C9898u, 0x803C98B0u, 0x803C98C4u, 0x803C98C4u, 0x803C98C4u,
      0x803C98C4u, 0x803C979Cu};
  for (uint32_t i = 0; i < 17u; ++i) put32(ctx, kJumpTable + 4u * i, kTable[i]);

}

// ---------------------------------------------------------------------------
// The comparison
// ---------------------------------------------------------------------------

struct Outcome {
  nl::State state;
  std::vector<nl::Effect> effects;
  std::vector<nl::Effect> ports;
};

template <class F>
static void run_guest(CPUContext& ctx, F fn, Outcome& o) {
  nl::g_effects.clear();
  nl::g_ports.clear();
  g_interpreter_used = false;
  nl::g_recording = true;
  fn(ctx);
  nl::g_recording = false;
  o.state = nl::capture(ctx);
  o.effects = nl::g_effects.entries;
  o.ports = nl::g_ports.entries;
}

static int g_cases = 0;
static int g_failures = 0;
static int g_group_failures = 0;

// The registers the GXSetFog qualification cares about: every register the
// translated body leaves modified.  The harness' VolatileSet allows the EABI
// volatile registers to differ, so the fog group compares them explicitly.
static nl::Difference compare_modified_registers(const nl::State& a, const nl::State& b) {
  nl::Difference d;
  char buf[200];
  for (int i = 0; i < 32; ++i) {
    if (a.gpr[i] != b.gpr[i]) {
      std::snprintf(buf, sizeof(buf), "gpr[%d]: translated=%08X native=%08X", i, a.gpr[i], b.gpr[i]);
      d.note(buf);
    }
    if (!nl::same_bits(a.fpr[i], b.fpr[i])) {
      std::snprintf(buf, sizeof(buf), "fpr[%d]: translated=%.17g native=%.17g", i, a.fpr[i], b.fpr[i]);
      d.note(buf);
    }
  }
  for (int i = 0; i < 8; ++i) {
    if (a.cr_lt[i] != b.cr_lt[i] || a.cr_gt[i] != b.cr_gt[i] ||
        a.cr_eq[i] != b.cr_eq[i] || a.cr_so[i] != b.cr_so[i]) {
      std::snprintf(buf, sizeof(buf), "cr[%d] differs", i);
      d.note(buf);
    }
  }
  if (!nl::same_bits(a.ps1[31], b.ps1[31])) {
    std::snprintf(buf, sizeof(buf), "ps1[31]: translated=%.17g native=%.17g", a.ps1[31], b.ps1[31]);
    d.note(buf);
  }
  return d;
}

// The strictest comparison (strict == kStrictAll) is the harness' own
// `compare` with an empty VolatileSet, so every GPR, FPR, paired-single
// mirror, CR field, LR/CTR/XER and PC is compared bit for bit.

// 0: the EABI volatile set may differ; 1: also compare the GXSetFog modified
// set explicitly; 2: compare every register (no allowances at all).
enum StrictMode { kStrictNone = 0, kStrictModified = 1, kStrictAll = 2 };

static void check(const char* name, int index, CPUContext& a, CPUContext& b, const Outcome& t,
                  const Outcome& n, int strict = kStrictNone) {
  ++g_cases;
  nl::VolatileSet vol;
  if (strict != kStrictAll) {
    for (int i = 0; i < 14; ++i) vol.allow_gpr(i);
    for (int i = 0; i < 14; ++i) vol.allow_fpr(i);
    vol.allow_cr(0);
    vol.allow_cr(6);
    vol.allow_cr(7);
  }

  nl::Difference d = nl::compare(t.state, n.state, vol);
  // The ordered guest *writes* are the observable effects; the reads of the
  // two paths happen in a different order (the guest reads the state pointer
  // where its own code does, the lowering reads it where it needs it), and
  // the final RAM comparison covers them.
  std::vector<nl::Effect> tw, nw;
  for (const nl::Effect& e : t.effects) {
    if (!e.is_write) continue;
    if ((e.address & 0x3FFFFFFFu) >= kStackPhys - 512u && (e.address & 0x3FFFFFFFu) < kStackPhys + 512u) continue;  // the guest's dead stack spills
    tw.push_back(e);
  }
  for (const nl::Effect& e : n.effects) {
    if (!e.is_write) continue;
    if ((e.address & 0x3FFFFFFFu) >= kStackPhys - 512u && (e.address & 0x3FFFFFFFu) < kStackPhys + 512u) continue;
    nw.push_back(e);
  }
  nl::Difference e = nl::compare_effects(tw, nw, "effect");
  if (e.any) d.note(e.text);
  nl::Difference p = nl::compare_effects(t.ports, n.ports, "port");
  if (p.any) d.note(p.text);

  // The guest RAM in the ranges the two paths touched, without the callee's
  // stack window (the native path has no frame).
  std::vector<nl::Region> regions = nl::touched_regions(t.effects, n.effects);
  std::vector<nl::Region> ram;
  for (const nl::Region& r : regions) {
    if (r.hi > kStackPhys - 512u && r.lo < kStackPhys + 512u) continue;
    ram.push_back(r);
  }
  nl::Difference m = nl::compare_ram(a.mmu, b.mmu, ram);
  if (m.any) d.note(m.text);

  if (strict == kStrictModified) {
    // Every register the translated body leaves modified has to match.
    nl::Difference r = compare_modified_registers(t.state, n.state);
    if (r.any) d.note(r.text);
  }

  if (g_interpreter_used) d.note("the guest asked the interpreter to run an instruction");
  if (d.any) {
    ++g_failures;
    ++g_group_failures;
    if (g_failures <= 400) std::printf("FAIL %s case %d\n%s", name, index, d.text.c_str());
  }
}

// ---------------------------------------------------------------------------
// The guest register file the lowerings see
// ---------------------------------------------------------------------------

static void reset_registers(CPUContext& ctx) {
  for (int i = 0; i < 32; ++i) {
    ctx.gpr[i] = 0;
    ctx.fpr[i] = 0.0;
    ctx.ps1[i] = 0.0;
  }
  for (int i = 0; i < 8; ++i) {
    ctx.cr[i].lt = ctx.cr[i].gt = ctx.cr[i].eq = ctx.cr[i].so = false;
  }
  // A non-trivial cr6/cr7: the GX float helper writes them, and a path that
  // does not call the helper must leave them alone.
  ctx.cr[6].lt = true;
  ctx.cr[7].gt = true;
  ctx.lr = 0x80001234u;
  ctx.ctr = 0;
  ctx.xer = 0;
  ctx.pc = 0;
  ctx.msr = 0x2000u;  // the FPU is enabled
  ctx.fpscr = 0;
  ctx.inst_count = 0;
  ctx.gpr[1] = kStack;
  ctx.gpr[2] = kSda;
  ctx.ps1[31] = 1.0;
}

// The arguments of the entry under test.
static uint32_t g_arg[8];

// ---------------------------------------------------------------------------
// The test groups
// ---------------------------------------------------------------------------

// Set when the native path requests translated execution. The translated
// body owns the resulting PC, which may belong to a scheduler callback.
static bool g_native_preempted = false;

// One case: the translated path in `a`, the native path in `b`.
template <class F, class G>
static void run_case(const char* name, int index, CPUContext& a, CPUContext& b, F translated,
                     G native_run, int strict = kStrictNone) {
  Outcome t, n;
  run_guest(a, translated, t);
  nl::g_effects.clear();
  nl::g_ports.clear();
  g_interpreter_used = false;
  nl::g_recording = true;
  g_native_preempted = false;
  native_run(b);
  nl::g_recording = false;
  // The staged entry publishes the return PC.  A native path that was
  // preempted by the guest's scheduler leaves its own PC (the poll point)
  // behind, and that PC must survive.
  if (!g_native_preempted) b.pc = b.lr;
  n.state = nl::capture(b);
  n.effects = nl::g_effects.entries;
  n.ports = nl::g_ports.entries;
  check(name, index, a, b, t, n, strict);
}

int main() {
  std::mt19937 rng(0x5eed1234u);
  const int iterations = 24;
  std::unique_ptr<CPUContext> a(new CPUContext());
  std::unique_ptr<CPUContext> b(new CPUContext());
  write_constants(*a);
  write_constants(*b);

  // Compare the complete state visible to an escaping callback, including
  // the stack frame. No register is volatile across an asynchronous poll.
  {
    struct EscapeCase { int credit; double nearv, farv; };
    const EscapeCase escape_cases[] = {
      {1, 1.0, 1.00001}, {2, 1.0, 1.00001},
      {4, 1.0, 1.00001}, {8, 1.0, 1.00001},
      {1, -9.0, 1.0}, {2, -9.0, 1.0},
      {3, -9.0, 1.0}, {4, -9.0, 1.0},
    };
    for (const auto& ec : escape_cases) {
      const int credit = ec.credit;
      for (CPUContext* c : {a.get(), b.get()}) {
        reset_registers(*c);
        c->gpr[3] = 1;
        c->gpr[4] = kColor;
        c->fpr[1] = 1.0;
        c->fpr[2] = 100.0;
        c->fpr[3] = ec.nearv;
        c->fpr[4] = ec.farv;
        c->pc = 0x803CD2A0u;
        for (uint32_t p = kStack - 64; p < kStack + 8; p += 4)
          put32(*c, p, 0xdeadbeefu);
      }
      g_callback_escape = true;
      g_native_poll_credit = credit;
      try { func_803CD2A0(*a); } catch (const CallbackEscape&) {}
      g_native_poll_credit = credit;
      try {
        if (nlx::gx_set_fog(nlx::guest_mem(*b), b->inst_count,
                            b->gpr[2], b->gpr[3], b->gpr[4],
                            b->fpr[1], b->fpr[2], b->fpr[3], b->fpr[4], *b))
          func_803CD2A0(*b);
      } catch (const CallbackEscape&) {}
      g_callback_escape = false;
      auto d = nl::compare(nl::capture(*a), nl::capture(*b), nl::VolatileSet{});
      for (uint32_t p = kStack - 64; p < kStack + 8; p += 4)
        if (a->mmu.read32(p) != b->mmu.read32(p)) d.note("callback stack frame differs");
      ++g_cases;
      if (d.any) { ++g_failures; std::printf("FAIL fog_callback_escape credit %d\n%s", credit, d.text.c_str()); }
    }
    g_native_poll_credit = 1 << 30;
  }

  // --- GXClearVtxDesc ---------------------------------------------------
  {
    const int before = g_cases;
    g_group_failures = 0;
    for (int i = 0; i < iterations; ++i) {
      reset_registers(*a);
      reset_registers(*b);
      put_pattern(*a, kState, 0x600, 0x51u + static_cast<uint32_t>(i));
      put_pattern(*b, kState, 0x600, 0x51u + static_cast<uint32_t>(i));
      a->pc = 0x803C9720u;
      b->pc = 0x803C9720u;
      run_case("gx_clear_vtx_desc", i, *a, *b, func_803C9720,
               [](CPUContext& ctx) {
                 nlx::gx_clear_vtx_desc(nlx::guest_mem(ctx), ctx.inst_count, ctx.gpr[2]);
               });
    }
    std::printf("NATIVE_LOWERING_TESTS gx_clear_vtx_desc %d/%d\n", g_cases - g_group_failures - before,
                g_cases - before);
  }

  // --- GXSetVtxAttrFmt --------------------------------------------------
  {
    const int before = g_cases;
    g_group_failures = 0;
    // The tables are read-only guest data: install them in both contexts.
    for (CPUContext* c : {a.get(), b.get()}) {
      static const uint32_t kTable[17] = {
          0x803C9784u, 0x803C979Cu, 0x803C97D8u, 0x803C97ECu, 0x803C9800u,
          0x803C9818u, 0x803C9830u, 0x803C9848u, 0x803C9860u, 0x803C9880u,
          0x803C9898u, 0x803C98B0u, 0x803C98C4u, 0x803C98C4u, 0x803C98C4u,
          0x803C98C4u, 0x803C979Cu};
      for (uint32_t k = 0; k < 17u; ++k) put32(*c, kJumpTable + 4u * k, kTable[k]);
    }
    // Every attribute class (below POS, POS..NBT, above NBT), every format
    // slot, the adversarial count/type/shift values and the NRM count 2.
    const uint32_t attrs[] = {0u, 8u, 9u, 10u, 11u, 12u, 13u, 18u, 19u, 20u, 21u, 24u,
                              25u, 26u, 0xFFFFFFFFu};
    const uint32_t cnts[] = {0u, 1u, 2u, 3u, 0xFFFFFFFFu, 0x7FFFFFFFu};
    const uint32_t types[] = {0u, 1u, 2u, 7u, 0xFFFFFFFFu, 0xFFFFFFF9u};
    const uint32_t shifts[] = {0u, 1u, 31u, 0xFFu};
    const uint32_t fmts[] = {0u, 1u, 7u, 8u, 9u, 31u, 32u, 255u, 0xFFFFFFFFu};
    int index = 0;
    for (uint32_t fmt : fmts) {
      for (uint32_t attr : attrs) {
        // Every component count for the normal attributes: compCnt == 2 is
        // GX_NRM_NBT3, which reaches a counted block the paired grid never
        // visits (the retired count is one higher on that path).
        const uint32_t count = (attr == 10u || attr == 25u)
                                   ? static_cast<uint32_t>(sizeof(cnts) / sizeof(cnts[0]))
                                   : 1u;
        for (uint32_t ci = 0; ci < count; ++ci) {
        const uint32_t cnt = (attr == 10u || attr == 25u)
                                 ? cnts[ci]
                                 : cnts[index % (sizeof(cnts) / sizeof(cnts[0]))];
        const uint32_t type = types[index % (sizeof(types) / sizeof(types[0]))];
        const uint32_t shift = shifts[index % (sizeof(shifts) / sizeof(shifts[0]))];
        ++index;
        reset_registers(*a);
        reset_registers(*b);
        put_pattern(*a, kState, 0x600, 0x61u + static_cast<uint32_t>(index));
        put_pattern(*b, kState, 0x600, 0x61u + static_cast<uint32_t>(index));
        for (CPUContext* c : {a.get(), b.get()}) {
          c->gpr[3] = fmt;
          c->gpr[4] = attr;
          c->gpr[5] = cnt;
          c->gpr[6] = type;
          c->gpr[7] = shift;
          c->gpr[8] = 0x88888888u;
          c->gpr[9] = 0x99999999u;
          c->ctr = 0xDEADBEEFu;
          c->pc = 0x803C9754u;
        }
        run_case("gx_set_vtx_attr_fmt", index, *a, *b, func_803C9754,
                 [](CPUContext& ctx) { nlx::apply_gx_set_vtx_attr_fmt(ctx); }, kStrictAll);
        }
      }
    }
    std::printf("NATIVE_LOWERING_TESTS apply_gx_set_vtx_attr_fmt %d/%d\n",
                g_cases - g_group_failures - before, g_cases - before);
  }

  // --- GXSetArray -------------------------------------------------------
  {
    const int before = g_cases;
    g_group_failures = 0;
    for (int i = 0; i < iterations; ++i) {
      const uint32_t attr = (i % 2 == 0) ? 9u + static_cast<uint32_t>(i % 12) : 25u;
      const uint32_t base = 0x80000000u | (rng() & 0x0FFFFFFFu);
      const uint32_t stride = rng() & 0xFFu;
      reset_registers(*a);
      reset_registers(*b);
      for (CPUContext* c : {a.get(), b.get()}) {
        c->gpr[3] = attr;
        c->gpr[4] = base;
        c->gpr[5] = stride;
        c->pc = 0x803C9B38u;
      }
      g_arg[0] = attr;
      g_arg[1] = base;
      g_arg[2] = stride;
      run_case("gx_set_array", i, *a, *b, func_803C9B38,
               [](CPUContext& ctx) {
                 nlx::gx_set_array(nlx::guest_mem(ctx), ctx.inst_count, g_arg[0], g_arg[1], g_arg[2]);
               });
    }
    std::printf("NATIVE_LOWERING_TESTS gx_set_array %d/%d\n", g_cases - g_group_failures - before,
                g_cases - before);
  }

  // --- GXSetZMode -------------------------------------------------------
  {
    const int before = g_cases;
    g_group_failures = 0;
    for (int i = 0; i < iterations; ++i) {
      const uint32_t test_en = rng() & 1u;
      const uint32_t func = rng() & 7u;
      const uint32_t upd = rng() & 1u;
      reset_registers(*a);
      reset_registers(*b);
      put_pattern(*a, kState, 0x600, 0x71u + static_cast<uint32_t>(i));
      put_pattern(*b, kState, 0x600, 0x71u + static_cast<uint32_t>(i));
      for (CPUContext* c : {a.get(), b.get()}) {
        c->gpr[3] = test_en;
        c->gpr[4] = func;
        c->gpr[5] = upd;
        c->pc = 0x803CD698u;
      }
      g_arg[0] = test_en;
      g_arg[1] = func;
      g_arg[2] = upd;
      run_case("gx_set_z_mode", i, *a, *b, func_803CD698,
               [](CPUContext& ctx) {
                 nlx::gx_set_z_mode(nlx::guest_mem(ctx), ctx.inst_count, ctx.gpr[2], g_arg[0], g_arg[1],
                                    g_arg[2]);
               });
    }
    std::printf("NATIVE_LOWERING_TESTS gx_set_z_mode %d/%d\n", g_cases - g_group_failures - before,
                g_cases - before);
  }

  // --- GXSetChanCtrl ----------------------------------------------------
  {
    const int before = g_cases;
    g_group_failures = 0;
    for (int i = 0; i < iterations; ++i) {
      const uint32_t chan = static_cast<uint32_t>(i % 8);
      const uint32_t enable = rng() & 1u;
      const uint32_t amb = rng() & 1u;
      const uint32_t mat = rng() & 1u;
      const uint32_t mask = rng() & 0xFFu;
      const uint32_t diff = rng() & 3u;
      const uint32_t attn = static_cast<uint32_t>(i % 4);
      reset_registers(*a);
      reset_registers(*b);
      put_pattern(*a, kState, 0x600, 0x81u + static_cast<uint32_t>(i));
      put_pattern(*b, kState, 0x600, 0x81u + static_cast<uint32_t>(i));
      for (CPUContext* c : {a.get(), b.get()}) {
        c->gpr[3] = chan;
        c->gpr[4] = enable;
        c->gpr[5] = amb;
        c->gpr[6] = mat;
        c->gpr[7] = mask;
        c->gpr[8] = diff;
        c->gpr[9] = attn;
        c->pc = 0x803CB7E0u;
      }
      g_arg[0] = chan;
      g_arg[1] = enable;
      g_arg[2] = amb;
      g_arg[3] = mat;
      g_arg[4] = mask;
      g_arg[5] = diff;
      g_arg[6] = attn;
      run_case("gx_set_chan_ctrl", i, *a, *b, func_803CB7E0,
               [](CPUContext& ctx) {
                 nlx::gx_set_chan_ctrl(nlx::guest_mem(ctx), ctx.inst_count, ctx.gpr[2], g_arg[0],
                                       g_arg[1], g_arg[2], g_arg[3], g_arg[4], g_arg[5], g_arg[6],
                                       ctx.xer);
               });
    }
    std::printf("NATIVE_LOWERING_TESTS gx_set_chan_ctrl %d/%d\n", g_cases - g_group_failures - before,
                g_cases - before);
  }

  // --- GXSetFogRangeAdj -------------------------------------------------
  {
    const int before = g_cases;
    g_group_failures = 0;
    const uint32_t enables[] = {0u, 1u, 2u, 0xFFFFFFFFu, 0x80000000u};
    const uint32_t centers[] = {0u, 1u, 0xFFFFu, 0x1234u, 0xFFFFFFFFu};
    int index = 0;
    for (uint32_t enable : enables) {
      for (uint32_t center : centers) {
        ++index;
        reset_registers(*a);
        reset_registers(*b);
        for (CPUContext* c : {a.get(), b.get()}) {
          // A table whose halves exercise the 12-bit masks and the high bits.
          for (uint32_t k = 0; k < 20u; ++k) {
            put16(*c, kFogTable + 2u * k,
                  static_cast<uint16_t>(0xF000u + 0x111u * k + 7u * static_cast<uint32_t>(index)));
          }
          c->gpr[3] = enable;
          c->gpr[4] = center;
          c->gpr[5] = kFogTable;
          c->gpr[6] = 0x66666666u;
          c->gpr[7] = 0x77777777u;
          c->gpr[8] = 0x88888888u;
          c->gpr[9] = 0x99999999u;
          c->gpr[10] = 0xAAAAAAAAu;
          c->gpr[11] = 0xBBBBBBBBu;
          c->gpr[12] = 0xCCCCCCCCu;
          c->pc = 0x803CD4CCu;
        }
        run_case("gx_set_fog_range_adj", index, *a, *b, func_803CD4CC,
                 [](CPUContext& ctx) { nlx::apply_gx_set_fog_range_adj(ctx); }, kStrictAll);
      }
    }
    std::printf("NATIVE_LOWERING_TESTS apply_gx_set_fog_range_adj %d/%d\n",
                g_cases - g_group_failures - before, g_cases - before);
  }

  // --- GXLoadPosMtxImm --------------------------------------------------
  {
    const int before = g_cases;
    g_group_failures = 0;
    for (int i = 0; i < iterations; ++i) {
      const uint32_t idx = static_cast<uint32_t>(i % 4);
      reset_registers(*a);
      reset_registers(*b);
      for (CPUContext* c : {a.get(), b.get()}) {
        for (uint32_t k = 0; k < 12u; ++k) {
          uint32_t bk = 0x3F800000u + k * 0x00100001u;
          putf32(*c, kMatrix + 4u * k, static_cast<float>(bk));
        }
        c->gpr[3] = kMatrix;
        c->gpr[4] = idx;
        c->pc = 0x803CDA4Cu;
      }
      g_arg[0] = kMatrix;
      g_arg[1] = idx;
      run_case("gx_load_pos_mtx_imm", i, *a, *b, func_803CDA4C,
               [](CPUContext& ctx) {
                 nlx::gx_load_pos_mtx_imm(nlx::guest_mem(ctx), ctx.inst_count, g_arg[0], g_arg[1]);
               });
    }
    std::printf("NATIVE_LOWERING_TESTS gx_load_pos_mtx_imm %d/%d\n", g_cases - g_group_failures - before,
                g_cases - before);
  }

  // --- GXLoadNrmMtxImm --------------------------------------------------
  {
    const int before = g_cases;
    g_group_failures = 0;
    for (int i = 0; i < iterations; ++i) {
      const uint32_t idx = static_cast<uint32_t>(i % 4);
      reset_registers(*a);
      reset_registers(*b);
      for (CPUContext* c : {a.get(), b.get()}) {
        for (uint32_t k = 0; k < 12u; ++k) {
          uint32_t bk = 0x3F800000u + k * 0x00100001u;
          putf32(*c, kMatrix + 4u * k, static_cast<float>(bk));
        }
        c->gpr[3] = kMatrix;
        c->gpr[4] = idx;
        c->pc = 0x803CDA9Cu;
      }
      g_arg[0] = kMatrix;
      g_arg[1] = idx;
      run_case("gx_load_nrm_mtx_imm", i, *a, *b, func_803CDA9C,
               [](CPUContext& ctx) {
                 nlx::gx_load_nrm_mtx_imm(nlx::guest_mem(ctx), ctx.inst_count, g_arg[0], g_arg[1]);
               });
    }
    std::printf("NATIVE_LOWERING_TESTS gx_load_nrm_mtx_imm %d/%d\n", g_cases - g_group_failures - before,
                g_cases - before);
  }

  // --- GXLoadLightObjImm ------------------------------------------------
  {
    const int before = g_cases;
    g_group_failures = 0;
    // Every light id (the guest's cntlzw/subfic slot arithmetic), including
    // the zero and high-bit cases, against numerator patterns that include
    // normal values, both infinities and signalling/quiet NaNs (the PSQ round
    // trip quiets a signalling NaN and the test compares the fpr/ps1 mirror).
    const uint32_t lights[] = {0u, 1u, 2u, 3u, 4u, 5u, 6u, 7u, 8u,
                               0x80000000u, 0x40000000u, 0xFFFFFFFFu};
    const uint32_t patterns[] = {
        0x3F800000u, 0xBF800000u, 0x7F800001u, 0x7FC00001u,
        0xFF800123u, 0x7F800000u, 0xFF800000u, 0x00000001u};
    int index = 0;
    for (uint32_t light : lights) {
      for (uint32_t pattern : patterns) {
        ++index;
        reset_registers(*a);
        reset_registers(*b);
        for (CPUContext* c : {a.get(), b.get()}) {
          for (uint32_t k = 0; k < 16u; ++k) {
            if (k == 3u) {
              put32(*c, kLightObj + 4u * k, 0x11223344u);  // the colour word
            } else {
              put32(*c, kLightObj + 4u * k,
                    pattern ^ (0x01010101u * k) ^ static_cast<uint32_t>(index));
            }
          }
          c->gpr[3] = kLightObj;
          c->gpr[4] = light;
          c->xer = 0xA5A5A5A5u;
          c->pc = 0x803CB590u;
        }
        run_case("gx_load_light_obj_imm", index, *a, *b, func_803CB590,
                 [](CPUContext& ctx) { nlx::apply_gx_load_light_obj_imm(ctx); }, kStrictAll);
      }
    }
    std::printf("NATIVE_LOWERING_TESTS apply_gx_load_light_obj_imm %d/%d\n",
                g_cases - g_group_failures - before, g_cases - before);
  }

  // --- GXSetTexCoordGen2 ------------------------------------------------
  {
    const int before = g_cases;
    g_group_failures = 0;
    // The two jump tables (read from orig/main.dol).  They live in guest RAM,
    // so the test installs them in both contexts.
    static const uint32_t kSrcTable[21] = {
        0x803C9BB4u, 0x803C9BC0u, 0x803C9BCCu, 0x803C9BD8u, 0x803C9BF4u,
        0x803C9BFCu, 0x803C9C04u, 0x803C9C0Cu, 0x803C9C14u, 0x803C9C1Cu,
        0x803C9C24u, 0x803C9C2Cu, 0x803C9C30u, 0x803C9C30u, 0x803C9C30u,
        0x803C9C30u, 0x803C9C30u, 0x803C9C30u, 0x803C9C30u, 0x803C9BE4u,
        0x803C9BECu};
    static const uint32_t kIdTable[7] = {
        0x803C9D24u, 0x803C9D34u, 0x803C9D44u, 0x803C9D54u,
        0x803C9D64u, 0x803C9D74u, 0x803C9D84u};
    // The input-domain guard: the wild ids/srcs are rejected by the manifest
    // guard and therefore keep the translated body.
    struct GuardCase { uint32_t id, src; bool served; };
    const GuardCase guards[] = {
        {0u, 0u, true}, {7u, 20u, true}, {7u, 21u, true}, {7u, 0x7FFFFFFFu, true},
        {8u, 0u, false}, {0xFFFFFFFFu, 0u, false}, {0u, 0xFFFFFFFFu, false},
        {0x80000000u, 0u, false}};
    for (const GuardCase& g : guards) {
      ++g_cases;
      if (nlx::gx_set_tex_coord_gen2_serves(g.id, g.src) != g.served) {
        ++g_failures;
        ++g_group_failures;
        std::printf("FAIL gx_set_tex_coord_gen2 guard id=%08X src=%08X\n", g.id, g.src);
      }
    }
    const int32_t types[] = {-3, -1, 0, 1, 2, 3, 8, 9, 10, 11, 12,
                             0x7FFFFFFF, static_cast<int32_t>(0x80000000u)};
    const uint32_t srcs[] = {0u, 1u, 2u, 3u, 4u, 5u, 6u, 7u, 8u, 9u, 10u, 11u,
                             12u, 13u, 14u, 15u, 16u, 17u, 18u, 19u, 20u, 21u, 25u,
                             0x7FFFFFFFu};
    int index = 0;
    for (uint32_t id = 0; id < 8u; ++id) {
      for (int32_t type : types) {
        for (uint32_t src : srcs) {
          ++index;
          reset_registers(*a);
          reset_registers(*b);
          for (CPUContext* c : {a.get(), b.get()}) {
            for (uint32_t k = 0; k < 21u; ++k) put32(*c, 0x8057661Cu + 4u * k, kSrcTable[k]);
            for (uint32_t k = 0; k < 7u; ++k) put32(*c, 0x80576600u + 4u * k, kIdTable[k]);
            put_pattern(*c, kState, 0x300, 0xA1u + static_cast<uint32_t>(index));
            put32(*c, kState + 1528, 0x11223344u);  // gxdt+1531 (VAT dirty byte)
            put32(*c, kState + 1532, 0xABCD1234u);  // gxDirtyFlags
            // Adversarial argument registers: every bit of texMtxIdx above the
            // six the guest uses, a normalize that is not 0/1 and a dual
            // index below GX_PTTEXMTX0.
            c->gpr[3] = id;
            c->gpr[4] = static_cast<uint32_t>(type);
            c->gpr[5] = src;
            c->gpr[6] = (index & 1) ? 0xFFFFFFFFu : (0x40u + static_cast<uint32_t>(index));
            c->gpr[7] = (index % 3 == 0) ? 0xFFFFFFFFu : static_cast<uint32_t>(index % 5);
            c->gpr[8] = (index % 4 == 0) ? 0xFFFFFFFFu : (0x40u + static_cast<uint32_t>(index));
            c->gpr[9] = 0x99999999u;
            c->gpr[10] = 0xAAAAAAAAu;
            c->gpr[11] = 0xBBBBBBBBu;
            c->gpr[12] = 0xCCCCCCCCu;
            c->ctr = 0xDEADBEEFu;
            c->pc = 0x803C9B88u;
          }
          run_case("gx_set_tex_coord_gen2", index, *a, *b, func_803C9B88,
                   [](CPUContext& ctx) { nlx::apply_gx_set_tex_coord_gen2(ctx); }, kStrictAll);
        }
      }
    }
    // A randomised pass over the whole served domain.
    for (int i = 0; i < 200; ++i) {
      const uint32_t id = rng() % 8u;
      const uint32_t type = rng();
      const uint32_t src = rng() % 22u;
      const uint32_t tex_mtx = rng();
      const uint32_t normalize = rng();
      const uint32_t dual_mtx = rng();
      reset_registers(*a);
      reset_registers(*b);
      for (CPUContext* c : {a.get(), b.get()}) {
        for (uint32_t k = 0; k < 21u; ++k) put32(*c, 0x8057661Cu + 4u * k, kSrcTable[k]);
        for (uint32_t k = 0; k < 7u; ++k) put32(*c, 0x80576600u + 4u * k, kIdTable[k]);
        put_pattern(*c, kState, 0x300, 0xB1u + static_cast<uint32_t>(i));
        put32(*c, kState + 1528, 0x11223344u);
        put32(*c, kState + 1532, 0xABCD1234u);
        c->gpr[3] = id;
        c->gpr[4] = type;
        c->gpr[5] = src;
        c->gpr[6] = tex_mtx;
        c->gpr[7] = normalize;
        c->gpr[8] = dual_mtx;
        c->pc = 0x803C9B88u;
      }
      run_case("gx_set_tex_coord_gen2", 100000 + i, *a, *b, func_803C9B88,
               [](CPUContext& ctx) { nlx::apply_gx_set_tex_coord_gen2(ctx); }, kStrictAll);
    }
    std::printf("NATIVE_LOWERING_TESTS apply_gx_set_tex_coord_gen2 %d/%d\n",
                g_cases - g_group_failures - before, g_cases - before);
  }

  // --- GXSetFog ---------------------------------------------------------
  {
    const int before = g_cases;
    g_group_failures = 0;
    struct FogCase {
      uint32_t type;
      double start, end, nearv, farv;
      bool poll_due;        // the guest's poll credit is exhausted
      bool switch_request;  // the pending callback asks for a context switch
      double ps1_31;        // the value of ps1[31] the guest round trips
    };
    const float inf = std::numeric_limits<float>::infinity();
    const float nan = std::numeric_limits<float>::quiet_NaN();
    const FogCase cases[] = {
      // the ordinary perspective case
      {1u, 1.0, 100.0, 0.0, 1.0, false, false, 1.0},
      // the degenerate fog: end == start and far == near
      {1u, 5.0, 5.0, 1.0, 100.0, false, false, 1.0},
      {1u, 0.0, 1.0, 2.0, 2.0, false, false, 1.0},
      // the orthographic branch (bit 3 of the type set)
      {9u, 1.0, 100.0, 0.0, 1.0, false, false, 1.0},
      {9u, 5.0, 5.0, 1.0, 100.0, false, false, 1.0},
      {9u, 0.0, 1.0, 2.0, 2.0, false, false, 1.0},
      // NaN in every argument
      {1u, nan, 100.0, 0.0, 1.0, false, false, 1.0},
      {1u, 1.0, nan, 0.0, 1.0, false, false, 1.0},
      {1u, 1.0, 100.0, nan, 1.0, false, false, 1.0},
      {1u, 1.0, 100.0, 0.0, nan, false, false, 1.0},
      {9u, nan, 100.0, 0.0, 1.0, false, false, 1.0},
      {9u, 1.0, 100.0, 0.0, nan, false, false, 1.0},
      // infinity in every argument
      {1u, 1.0, 100.0, 0.0, inf, false, false, 1.0},
      {1u, 1.0, 100.0, 0.0, -inf, false, false, 1.0},
      {1u, 1.0, 100.0, inf, 1.0, false, false, 1.0},
      {1u, 1.0, inf, 0.0, 1.0, false, false, 1.0},
      {9u, 1.0, 100.0, 0.0, inf, false, false, 1.0},
      {9u, 1.0, inf, 0.0, 1.0, false, false, 1.0},
      // the loop boundaries: f25 = far / (far - near)
      {1u, 0.0, 1.0, 0.0, 1.0, false, false, 1.0},     // f25 = 1.0 exactly
      {1u, 0.0, 1.0, -1.0, 1.0, false, false, 1.0},   // f25 = 0.5 exactly
      {1u, 0.0, 1.0, 1.0, 2.0, false, false, 1.0},     // f25 = 2.0: one halving
      {1u, 0.0, 1.0, 3.0, 4.0, false, false, 1.0},     // f25 = 4.0: two halvings
      {1u, 0.0, 1.0, -3.0, 1.0, false, false, 1.0},    // f25 = 0.25: one doubling
      {1u, 0.0, 1.0, -9.0, 1.0, false, false, 1.0},    // f25 = 0.1: three doublings
      {1u, 0.0, 1.0, 2.0, 1.0, false, false, 1.0},     // f25 = -1: the second loop stops
      // a ps1[31] that a float cannot represent: the guest's PSQ round trip
      {1u, 1.0, 100.0, 0.0, 1.0, false, false, 1.0 + 0x1p-40},
      // the poll: due without a switch, and due with a switch
      {1u, 1.0, 100.0, 0.0, 1.0, true, false, 1.0},
      {1u, 1.0, 100.0, 0.0, 1.0, true, true, 1.0},
      {1u, 1.0, 100.0, 0.0, 4.0, true, true, 1.0},
    };
    const int kFogCases = static_cast<int>(sizeof(cases) / sizeof(cases[0]));
    for (int i = 0; i < kFogCases; ++i) {
      const FogCase& fc = cases[i];
      reset_registers(*a);
      reset_registers(*b);
      g_native_poll_credit = fc.poll_due ? 0 : (1 << 30);
      g_callback_requests_switch = fc.switch_request;
      for (CPUContext* c : {a.get(), b.get()}) {
        c->gpr[3] = fc.type;
        c->gpr[4] = kColor;
        c->fpr[1] = fc.start;
        c->fpr[2] = fc.end;
        c->fpr[3] = fc.nearv;
        c->fpr[4] = fc.farv;
        c->ps1[31] = fc.ps1_31;
        c->pc = 0x803CD2A0u;
        put32(*c, kColor, 0x11223344u);
      }
      run_case("gx_set_fog", i, *a, *b, func_803CD2A0,
               [](CPUContext& ctx) {
                 g_native_preempted = nlx::gx_set_fog(nlx::guest_mem(ctx), ctx.inst_count,
                                                        ctx.gpr[2], ctx.gpr[3], ctx.gpr[4],
                                                        ctx.fpr[1], ctx.fpr[2], ctx.fpr[3],
                                                        ctx.fpr[4], ctx);
                 if (g_native_preempted) {
                   // The staged entry restarts the translated body directly
                   // from entry with the original poll credit restored.
                   func_803CD2A0(ctx);
                 }
               },
               kStrictModified);
    }
    // The randomised cases: finite values only, because the guest's fog
    // normalisation loop does not terminate for an infinite quotient.
    std::uniform_real_distribution<float> dist(-1.0e4f, 1.0e4f);
    for (int i = 0; i < 40; ++i) {
      const uint32_t type = rng() & 0xFu;
      const float start = dist(rng);
      const float end = dist(rng);
      const float nearv = dist(rng);
      const float farv = dist(rng);
      reset_registers(*a);
      reset_registers(*b);
      g_native_poll_credit = 1 << 30;
      g_callback_requests_switch = false;
      const uint32_t color = rng();
      for (CPUContext* c : {a.get(), b.get()}) {
        c->gpr[3] = type;
        c->gpr[4] = kColor;
        c->fpr[1] = start;
        c->fpr[2] = end;
        c->fpr[3] = nearv;
        c->fpr[4] = farv;
        c->pc = 0x803CD2A0u;
        put32(*c, kColor, color);
      }
      run_case("gx_set_fog", kFogCases + i, *a, *b, func_803CD2A0,
               [](CPUContext& ctx) {
                 g_native_preempted = nlx::gx_set_fog(nlx::guest_mem(ctx), ctx.inst_count,
                                                        ctx.gpr[2], ctx.gpr[3], ctx.gpr[4],
                                                        ctx.fpr[1], ctx.fpr[2], ctx.fpr[3],
                                                        ctx.fpr[4], ctx);
                 if (g_native_preempted) {
                   // The staged entry restarts the translated body directly
                   // from entry with the original poll credit restored.
                   func_803CD2A0(ctx);
                 }
               },
               kStrictModified);
    }
    g_native_poll_credit = 1 << 30;
    g_callback_requests_switch = false;
    std::printf("NATIVE_LOWERING_TESTS gx_set_fog %d/%d\n", g_cases - g_group_failures - before,
                g_cases - before);
  }

  std::printf("NATIVE_LOWERING_TESTS total %d/%d\n", g_cases - g_failures, g_cases);

  // --- the paired micro benchmark ---------------------------------------
  {
    reset_registers(*a);
    a->gpr[3] = 0;
    a->gpr[4] = kColor;
    a->fpr[1] = 1.0;
    a->fpr[2] = 100.0;
    a->fpr[3] = 0.0;
    a->fpr[4] = 1.0;
    put32(*a, kColor, 0x11223344u);
    const int kIters = 20000;
    std::vector<double> t_ms, n_ms;
    for (int rep = 0; rep < 7; ++rep) {
      auto t0 = std::chrono::steady_clock::now();
      for (int i = 0; i < kIters; ++i) {
        a->pc = 0x803CD2A0u;
        func_803CD2A0(*a);
      }
      auto t1 = std::chrono::steady_clock::now();
      for (int i = 0; i < kIters; ++i) {
        a->pc = 0x803CD2A0u;
        nlx::gx_set_fog(nlx::guest_mem(*a), a->inst_count, a->gpr[2], a->gpr[3], a->gpr[4],
                        a->fpr[1], a->fpr[2], a->fpr[3], a->fpr[4], *a);
      }
      auto t2 = std::chrono::steady_clock::now();
      t_ms.push_back(std::chrono::duration<double, std::milli>(t1 - t0).count() / kIters);
      n_ms.push_back(std::chrono::duration<double, std::milli>(t2 - t1).count() / kIters);
    }
    std::sort(t_ms.begin(), t_ms.end());
    std::sort(n_ms.begin(), n_ms.end());
    const double tm = t_ms[t_ms.size() / 2];
    const double nm = n_ms[n_ms.size() / 2];
    std::printf("NATIVE_LOWERING_BENCH gx_set_fog translated=%.6f ms/call native=%.6f ms/call "
                "speedup=%.2fx\n",
                tm, nm, tm / nm);
  }

  // --- the paired micro benchmark of the adopted GX family ---------------
  {
    const int kIters = 20000;
    auto bench = [](const char* name, auto translated, auto native_run) {
      std::vector<double> t_ms, n_ms;
      for (int rep = 0; rep < 7; ++rep) {
        auto t0 = std::chrono::steady_clock::now();
        for (int i = 0; i < kIters; ++i) translated();
        auto t1 = std::chrono::steady_clock::now();
        for (int i = 0; i < kIters; ++i) native_run();
        auto t2 = std::chrono::steady_clock::now();
        t_ms.push_back(std::chrono::duration<double, std::milli>(t1 - t0).count() / kIters);
        n_ms.push_back(std::chrono::duration<double, std::milli>(t2 - t1).count() / kIters);
      }
      std::sort(t_ms.begin(), t_ms.end());
      std::sort(n_ms.begin(), n_ms.end());
      const double tm = t_ms[t_ms.size() / 2];
      const double nm = n_ms[t_ms.size() / 2];
      std::printf("NATIVE_LOWERING_BENCH %s translated=%.6f ms/call native=%.6f ms/call "
                  "speedup=%.2fx\n", name, tm, nm, tm / nm);
    };

    // GXSetVtxAttrFmt
    reset_registers(*a);
    put_pattern(*a, kState, 0x600, 0x61u);
    bench("apply_gx_set_vtx_attr_fmt",
          [&] {
            a->gpr[3] = 0; a->gpr[4] = 11; a->gpr[5] = 1; a->gpr[6] = 2; a->gpr[7] = 3;
            a->pc = 0x803C9754u; func_803C9754(*a);
          },
          [&] {
            a->gpr[3] = 0; a->gpr[4] = 11; a->gpr[5] = 1; a->gpr[6] = 2; a->gpr[7] = 3;
            a->pc = 0x803C9754u; nlx::apply_gx_set_vtx_attr_fmt(*a);
          });

    // GXSetZMode
    reset_registers(*a);
    a->gpr[3] = 1; a->gpr[4] = 3; a->gpr[5] = 1;
    a->pc = 0x803CD698u;
    const uint32_t zt = 1, zf = 3, zu = 1;
    bench("gx_set_z_mode",
          [&] { a->pc = 0x803CD698u; func_803CD698(*a); },
          [&] { a->pc = 0x803CD698u;
                nlx::gx_set_z_mode(nlx::guest_mem(*a), a->inst_count, a->gpr[2], zt, zf, zu); });

    // GXLoadPosMtxImm / GXLoadNrmMtxImm
    reset_registers(*a);
    for (uint32_t k = 0; k < 12u; ++k) putf32(*a, kMatrix + 4u * k, 1.0f + static_cast<float>(k));
    a->gpr[3] = kMatrix; a->gpr[4] = 1;
    a->pc = 0x803CDA4Cu;
    bench("gx_load_pos_mtx_imm",
          [&] { a->pc = 0x803CDA4Cu; func_803CDA4C(*a); },
          [&] { a->pc = 0x803CDA4Cu;
                nlx::gx_load_pos_mtx_imm(nlx::guest_mem(*a), a->inst_count, a->gpr[3], a->gpr[4]); });
    a->pc = 0x803CDA9Cu;
    bench("gx_load_nrm_mtx_imm",
          [&] { a->pc = 0x803CDA9Cu; func_803CDA9C(*a); },
          [&] { a->pc = 0x803CDA9Cu;
                nlx::gx_load_nrm_mtx_imm(nlx::guest_mem(*a), a->inst_count, a->gpr[3], a->gpr[4]); });

    // GXLoadLightObjImm
    reset_registers(*a);
    for (uint32_t k = 0; k < 16u; ++k) putf32(*a, kLightObj + 4u * k, 1.0f + static_cast<float>(k));
    put32(*a, kLightObj + 12, 0x11223344u);
    bench("apply_gx_load_light_obj_imm",
          [&] { a->gpr[3] = kLightObj; a->gpr[4] = 4; a->pc = 0x803CB590u; func_803CB590(*a); },
          [&] { a->gpr[3] = kLightObj; a->gpr[4] = 4; a->pc = 0x803CB590u;
                nlx::apply_gx_load_light_obj_imm(*a); });

    // GXSetTexCoordGen2
    reset_registers(*a);
    put_pattern(*a, kState, 0x300, 0xC1u);
    for (uint32_t k = 0; k < 21u; ++k) put32(*a, 0x8057661Cu + 4u * k, 0x803C9BB4u);
    for (uint32_t k = 0; k < 7u; ++k) put32(*a, 0x80576600u + 4u * k, 0x803C9D24u);
    auto texgen_args = [&] {
      a->gpr[3] = 0; a->gpr[4] = 1; a->gpr[5] = 4; a->gpr[6] = 0x25; a->gpr[7] = 1; a->gpr[8] = 0x40;
    };
    bench("apply_gx_set_tex_coord_gen2",
          [&] { texgen_args(); a->pc = 0x803C9B88u; func_803C9B88(*a); },
          [&] { texgen_args(); a->pc = 0x803C9B88u; nlx::apply_gx_set_tex_coord_gen2(*a); });

    // GXSetChanCtrl
    reset_registers(*a);
    a->gpr[3] = 0; a->gpr[4] = 1; a->gpr[5] = 0; a->gpr[6] = 0;
    a->gpr[7] = 0xF; a->gpr[8] = 0; a->gpr[9] = 0;
    a->pc = 0x803CB7E0u;
    const uint32_t chan = 0, cen = 1, camb = 0, cmat = 0, cmask = 0xF, cdiff = 0, cattn = 0;
    bench("gx_set_chan_ctrl",
          [&] { a->pc = 0x803CB7E0u; func_803CB7E0(*a); },
          [&] { a->pc = 0x803CB7E0u;
                nlx::gx_set_chan_ctrl(nlx::guest_mem(*a), a->inst_count, a->gpr[2], chan, cen, camb,
                                      cmat, cmask, cdiff, cattn, a->xer); });

    // GXSetFogRangeAdj
    reset_registers(*a);
    bench("apply_gx_set_fog_range_adj",
          [&] { a->gpr[3] = 1; a->gpr[4] = 100; a->gpr[5] = kState + 0x100; a->pc = 0x803CD4CCu;
                func_803CD4CC(*a); },
          [&] { a->gpr[3] = 1; a->gpr[4] = 100; a->gpr[5] = kState + 0x100; a->pc = 0x803CD4CCu;
                nlx::apply_gx_set_fog_range_adj(*a); });
  }

  return g_failures == 0 ? 0 : 1;
}
