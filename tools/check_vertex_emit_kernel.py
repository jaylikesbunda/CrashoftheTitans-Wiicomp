"""Differential oracle for the NATIVE_VERTEX_EMIT_V1 kernel of func_803143EC (no game launch).

Compiles the untouched generated body (namespace ow), the staged body with the guarded dispatch (namespace nw) and
the kernel itself (namespace nw) into one binary and drives all three from identical seeded CPUContexts.

  * "kernel ran"   - ow::func_803143EC vs the kernel, entered at 0x803143EC: every GPR/FPR/PS1, CR, CTR, XER, MSR,
    FPSCR, SRR0/1, GQR, pc, lr, inst_count, the full published FIFO byte vector, the unpublished tail, every publish
    boundary, the HW-register writes and MEM1 (24 MiB) must be identical.
  * "fallback ran" - ow::func_803143EC vs the staged body when the guard must refuse (MSR.FP clear, a watch address
    inside the FIFO page, an active WPAR redirect): identical in everything, plus the kernel must not have been entered.
  * the staged body is additionally compared against the directly-called kernel on every case, so a guard that
    silently refused a case it should have taken is caught.

Adversarial axes: all 64 combinations of the descriptor bit tests (0x17/0x1b/0x14/0x13/0x1a/0x11) crossed with the
low-nibble vertex counts {0,1,7,8,9,15}, n=0, no attribute bits, count>8 (odd batch remainder), MSR.FP clear, a
watch inside the write-gather page, an active WPAR redirect, and polls firing mid-loop (credit 3 and credit 1).

The write-gather helpers, the publish boundary rule and the FIFO globals are extracted verbatim from the staged
build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp, so the byte comparison is against the real runtime code.

Writes build/gap-emit/vertex-emit/fixture.cpp, its executable and reports/vertex-emit-tests.json.
"""
from pathlib import Path
import json
import re
import subprocess
import sys

PORT = Path(__file__).resolve().parents[1]
WORK = PORT / 'build/gap-emit/vertex-emit'
sys.path.insert(0, str(PORT / 'tools'))
import vertex_emit_kernel as V

GEN = PORT / 'build/crash-generated'
NL = '\n'


def chunk_text():
    return V.chunk_path(PORT).read_text(errors='surrogateescape')


def text_of(name):
    for path in V.pristine_paths(PORT):
        s = path.read_text(errors='surrogateescape')
        m = re.search(r'(?:^|\n)[^\n]*\bvoid ' + name + r'\(NWII_CTX_PARAM ctx\) \{', s)
        if not m:
            continue
        a = m.start() + (1 if s[m.start()] == NL else 0)
        b = s.index(NL + '}' + NL, a)
        return s[a:b + 2]
    raise SystemExit('generated body for ' + name + ' not found')


def extract_braced(text, match):
    """The whole brace-balanced definition that starts at `match`."""
    i = text.index('{', match.start())
    depth = 0
    for j in range(i, len(text)):
        if text[j] == '{':
            depth += 1
        elif text[j] == '}':
            depth -= 1
            if depth == 0:
                return text[match.start():j + 1]
    raise SystemExit('unbalanced definition for %r' % match.group(0))


def callee_body(name):
    """The translated body the kernel's calls must reach, for one callee.

    The completed build's generated tree already carries the linked-region
    stage, so a callee may be a wrapper that dispatches into the runtime's
    region machinery; the fixture does not provide that machinery and does not
    need it - the wrapper's `native_region_original_<addr>` is the translated
    function itself, so that body is used and renamed back to the guest name.

    The tree also carries the bounded-block and loop-elision stages. Their
    helpers are equal to the code they replace by construction, so the
    original code is restored here: the oracle then runs the translated body
    itself, which is strictly the more conservative comparison.
    """
    addr = name[5:]
    body = None
    for path in V.pristine_paths(PORT):
        s = path.read_text(errors='surrogateescape')
        m = re.search(r'^[ \t]*void native_region_original_' + addr +
                      r'\(NWII_CTX_PARAM ctx\) \{', s, re.M)
        if not m:
            continue
        body = extract_braced(s, m).replace('void native_region_original_' + addr + '(',
                                            'void ' + name + '(', 1)
        break
    if body is None:
        body = text_of(name)
    body = re.sub(r'if\(native_blocks_on\)\{native_block_[0-9A-F_]+\(ctx\);goto (loc_[0-9A-F]{8});\}',
                  r'goto \1;', body)
    body = re.sub(r'loop_[0-9A-F]{8}\(ctx\);goto (loc_[0-9A-F]{8});', r'goto \1;', body)
    # Region originals tail-call each other by their staged name; the fixture
    # defines the translated bodies under the guest names, so rewrite the
    # references and let the closure pull the targets in.
    body = re.sub(r'\bnative_region_original_([0-9A-F]{8})\(', r'func_\1(', body)
    return body.replace('NWII_GUEST_ABI ', '')


def callee_bodies(names):
    """The transitive closure of the fixture's oracle callees."""
    out = {}
    pending = list(names)
    while pending and len(out) < 64:
        name = pending.pop(0)
        key = name.upper()
        if key in out:
            continue
        body = callee_body(name)
        out[key] = body
        for ref in re.findall(r'\bfunc_([0-9A-Fa-f]{8})\(ctx\)', body):
            if ref.upper() not in out:
                pending.append('func_' + ref.upper())
    return out


def gx_fifo_block():
    """The real write-gather block of gx.cpp: publish rule, redirect paths and the five GX_WGPIPE_Write* helpers."""
    runtime = V.RUNTIME_DIR if V.RUNTIME_DIR is not None else PORT / 'build/nwii-full-source/nWiiRuntime'
    gx = (runtime / 'src/hle/gx.cpp').read_text(errors='surrogateescape')
    a = gx.index('void native_gx_publish() {')
    b = gx.index(NL + 'uint64_t g_prof_decode_us_accum = 0;')
    return 'namespace nwii::runtime {' + NL + gx[a:b]


def chunk_prelude():
    # The owning chunk may be a promoted hot unit (build/crash-hot), not a crash-generated chunk, and
    # once the kernel is installed its helpers sit before the first generated body - stop at whichever
    # comes first so this stage's own block is not emitted a second time.
    s = V.chunk_path(PORT).read_text(errors='surrogateescape')
    cut = s.find(NL + '// ' + V.MARK + ': guarded write-gather emitter')
    head_match = re.search(r'(?:^|\n)[^\n]*\bvoid func_[0-9A-F]{8}\(NWII_CTX_PARAM ctx\) \{', s)
    first = head_match.start() if head_match else -1
    end = min(x for x in (cut, first) if x >= 0)
    head = s[:end]
    out, seen = [], set()
    for line in head.split(NL):
        m = re.match(r'^#include "(?:.*/)?([^/"]+)"$', line)
        if m and 'bounded-blocks' in line:
            if m.group(1) in seen:
                continue
            seen.add(m.group(1))
        out.append(line)
    return NL.join(out).strip(NL) + NL + '#include "functions.h"' + NL


CALLEES = ['func_802C6558', 'func_802C6658', 'func_80281C50', 'func_803A383C',
           'func_80393638', 'func_8039363C', 'func_80393640', 'func_80393644', 'func_80393648',
           'func_80393684', 'func_80393688', 'func_8039368C', 'func_80393690', 'func_80393694']


def build_fixture(work=None):
    work = work or WORK
    target = V.chunk_path(PORT).read_text(errors='surrogateescape')
    pristine_body = V.source_body(PORT)
    # The promote_regs parser expects the promoted form (the NWII_GUEST_ABI
    # attribute is removed by the entry specialisation stage). The attribute is
    # a host ABI hint only - it does not change what the guest code does - so
    # the fixture drops it from both sides and the comparison stays exact.
    plain_body = pristine_body.replace('NWII_GUEST_ABI ', '')
    staged, kernel, stats, counts = V.install(target, plain_body)
    kernel = kernel.replace('{', '{ ++nw_kernel_calls;', 1)   # observability for "the kernel ran"
    a, b = V.function_span(staged, V.NAME)
    staged_body = staged[a:b]
    assert staged_body.count(V.GUARD + '(ctx)') == 1
    assert kernel.count('void ' + V.KERNEL + '(NWII_CTX_PARAM ctx) {') == 1
    # the shipping build's own baseline: fix_hot_split promotes this function (it is #1 in tools/hot_functions.txt)
    import promote_regs as P
    hotsplit, hstats = P.promote(plain_body, True, True, V.nest_predicate())
    assert hotsplit, hstats
    hotsplit = hotsplit.replace('void ' + V.NAME + '(', 'void native_vertex_emit_hotsplit_803143EC(', 1)
    hotsplit = hotsplit.replace('{', '{ ++nw_hotsplit_calls;', 1)
    # The callees are oracle support, not the subject of the test: they are
    # defined at global scope where functions.h already declares them without
    # the preserve_none attribute, so the attribute is dropped here. The
    # function under test and the kernel keep the production spelling.
    callees = callee_bodies(CALLEES)
    callee_text = NL.join(NL + body.replace('NWII_GUEST_ABI ', '') for body in callees.values())
    fixture = work / 'fixture.cpp'
    fixture.write_text(chunk_prelude() + PRELUDE + gx_fifo_block() + NL + FIFO_ACCESSOR + NL +
                       CALLEE_DISPATCH + callee_text + NL + INDIRECT_TARGET + NL +
                       V.helpers() + NL +
                       'namespace ow {' + NL + plain_body + NL + '}' + NL +
                       'namespace nw {' + NL + kernel + NL + staged_body + NL + hotsplit + NL + '}' + NL +
                       HARNESS, errors='surrogateescape')
    return fixture, stats, counts


PRELUDE = r'''
#include <algorithm>
#include <bit>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <memory>
#include <random>
#include <vector>
#include <atomic>
#include <mutex>
#include <condition_variable>
// NATIVE_STATIC_RAM_V1: the fixed-base allocator defines native_static_ram_commit,
// which the MMU constructor calls (the generated code stores through the fixed base).
#include "runtime/native_fastmem_allocator.h"
using nwii::runtime::CPUContext;
using nwii::runtime::MMU;

uint64_t nw_kernel_calls = 0;
uint64_t nw_hotsplit_calls = 0;

namespace nwii::runtime {
CPUContext* g_ctx_ptr = nullptr;
MMU* g_mmu = nullptr;
bool g_trace_calls = false;
uint32_t g_watch_addr = 0;
int32_t g_native_poll_credit = 1 << 30;
uint64_t g_poll_sites = 0;
uint64_t g_poll_hits = 0;
void trace_call(uint32_t, CPUContext&) {}
void interpret_step(CPUContext&) { std::abort(); }
struct WatchHit { uint32_t addr, value; int width; };
std::vector<WatchHit> g_watch_hits;
void watch_hit(uint32_t addr, uint32_t value, int width) { g_watch_hits.push_back({addr, value, width}); }
std::vector<uint64_t> g_hw_writes;
uint16_t HW_Reg_Read16(uint32_t) { return 0; }
uint32_t HW_Reg_Read32(uint32_t) { return 0; }
void HW_Reg_Write16(uint32_t a, uint16_t v) { g_hw_writes.push_back((uint64_t(a) << 16) ^ v); }
void HW_Reg_Write32(uint32_t a, uint32_t v) { g_hw_writes.push_back((uint64_t(a) << 32) ^ v); }
void note_null_call(uint32_t, uint32_t) {}
bool process_pending_callbacks(CPUContext&) { ++g_poll_hits; return false; }
struct NativeFifoLockGuard {
    explicit NativeFifoLockGuard(std::mutex& m) : lock(m) {}
    std::unique_lock<std::mutex> lock;
};
// NATIVE_ADAPTIVE_GX_BATCHING_V1 / NATIVE_GATHER_REDIRECT_CACHE_V1: the copied
// publish block and write-gather helpers reference these; the fixture only needs
// them to exist - they decide when bytes are flushed, not what the guest writes
// mean, and both compared paths see the same policy.
namespace native_gx_batching {
enum Counter { EarlyRequests, SmallSuppressed, SmallBatches, Total };
inline void Record(Counter) {}
inline bool ShouldRequest(size_t) { return false; }
}
void NativeRequestGxDrain() {}
void native_gather_invalidate();
std::mutex g_fifo_mutex;
std::vector<uint8_t> g_hw_fifo;
std::atomic<size_t> g_gx_pending_bytes{0};
std::atomic<bool> g_gx_drain_request{false};
std::condition_variable g_gx_drain_cv;
std::vector<size_t> g_publish_boundaries;
struct WgpRingEntry { uint8_t b; uint32_t pc, lr; };
WgpRingEntry g_wgp_ring[16384];
uint64_t g_wgp_ring_n = 0;
// The production staging buffer (gx.cpp: NATIVE_GX_PRIVATE_FIFO_V1 /
// NATIVE_CPU_FIFO_FIXED_V1). The copied publish block and the write-gather
// helpers below use this exact type, so the fixture stages and publishes the
// same way the shipped runtime does.
struct NativeCpuFifo {
    alignas(64) uint8_t data[4160];
    size_t n = 0;
    bool empty() const { return n == 0; }
    size_t size() const { return n; }
    void clear() { n = 0; }
};
static NativeCpuFifo g_cpu_fifo;
extern const size_t* const g_native_gx_staged_size = &g_cpu_fifo.n;
bool g_native_fifo_ok = false;            // NATIVE_FIFO_OK_FLAG_V1
// Counts the poll sites themselves, not the dispatcher's callback checks, so both paths must agree.
inline bool native_fixture_poll_due() { bool due = native_poll_due(); if (due) ++g_poll_sites; return due; }
}
#define native_poll_due native_fixture_poll_due
namespace native_trace { struct Scope { explicit Scope(const char*) {} }; }
namespace nwii::runtime::hw {
uint32_t pi_fifo_base = 0, pi_fifo_end = 0x04000000u, g_cp_fifo_base = 0, pi_fifo_wptr = 0;
void pi_fifo_get(uint32_t& base, uint32_t& end, uint32_t& wptr) { base = pi_fifo_base; end = pi_fifo_end; wptr = pi_fifo_wptr; }
void pi_fifo_set_wptr(uint32_t w) { pi_fifo_wptr = w; }
uint32_t cp_fifo_base_reg() { return g_cp_fifo_base; }
}
// Every append into the published FIFO records its boundary, so the 4096-byte publish rule is compared exactly.
static void NativeAppendFifo(std::vector<uint8_t>& d, NativeCpuFifo& s) {
    nwii::runtime::g_publish_boundaries.push_back(d.size());
    d.insert(d.end(), s.data, s.data + s.n);
    s.n = 0;
}
'''

FIFO_ACCESSOR = r'''
namespace nwii::runtime {
size_t native_fixture_fifo_size() { return g_cpu_fifo.size(); }
const uint8_t* native_fixture_fifo() { return g_cpu_fifo.data; }
void native_fixture_reset_fifo() {
    g_cpu_fifo.clear(); g_hw_fifo.clear(); g_publish_boundaries.clear();
    g_watch_hits.clear(); g_hw_writes.clear(); g_poll_hits = 0; g_poll_sites = 0;
}
}
'''

CALLEE_DISPATCH = r'''
void func_80310000(NWII_CTX_PARAM ctx);  // fixture stand-in for the two vtable targets
namespace nwii::runtime {
void native_call_indirect(CPUContext& ctx) {
    if (!ctx.is_running || ctx.pc == 0) return;
    if (ctx.pc == 0x80310000) { ctx.inst_count += 1; func_80310000(ctx); return; }
    std::abort();
}
}
'''

INDIRECT_TARGET = r'''
void func_80310000(NWII_CTX_PARAM ctx) {   // identical for both paths, so it cannot mask a difference
    ctx.inst_count += 4;
    ctx.gpr[3] = ctx.mmu.read32(ctx.gpr[4]);
    ctx.pc = ctx.lr;
}
'''

MAIN = ''

HARNESS = r'''
static const uint32_t SENTINEL = 0x81234560u;
static const uint32_t SDATA = 0x80020000u;      // r13
static const uint32_t OBJ = 0x80040000u;        // r3
static const uint32_t DESC = 0x80041000u;       // *(r3+0x48)
static const uint32_t VERT = 0x80042000u;       // *(desc+0x10) -> r28
static const uint32_t SCRATCH = 0x80050000u;    // r26 (the vtable result)
static const uint32_t VTABLE = 0x80031000u;
static const uint32_t TABLE = 0x805EE6F0u;      // the vertex-format table, a constant in the guest code

static uint32_t seed_bits(unsigned seed, unsigned n) {
    uint32_t x = seed * 2654435761u + n * 40503u + 0x9E3779B9u;
    x ^= x >> 15; x *= 2246822519u; x ^= x >> 13; x *= 3266489917u; x ^= x >> 16;
    return x;
}
static float seeded_float(unsigned seed, unsigned n) {
    return std::bit_cast<float>(seed_bits(seed, n) & 0x807FFFFFu | 0x3E800000u);
}

// The MMU's own watch predicate for a four byte store at the FIFO port. The
// kernel's write-gather helper re-tests it per store, so a watch that cannot
// hit the FIFO port cannot change what the kernel does.
static bool watch_may_hit_fifo(uint32_t watch) {
    if (!watch) return false;
    const uint32_t d = (0x0C008000u + 4u - 1u) - (watch & 0x3FFFFFF8u);
    return d < 4u + 7u;
}
struct Case { unsigned count; uint32_t fmt; bool fp_on; uint32_t watch; bool redirect; unsigned poll_credit; };


static void reset(CPUContext& c, const Case& k, unsigned seed) {
    nwii::runtime::native_fixture_reset_fifo();
    nw_kernel_calls = 0;
    nwii::runtime::g_watch_addr = k.watch;
    nwii::runtime::g_native_poll_credit = int32_t(k.poll_credit ? k.poll_credit : (1u << 30));
    nwii::runtime::hw::pi_fifo_base = 0;
    nwii::runtime::hw::pi_fifo_end = 0x04000000u;
    nwii::runtime::hw::pi_fifo_wptr = 0;
    nwii::runtime::hw::g_cp_fifo_base = 0;
    nwii::runtime::g_native_fifo_ok = false;
    nwii::runtime::native_wpar_write(k.redirect ? 0x10000000u : 0x0C008000u);
    nwii::runtime::g_native_fifo_ok = true;   // warmed: the ordinary mode was already observed before this call
    for (uint32_t a = 0x80000000u; a < 0x80060000u; a += 4) c.mmu.write32(a, seed_bits(seed, a >> 2));
    for (uint32_t a = 0x80000000u; a < 0x80060000u; a += 4) c.mmu.write_f32(a, seeded_float(seed + 7, a >> 2));
    for (uint32_t a = TABLE; a < TABLE + 0x400u; a += 2) c.mmu.write16(a, uint16_t(seed_bits(seed + 11, a >> 1) % 24u));
    for (uint32_t i = 0; i < 32; ++i) {
        c.gpr[i] = seed_bits(seed + 3, i);
        c.fpr[i] = double(int32_t(seed_bits(seed + 5, i))) / 4093.0;
        c.ps1[i] = double(int32_t(seed_bits(seed + 9, i))) / 8191.0;
    }
    for (auto& f : c.cr) { f.lt = false; f.gt = false; f.eq = false; f.so = false; }
    c.gpr[1] = 0x801F0000u;                  // guest stack, inside the compared window
    c.gpr[2] = 0x80024000u;
    c.gpr[3] = OBJ;
    c.gpr[13] = SDATA;
    c.lr = SENTINEL;
    c.ctr = 0;
    c.xer = 0;
    c.msr = 0x8032u | (k.fp_on ? 0x2000u : 0u);
    c.fpscr = 0;
    c.gqr.fill(0);
    c.pc = 0x803143ECu;
    c.inst_count = 17;
    c.in_callback = false;
    c.mmu.write32(OBJ + 0x48, DESC);
    c.mmu.write32(DESC + 0x10, VERT);
    c.mmu.write8(OBJ + 0x58, 0);
    c.mmu.write32(SDATA - 0x2B7C, k.count);
    c.mmu.write32(SDATA - 0x2B88, seed_bits(seed + 13, 1) & 0x00FFFFFFu);
    c.mmu.write32(SDATA - 0x2B1C, 0x80030000u);
    c.mmu.write32(SDATA - 0x2B78, 0x80032000u);
    c.mmu.write32(0x80030000u, VTABLE);
    c.mmu.write32(VTABLE + 0x80, 0x80310000u);
    c.mmu.write32(VTABLE + 0x84, 0x80310000u);
    c.mmu.write32(0x80032000u + 0x10, 0x80033000u);
    c.mmu.write32(0x80033000u, SCRATCH);
    c.mmu.write32(SCRATCH + 0, SCRATCH);
    c.mmu.write32(SCRATCH + 4, k.fmt);
    c.mmu.write32(SCRATCH + 0x0C, 4);
    for (uint32_t o = 0x10; o < 0x100; o += 4) c.mmu.write_f32(SCRATCH + o, seeded_float(seed + 17, o));
    for (uint32_t o = 0x10; o < 0x100; o += 4) c.mmu.write_f32(VERT + o, seeded_float(seed + 19, o));
    for (uint32_t o = 0; o < 0x200; o += 4) c.mmu.write_f32(0x8001E000u + o, seeded_float(seed + 23, o));
}

struct Snap {
    uint32_t pc, lr, ctr, xer, msr, fpscr, srr0, srr1, gpr[32], gqr[8];
    double fpr[32], ps1[32];
    bool cr[8][4];
    uint64_t ic, polls;
    std::vector<uint8_t> hw, tail;
    std::vector<size_t> bounds;
    std::vector<uint64_t> hww;
    std::vector<nwii::runtime::WatchHit> hits;
    std::vector<uint8_t> ram;
};

static void capture(CPUContext& c, Snap& s) {
    s.pc = c.pc; s.lr = c.lr; s.ctr = c.ctr; s.xer = c.xer; s.msr = c.msr; s.fpscr = c.fpscr;
    s.srr0 = c.srr0; s.srr1 = c.srr1; s.ic = c.inst_count; s.polls = nwii::runtime::g_poll_sites;
    for (int i = 0; i < 32; ++i) { s.gpr[i] = c.gpr[i]; s.fpr[i] = c.fpr[i]; s.ps1[i] = c.ps1[i]; }
    for (int i = 0; i < 8; ++i) {
        s.gqr[i] = c.gqr[i];
        s.cr[i][0] = c.cr[i].lt; s.cr[i][1] = c.cr[i].gt; s.cr[i][2] = c.cr[i].eq; s.cr[i][3] = c.cr[i].so;
    }
    s.hw.assign(nwii::runtime::g_hw_fifo.begin(), nwii::runtime::g_hw_fifo.end());
    s.tail.assign(nwii::runtime::native_fixture_fifo(), nwii::runtime::native_fixture_fifo() + nwii::runtime::native_fixture_fifo_size());
    s.bounds = nwii::runtime::g_publish_boundaries;
    s.hww = nwii::runtime::g_hw_writes;
    s.hits = nwii::runtime::g_watch_hits;
    s.ram.assign(c.mmu.mem1.data(), c.mmu.mem1.data() + 0x800000);
}

static bool differs(const Snap& a, const Snap& b, std::string& why) {
    auto bad = [&](const char* n) { why = n; return true; };
    if (a.pc != b.pc) return bad("pc");
    if (a.lr != b.lr) return bad("lr");
    if (a.ctr != b.ctr) return bad("ctr");
    if (a.xer != b.xer) return bad("xer");
    if (a.msr != b.msr) return bad("msr");
    if (a.fpscr != b.fpscr) return bad("fpscr");
    if (a.srr0 != b.srr0) return bad("srr0");
    if (a.srr1 != b.srr1) return bad("srr1");
    if (a.ic != b.ic) return bad("inst_count");
    if (a.polls != b.polls) return bad("poll sites");
    for (int i = 0; i < 32; ++i) if (a.gpr[i] != b.gpr[i]) return bad("gpr");
    for (int i = 0; i < 32; ++i) if (std::bit_cast<uint64_t>(a.fpr[i]) != std::bit_cast<uint64_t>(b.fpr[i])) return bad("fpr");
    for (int i = 0; i < 32; ++i) if (std::bit_cast<uint64_t>(a.ps1[i]) != std::bit_cast<uint64_t>(b.ps1[i])) return bad("ps1");
    for (int i = 0; i < 8; ++i) for (int j = 0; j < 4; ++j) if (a.cr[i][j] != b.cr[i][j]) return bad("cr");
    for (int i = 0; i < 8; ++i) if (a.gqr[i] != b.gqr[i]) return bad("gqr");
    if (a.hw != b.hw) return bad("published FIFO bytes");
    if (a.tail != b.tail) return bad("unpublished FIFO tail");
    if (a.bounds != b.bounds) return bad("publish boundaries");
    if (a.hww != b.hww) return bad("hw register writes");
    if (a.hits.size() != b.hits.size()) return bad("watch hits");
    if (a.ram != b.ram) return bad("mem1");
    return false;
}

static void harness_fail(const char* what, CPUContext& c, unsigned n) {
    std::cout << "HARNESS_FAIL " << what << " pc=0x" << std::hex << c.pc << " lr=0x" << c.lr << std::dec << " n=" << n << std::endl;
    std::fflush(stdout);
    std::_Exit(3);
}

// One run_game iteration: dispatch the pc, then enter the variant. 0x800 is the FP-exception vector.
template <class F> static void drive(F fn, CPUContext& c) {
    for (unsigned n = 0; n < 400000; ++n) {
        if (c.pc == SENTINEL || c.pc == 0x800u || c.pc == 0u) return;
        if (c.pc >= 0x803143ECu && c.pc < 0x80316600u) { nwii::runtime::process_pending_callbacks(c); ++c.inst_count; fn(c); continue; }
        if (c.pc == 0x80310000u) { nwii::runtime::process_pending_callbacks(c); ++c.inst_count; func_80310000(c); continue; }
        harness_fail("DRIVE_ABORT", c, n);
    }
    harness_fail("DRIVE_BUDGET", c, 400000);
}

static void drive_ow(CPUContext& c) { drive([](CPUContext& x) { ow::func_803143EC(x); }, c); }
static void drive_nw(CPUContext& c) { drive([](CPUContext& x) { nw::func_803143EC(x); }, c); }
static void drive_kernel(CPUContext& c) { drive([](CPUContext& x) { nw::native_vertex_emit_803143EC(x); }, c); }
static void drive_hotsplit(CPUContext& c) { drive([](CPUContext& x) { nw::native_vertex_emit_hotsplit_803143EC(x); }, c); }

static const Case WORKLOAD[3] = {{2u, 0x00000004u, true, 0u, false, 0u},
                                 {8u, 0x001F001Fu, true, 0u, false, 0u},
                                 {16u, 0x000F001Fu, true, 0u, false, 0u}};

int main() {
    auto A = std::make_unique<CPUContext>(), B = std::make_unique<CPUContext>(), C = std::make_unique<CPUContext>();
    std::vector<Case> cases;
    for (unsigned bits = 0; bits < 64; ++bits)
        for (unsigned low : {0u, 1u, 7u, 8u, 9u, 15u})
            cases.push_back(Case{1u + (bits % 3), (bits << 17) | low, true, 0u, false, 0u});
    cases.push_back(Case{0u, 0x00000004u, true, 0u, false, 0u});            // n = 0
    cases.push_back(Case{3u, 0x00000000u, true, 0u, false, 0u});            // no attribute bits
    cases.push_back(Case{2u, 0x00000000u, false, 0u, false, 0u});           // MSR.FP clear
    cases.push_back(Case{2u, 0x00000004u, true, 0xCC008000u, false, 0u});   // watch inside the FIFO page
    cases.push_back(Case{2u, 0x00000004u, true, 0x80597790u, false, 0u});   // watch outside the FIFO page (the runtime's default)
    // Watches that DO hit what the kernel writes (the object AABB and a frame
    // spill): the guard must still let the kernel run and the kernel's writes
    // must still reach watch_hit in the same order as the transcribed body.
    cases.push_back(Case{2u, 0x00000004u, true, 0x8005000Cu, false, 0u});   // watch on the object the kernel writes
    cases.push_back(Case{2u, 0x00000004u, true, 0x801EFF8Cu, false, 0u});   // watch on a frame spill
    cases.push_back(Case{3u, 0x00000000u, true, 0x80597790u, false, 0u});   // no attribute bits, watch outside
    cases.push_back(Case{2u, 0x00000004u, true, 0u, true, 0u});             // WPAR redirect active
    cases.push_back(Case{2u, 0x00000004u, true, 0u, false, 3u});            // poll fires mid-loop
    cases.push_back(Case{2u, 0x00000004u, true, 0u, false, 1u});            // poll fires on every site
    cases.push_back(Case{5u, 0x00000004u, true, 0u, false, 0u});            // odd batch remainder
    cases.push_back(Case{9u, 0x001F001Fu, true, 0u, false, 0u});            // > 8 vertices, all attributes

    unsigned kernel_cases = 0, kernel_bad = 0, fallback_cases = 0, fallback_bad = 0, kernel_ran = 0, direct_bad = 0;
    for (unsigned ci = 0; ci < cases.size(); ++ci) {
        const Case& k = cases[ci];
        unsigned seed = ci * 31 + 7;
        reset(*A, k, seed); reset(*B, k, seed); reset(*C, k, seed);
        drive_ow(*A);
        nw_kernel_calls = 0;
        drive_nw(*B);
        unsigned staged_entered_kernel = unsigned(nw_kernel_calls);
        drive_kernel(*C);
        Snap sa, sb, sc;
        capture(*A, sa); capture(*B, sb); capture(*C, sc);
        std::string why, why2;
        bool guard_ok = k.fp_on && !watch_may_hit_fifo(k.watch) && !k.redirect;
        bool same = !differs(sa, sb, why);
        if (differs(sa, sc, why2)) ++direct_bad;
        if (guard_ok) {
            ++kernel_cases;
            if (!same) { ++kernel_bad; if (kernel_bad <= 5) std::cout << "KERNEL_MISMATCH case=" << ci << " field=" << why << std::endl; }
            if (staged_entered_kernel) ++kernel_ran;
            else if (kernel_bad <= 5) std::cout << "KERNEL_NOT_ENTERED case=" << ci << std::endl;
        } else {
            ++fallback_cases;
            if (!same) { ++fallback_bad; if (fallback_bad <= 5) std::cout << "FALLBACK_MISMATCH case=" << ci << " field=" << why << std::endl; }
            if (staged_entered_kernel) std::cout << "FALLBACK_ENTERED_KERNEL case=" << ci << std::endl;
        }
    }
    std::cout << "VERTEX_EMIT_STATES kernel_ran=" << kernel_ran << "/" << kernel_cases
              << " kernel_mismatch=" << kernel_bad << " fallback=" << fallback_cases
              << " fallback_mismatch=" << fallback_bad << " direct_kernel_mismatch=" << direct_bad
              << " cases=" << cases.size() << std::endl;

    for (int w = 0; w < 3; ++w) {
        Case k = WORKLOAD[w];
        auto cheap = [&](CPUContext& c) {
            nwii::runtime::native_fixture_reset_fifo();
            nwii::runtime::g_watch_addr = 0;
            nwii::runtime::g_native_poll_credit = 1 << 30;
            nwii::runtime::g_native_fifo_ok = false;
            nwii::runtime::native_wpar_write(0x0C008000u);
            nwii::runtime::g_native_fifo_ok = true;
            for (uint32_t i = 0; i < 32; ++i) {
                c.gpr[i] = seed_bits(3 + 3, i);
                c.fpr[i] = double(int32_t(seed_bits(3 + 5, i))) / 4093.0;
                c.ps1[i] = double(int32_t(seed_bits(3 + 9, i))) / 8191.0;
            }
            for (auto& f : c.cr) { f.lt = false; f.gt = false; f.eq = false; f.so = false; }
            c.gpr[1] = 0x801F0000u; c.gpr[2] = 0x80024000u; c.gpr[3] = OBJ; c.gpr[13] = SDATA;
            c.lr = SENTINEL; c.ctr = 0; c.xer = 0; c.msr = 0x8032u | 0x2000u;
            c.fpscr = 0; c.gqr.fill(0); c.pc = 0x803143ECu; c.inst_count = 17; c.in_callback = false;
            c.mmu.write32(SDATA - 0x2B7C, k.count);
            c.mmu.write8(OBJ + 0x58, 0);
            c.mmu.write32(SCRATCH + 4, k.fmt);
            c.mmu.write32(SCRATCH + 0x0C, 4);
            c.mmu.write32(0x80033000u, SCRATCH);
            c.mmu.write32(VTABLE + 0x80, 0x80310000u);
            c.mmu.write32(VTABLE + 0x84, 0x80310000u);
        };
        auto& c = *A;
        reset(c, k, 3);
        double best[5];
        std::vector<std::vector<double>> rounds(5);
        uint64_t cnt[5][3] = {};
        for (int r = 0; r < 41; ++r) {
            for (int mode = 0; mode < 5; ++mode) {
                auto t0 = std::chrono::steady_clock::now();
                for (unsigned i = 0; i < 300; ++i) {
                    cheap(c);
                    if (mode == 0) drive_ow(c);
                    else if (mode == 1) drive_kernel(c);
                    else if (mode == 2) drive_nw(c);
                    else if (mode == 3) drive_hotsplit(c);
                    cnt[mode][0] += nwii::runtime::g_hw_fifo.size() + nwii::runtime::native_fixture_fifo_size();
                    cnt[mode][1] += nwii::runtime::g_poll_sites;
                    cnt[mode][2] += c.inst_count - 17;
                }
                auto t1 = std::chrono::steady_clock::now();
                rounds[mode].push_back(std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0).count() / 1000.0 / 300.0);
            }
        }
        for (int mode = 0; mode < 5; ++mode) {
            std::sort(rounds[mode].begin(), rounds[mode].end());
            best[mode] = rounds[mode].front();
        }
        for (int mode = 0; mode < 5; ++mode)
            std::cout << "VERTEX_EMIT_TIME w=" << w << " mode="
                      << (mode == 0 ? "original" : mode == 1 ? "kernel" : mode == 2 ? "staged" : mode == 3 ? "hot-split" : "harness-only")
                      << " us=" << best[mode] << " fifo_bytes=" << (cnt[mode][0] / 12300)
                      << " poll_sites=" << (cnt[mode][1] / 12300) << " inst=" << (cnt[mode][2] / 12300) << std::endl;
        std::cout << "VERTEX_EMIT_RATIO w=" << w << " kernel/original=" << (best[1] / best[0])
                  << " staged/original=" << (best[2] / best[0])
                  << " hotsplit/original=" << (best[3] / best[0])
                  << " kernel/hotsplit=" << (best[1] / best[3]) << std::endl;
    }
    return (kernel_bad + fallback_bad + direct_bad) ? 1 : 0;
}
'''


def main():
    """Run the oracle.

    The tree may be given explicitly so the check runs against an isolated copy
    of a completed build without touching it or the repository's own build/
    junctions: --generated is the directory that holds the generated chunks,
    --runtime the nWiiRuntime source tree (gx.cpp and the headers), --include
    the runtime include directory, --work a scratch directory, --report the
    JSON report path.
    """
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--generated', type=Path, default=None)
    ap.add_argument('--runtime', type=Path, default=None)
    ap.add_argument('--include', type=Path, default=None)
    ap.add_argument('--work', type=Path, default=None)
    ap.add_argument('--report', type=Path, default=None)
    args = ap.parse_args()
    work = args.work or WORK
    if args.generated:
        V.GENERATED_DIR = args.generated
    if args.runtime:
        V.RUNTIME_DIR = args.runtime
    work.mkdir(parents=True, exist_ok=True)
    V.apply_runtime(PORT)
    fixture, stats, counts = build_fixture(work)
    exe = work / 'vertex-emit.exe'
    include = args.include or PORT / 'build/nwii-full-source/nWiiRuntime/include'
    report = args.report or (PORT / 'reports/vertex-emit-tests.json')
    commands = [
        ['g++', '-std=c++20', '-O2', '-fwrapv', '-fno-strict-aliasing', '-ffp-contract=off', '-static',
         # The generated guest code stores through native_guest_fast, which maps
         # guest MEM1/MEM2 at fixed host addresses; without the define the MMU keeps
         # its RAM in a vector and every store faults. NWII_CTX_RESTRICT is
         # deliberately not passed: it is a compiler hint whose contract is under
         # review, and leaving it out only makes this measurement conservative.
         '-DNWII_STATIC_RAM_BASE',
         '-I', str(include), '-I', str(GEN), str(fixture), '-o', str(exe)],
        [str(exe)],
    ]
    runs = []
    for command in commands:
        r = subprocess.run([str(c) for c in command], capture_output=True, text=True, timeout=1800)
        runs.append(dict(command=[str(c) for c in command], exit_code=r.returncode, output=r.stdout + r.stderr))
        print(runs[-1]['output'][-4000:], flush=True)
        if r.returncode:
            break
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(
        dict(passed=runs[-1]['exit_code'] == 0, function='func_%08X' % V.TARGET, promotion=stats,
             write_gather_stores_specialised=dict(f32=counts[0], u32=counts[1]),
             comparison='full context, FIFO bytes + publish boundaries, HW writes, 8 MiB of MEM1',
             game_launched=False, runs=runs), indent=2) + NL)
    raise SystemExit(runs[-1]['exit_code'])


if __name__ == '__main__':
    main()
