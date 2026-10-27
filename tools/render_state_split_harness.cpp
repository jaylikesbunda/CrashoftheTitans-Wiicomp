// Differential harness for the renderer-owned GX state split (NATIVE_RENDERER_STATE_SPLIT_V1).
//
// Builds:
//   -DHARNESS_OLD  : unpatched staged fifo_parser.cpp. The renderer's state loop writes gx::g_state (old behaviour).
//   -DHARNESS_NEW  : patched fifo_parser.cpp + render_state.cpp. The loop writes RenderState() after RenderStateBegin().
//
// The renderer itself needs GL and cannot run headless, so this harness models exactly the state effects of
// RendererGL::Render's command loop (BP shadow resolution, TLUT copy from the early-signal snapshot, XF and matrix
// loads, projection, EFB-copy pe_clear). The GL calls have no effect on the state compared here.
//
// Usage: harness <seed> <batches> <sync|queued>
//   sync   : parse drain N, then render drain N (old ordering).
//   queued : parse all drains first, then render them in order (the queued ordering of the deferred renderer).
// Output: one line per observation. Identical output for OLD sync, NEW sync and NEW queued means the split is exact.
#include "runtime/cpu_context.h"
#include "runtime/gx/fifo_parser.h"
#include "runtime/gx/command_stream.h"
#include "runtime/gx_state.h"
#include "runtime/gx_command.h"
#ifdef HARNESS_NEW
#include "runtime/gx/render_state.h"
#endif
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace nwii::runtime { extern MMU* g_mmu; }
using namespace nwii::runtime;
using namespace nwii::runtime::gx;

// ---------------------------------------------------------------- link stubs
// Symbols fifo_parser.cpp needs that live outside the GX path (HW registers, the RAM watch, the DL fetch).
namespace nwii::runtime {
MMU* g_mmu = nullptr;
uint32_t g_watch_addr = 0;
uint32_t HW_Reg_Read32(uint32_t) { return 0; }
uint64_t native_ram_newest_write(const uint8_t*, size_t, size_t, size_t) { return 0; }
bool GX_DL_Fetch(uint32_t addr, uint32_t size, std::vector<uint8_t>& out) {
    out.resize(size);
    for (uint32_t i = 0; i < size; ++i) out[i] = g_mmu->read8(addr + i);
    return true;
}
namespace gx { GXState g_state; }
}

// ---------------------------------------------------------------- deterministic generator
static uint64_t g_rng = 0x9E3779B97F4A7C15ull;
static uint32_t Rnd() { g_rng ^= g_rng >> 12; g_rng ^= g_rng << 25; g_rng ^= g_rng >> 27; return uint32_t((g_rng * 0x2545F4914F6CDD1Dull) >> 32); }
static uint32_t Rnd(uint32_t n) { return Rnd() % n; }

static const uint8_t kBpPool[] = {0x00, 0x40, 0x41, 0x42, 0x43, 0x45, 0x47, 0x48, 0x49, 0x4A, 0x4B, 0x4D, 0x4F, 0x50, 0x52,
                                  0x63, 0x66, 0x67, 0xF3, 0xFE, 0xE0, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE9, 0xEA,
                                  0xEB, 0xEC, 0xED, 0xEE, 0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x2E, 0x2F, 0x80, 0x81, 0x84,
                                  0x88, 0x89, 0x8A, 0x8B, 0x94, 0x95, 0x98, 0x99, 0xA0, 0xA4, 0xA8, 0xB4, 0xB8, 0xC0, 0xC1,
                                  0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xD0, 0xD1, 0xD2, 0xD3, 0xDF};
static const uint8_t kCpPool[] = {0x30, 0x50, 0x60, 0x70, 0x71, 0x80, 0x90, 0xA0, 0xA1, 0xB0, 0xB1};
static const uint16_t kXfPool[] = {0x1000, 0x1001, 0x1009, 0x100A, 0x1010, 0x1012, 0x1020, 0x1021, 0x1026, 0x0000, 0x0010,
                                   0x00FF, 0x0400, 0x0410, 0x045F, 0x0500, 0x0510, 0x0600, 0x0610, 0x067F, 0x1005, 0x10FF};

static void PutU32(std::vector<uint8_t>& o, uint32_t v) { o.push_back(v >> 24); o.push_back(v >> 16); o.push_back(v >> 8); o.push_back(v); }
static void PutU16(std::vector<uint8_t>& o, uint32_t v) { o.push_back(v >> 8); o.push_back(v); }

// Writes a random display-list body into out (no nested calls).
static void GenBody(std::vector<uint8_t>& out, int n) {
    for (int k = 0; k < n; ++k) {
        const uint32_t r = Rnd(100);
        if (r < 30) {
            out.push_back(0x61); out.push_back(kBpPool[Rnd(sizeof kBpPool)]);
            uint32_t v = Rnd() & 0xFFFFFF;
            if (Rnd(8) == 0) v = (v & 0x3FFF) | ((Rnd(2)) ? 0x4000 : 0);
            out.push_back(v >> 16); out.push_back(v >> 8); out.push_back(v);
        } else if (r < 36) {
            out.push_back(0x61); out.push_back(0x64);
            const uint32_t addr = 0x300000 + (Rnd(0x100000) & ~0x1Fu);
            const uint32_t v = addr >> 5;
            out.push_back(v >> 16); out.push_back(v >> 8); out.push_back(v);
        } else if (r < 40) {
            out.push_back(0x61); out.push_back(0x65);
            const uint32_t v = (Rnd(0x3FF)) | ((Rnd(24)) << 10);
            out.push_back(v >> 16); out.push_back(v >> 8); out.push_back(v);
        } else if (r < 48) {
            out.push_back(0x08); out.push_back(kCpPool[Rnd(sizeof kCpPool)]); PutU32(out, Rnd());
        } else if (r < 52) {
            out.push_back(0x78); out.push_back(Rnd(4) == 0 ? 1 : 0);
            out.push_back(Rnd(4) == 0 ? kCpPool[Rnd(sizeof kCpPool)] : kBpPool[Rnd(sizeof kBpPool)]);
            PutU32(out, Rnd()); PutU32(out, Rnd());
        } else if (r < 62) {
            const uint32_t num = 1 + Rnd(4);
            out.push_back(0x10); PutU16(out, num - 1); PutU16(out, kXfPool[Rnd(sizeof kXfPool)] + (Rnd(4) ? 0 : 0));
            for (uint32_t i = 0; i < num; ++i) PutU32(out, Rnd());
        } else if (r < 68) {
            const uint8_t cmd = uint8_t(0x20 + 8 * Rnd(4));
            const uint32_t index = Rnd(64), num = 1 + Rnd(4), addr = (Rnd(2) ? 0x000 : 0x400) + Rnd(0x60);
            out.push_back(cmd); PutU32(out, (index << 16) | ((num - 1) << 12) | (addr & 0xFFF));
        } else if (r < 90) {
            out.push_back(uint8_t(0x80 | (Rnd(8) << 3)));
            const uint32_t count = 1 + Rnd(6);
            PutU16(out, count);
            for (uint32_t i = 0; i < count * 12; ++i) out.push_back(uint8_t(Rnd()));
        } else {
            out.push_back(0x00);
        }
    }
}

// One drain's bytes. Display lists go to RAM at 0x400000 + slot*0x4000 and are called with 0x40.
static void GenBatch(std::vector<uint8_t>& out, int slot) {
    out.clear();
    // fixed VCD / VAT setup on most drains: direct position float, so draws are well formed
    if (Rnd(3)) {
        out.push_back(0x08); out.push_back(0x50); PutU32(out, 0x00000200);
        out.push_back(0x08); out.push_back(0x70); PutU32(out, 0x00000009);
    }
    const int n = 8 + Rnd(40);
    for (int k = 0; k < n; ++k) {
        if (Rnd(12) == 0) {
            std::vector<uint8_t> dl;
            GenBody(dl, 4 + Rnd(10));
            const uint32_t addr = 0x400000 + uint32_t(slot % 16) * 0x4000 + uint32_t(k % 4) * 0x800;
            uint8_t* ram = g_mmu->get_ptr(addr);
            std::memcpy(ram, dl.data(), dl.size());
            out.push_back(0x40); PutU32(out, addr); PutU32(out, uint32_t(dl.size()));
        } else {
            GenBody(out, 1);
        }
    }
}

// ---------------------------------------------------------------- renderer state model
struct Shadow {
    uint32_t bp[256];   // renderer BP shadow (s_native_bp_shadow)
    uint32_t mask;      // one-shot BP mask (s_native_bp_mask)
    uint32_t early[256];
    uint32_t early_mask;
};
static Shadow g_shadow;

// Same rule as RendererGL::NativeBpRegisterValue and the early-signal resolver in gx.cpp.
static uint32_t ResolveBp(uint32_t* sh, uint32_t& mask, uint8_t reg, uint32_t val) {
    const uint32_t resolved = (sh[reg] & ~mask) | (val & mask);
    sh[reg] = resolved & 0xffffff;
    mask = reg == 0xfe ? (resolved & 0xffffff) : 0xffffff;
    return resolved & 0xffffff;
}

static uint64_t Fnv(const void* p, size_t n, uint64_t h) {
    const uint8_t* b = static_cast<const uint8_t*>(p);
    for (size_t i = 0; i < n; ++i) h = (h ^ b[i]) * 1099511628211ull;
    return h;
}
// Digest of every state field the renderer can read; the TLUT block is included only where asked.
static uint64_t Digest(const GXState& g, bool with_tlut) {
    uint64_t h = 1469598103934665603ull;
    h = Fnv(&g, offsetof(GXState, tlutMem), h);
    if (with_tlut) h = Fnv(g.tlutMem, sizeof(g.tlutMem), h);
    return h;
}

// The renderer's command loop, state effects only. `g` is gx::g_state (OLD) or RenderState() (NEW).
static void ApplyBp(GXState& g, uint8_t reg, uint32_t val) {
#ifdef HARNESS_OLD
    (void)g; ApplyBPRegister(reg, val);
#else
    ApplyBPRegisterTo(g, reg, val);
#endif
}

static void RenderLoop(GXState& g, CommandStream& cs, int batch, FILE* out) {
    // NativeRestoreBpSnapshot: the BP words are the renderer's shadow at batch start.
    std::memcpy(g.bp, g_shadow.bp, sizeof(g.bp));
    for (size_t idx = 0; idx < cs.entries.size(); ++idx) {
        StreamEntry& e = cs.entries[idx];
        const uint8_t kind = e.kind;
        if (kind == (uint8_t)GXCommandType::XFRegister) {
            const GXCommand& cmd = cs.heavy[e.heavy];
            const int addr = cmd.reg;
            bool touched = false;
            for (size_t i = 0; i < cmd.payload.size(); ++i) {
                const int cur = addr + (int)i;
                if (cur >= 0x1000 && cur < 0x1000 + 256) std::memcpy(&g.xf[cur - 0x1000], &cmd.payload[i], 4);
                if (cur >= 0x0000 && cur <= 0x00FF) g.posMatrices[cur] = cmd.payload[i];
                else if (cur >= 0x0400 && cur <= 0x045F) g.normalMatrices[cur - 0x0400] = cmd.payload[i];
                else if (cur >= 0x0500 && cur < 0x0600) g.postMatrices[cur - 0x0500] = cmd.payload[i];
                else if (cur >= 0x0600 && cur <= 0x067F) g.lights[(cur - 0x0600) / 16][(cur - 0x0600) % 16] = cmd.payload[i];
                else if (cur >= 0x1020 && cur <= 0x1026) {
                    g.projection[cur - 0x1020] = cmd.payload[i];
                    if (cur == 0x1026) std::memcpy(&g.projType, &cmd.payload[i], 4);
                    touched = true;
                }
            }
            if (touched) g.projSet = true;
        } else if (kind == (uint8_t)GXCommandType::BPRegister) {
            const uint8_t reg = uint8_t(e.reg);
            const uint32_t bp_value = ResolveBp(g_shadow.bp, g_shadow.mask, reg, e.val);
            // Early-signal TLUT snapshot (gx.cpp): the bytes the guest would have read at the BP 0x65 write.
            if (reg == 0x65 && (e.flags & kStreamTlutSnapshot)) {
                const uint32_t dst = (bp_value & 0x3FF) << 9;
                GXCommand* h = cs.Heavy(e);
                if (h && h->native_tlut.size()) std::memcpy(&g.tlutMem[dst], h->native_tlut.data(), h->native_tlut.size());
            } else {
                ApplyBp(g, reg, bp_value);
            }
            if (reg == 0x52) g.pe_clear_pending = false;
            if ((reg == 0x45 && (bp_value & 0xff) == 2) || reg == 0x47 || reg == 0x48)
                fprintf(out, "PE %d %u %02X %06X\n", batch, unsigned(idx), reg, bp_value);
            fprintf(out, "BP %d %u %02X %06X %016llx\n", batch, unsigned(idx), reg, bp_value,
                    (unsigned long long)Digest(g, false));
        } else if (kind == (uint8_t)GXCommandType::DrawPrimitive) {
            fprintf(out, "DRAW %d %u %016llx\n", batch, unsigned(idx), (unsigned long long)Digest(g, true));
        }
    }
    fprintf(out, "END %d %016llx\n", batch, (unsigned long long)Digest(g, true));
}

// The early-signal TLUT snapshot: the heavy record carries the bytes read from RAM at the source address.
static void EarlyTlutSnapshot(CommandStream& cs) {
    for (auto& e : cs.entries) {
        if (e.kind != (uint8_t)GXCommandType::BPRegister) continue;
        const uint8_t reg = uint8_t(e.reg);
        const uint32_t resolved = ResolveBp(g_shadow.early, g_shadow.early_mask, reg, e.val);
        if (reg != 0x65) continue;
        const uint32_t src = (g_shadow.early[0x64] & 0xFFFFFF) << 5;
        const uint32_t dst = (resolved & 0x3FF) << 9, bytes = ((resolved >> 10) & 0x7FF) * 32;
        if (dst + bytes > 0x80000) continue;
        GXCommand* h = cs.Heavy(e);
        if (!h) {
            e.heavy = (uint32_t)cs.heavy.size();
            cs.heavy.emplace_back();
            h = &cs.heavy.back();
            h->type = GXCommandType::BPRegister; h->reg = reg; h->val = e.val;
        }
        h->native_tlut.resize(bytes);
        if (const uint8_t* p = g_mmu->get_ptr(src)) { if (bytes) std::memcpy(h->native_tlut.data(), p, bytes); }
        h->native_tlut_snapshot = true;
        e.flags |= kStreamTlutSnapshot;
    }
}

int main(int argc, char** argv) {
    if (argc < 4) { fprintf(stderr, "usage: harness <seed> <batches> <sync|queued>\n"); return 2; }
    const uint32_t seed = (uint32_t)strtoul(argv[1], nullptr, 10);
    const int batches = atoi(argv[2]);
    const bool queued = strcmp(argv[3], "queued") == 0;
    g_rng ^= uint64_t(seed) * 0x100000001B3ull;
    g_mmu = new MMU();
    {
        uint8_t* ram = g_mmu->get_ptr(0x00000000);
        for (uint32_t i = 0; i < 0x800000; ++i) ram[i] = uint8_t(Rnd());
    }
    std::memset(&g_shadow, 0, sizeof g_shadow);
    g_shadow.mask = 0xffffff; g_shadow.early_mask = 0xffffff;
    FILE* out = stdout;

#ifdef HARNESS_NEW
    if (!queued) {
        for (int b = 0; b < batches; ++b) {
            std::vector<uint8_t> bytes;
            GenBatch(bytes, b);
            CommandStream cs;
            FifoParser::Parse(bytes, cs);
            NativeTakeParseSnapshot(RenderBatchSnapshot());
            EarlyTlutSnapshot(cs);
            RenderStateBegin(RenderBatchSnapshot());
            RenderLoop(RenderState(), cs, b, out);
        }
    } else {
        std::vector<CommandStream> streams(batches);
        std::vector<ParseSnapshot> snaps(batches);
        for (int b = 0; b < batches; ++b) {
            std::vector<uint8_t> bytes;
            GenBatch(bytes, b);
            FifoParser::Parse(bytes, streams[b]);
            NativeTakeParseSnapshot(snaps[b]);
            EarlyTlutSnapshot(streams[b]);
        }
        for (int b = 0; b < batches; ++b) {
            RenderStateBegin(snaps[b]);
            RenderLoop(RenderState(), streams[b], b, out);
        }
    }
#else
    (void)queued;
    for (int b = 0; b < batches; ++b) {
        std::vector<uint8_t> bytes;
        GenBatch(bytes, b);
        CommandStream cs;
        FifoParser::Parse(bytes, cs);
        EarlyTlutSnapshot(cs);
        RenderLoop(gx::g_state, cs, b, out);
    }
#endif
    return 0;
}
