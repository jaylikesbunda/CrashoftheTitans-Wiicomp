// NATIVE_SOURCE_LOWERING_V1
//
// Differential test for the *inferred* native lowerings of
// runtime-overlays/native_lower_inferred.h.
//
// These two guest functions have no recovered source.  Their behaviour was
// reconstructed from the translated body and the PPC mnemonics the translator
// records on every line, so the oracle is the translated body itself: this
// test runs the translated function (extracted from the generated chunk, with
// its memory accesses routed to the harness recorder) and the native kernel on
// identical input states and compares
//
//   * the ordered guest memory-effect log,
//   * the ordered GX hardware port log,
//   * the guest RAM in the ranges the two paths touched (widened), which is
//     what covers the paired-single stores the recorder cannot see,
//   * the *whole* register file: gpr[0..31], fpr[0..31], ps1[0..31],
//     CR0..CR7, LR, CTR, XER, PC, MSR and FPSCR - no register is allowed to
//     differ, because the native path publishes every register the translated
//     body writes,
//   * the retired guest instruction count.
//
// The cases cover ordinary inputs, the branch boundaries of each comparison,
// NaN / inf / denormal / signed zero, address wrapping, aliasing between the
// output and the inputs, and (for 0x8028199C) the paired-single GQR load/store
// types.
#include "runtime/cpu_context.h"
#include "runtime/native_guest_fast.h"
#include "runtime/native_fastmem.h"
#include "native_lowering_harness.h"

#if __has_include("guest_lowering_extracted.h")
#include "guest_lowering_extracted.h"
namespace nl = nwii::runtime::native_lower_test;
#else
// A stand-alone build (no extracted header): the same bodies the driver
// extracts, already routed to the harness recorder. The copies are named
// `translated_*` and only aliased to the guest names here, so the driver
// still extracts the real bodies (and can drop a candidate from the
// manifest without breaking this file).
#define func_802880E8 translated_802880E8
#define func_8028199C translated_8028199C
namespace nl = nwii::runtime::native_lower_test;
using namespace nwii::runtime;
// The same two bodies the driver extracts, copied verbatim (with the
// `ctx.mmu.<access>` calls already rewritten to `nl::rec_<access>`), so this
// test also builds and runs stand-alone.
// ---- extracted from the generated chunk (storage/crash-generated) ----
void translated_802880E8(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x802880E8, ctx);
    if (ctx.pc != 0x802880E8) {
        native_perf_census::GuestSwitch(ctx.pc,30); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x802880EC: goto loc_802880EC;
            case 0x802880F4: goto loc_802880F4;
            case 0x80288100: goto loc_80288100;
            case 0x80288108: goto loc_80288108;
            case 0x8028810C: goto loc_8028810C;
            case 0x80288110: goto loc_80288110;
            case 0x80288114: goto loc_80288114;
            case 0x80288118: goto loc_80288118;
            case 0x80288124: goto loc_80288124;
            case 0x8028812C: goto loc_8028812C;
            case 0x80288130: goto loc_80288130;
            case 0x80288134: goto loc_80288134;
            case 0x80288138: goto loc_80288138;
            case 0x8028813C: goto loc_8028813C;
            case 0x80288148: goto loc_80288148;
            case 0x80288150: goto loc_80288150;
            case 0x80288154: goto loc_80288154;
            case 0x80288158: goto loc_80288158;
            case 0x8028815C: goto loc_8028815C;
            case 0x80288160: goto loc_80288160;
            case 0x80288164: goto loc_80288164;
            case 0x80288168: goto loc_80288168;
            case 0x8028816C: goto loc_8028816C;
            case 0x80288170: goto loc_80288170;
            case 0x80288174: goto loc_80288174;
            case 0x80288178: goto loc_80288178;
            case 0x8028817C: goto loc_8028817C;
            case 0x80288180: goto loc_80288180;
            case 0x80288184: goto loc_80288184;
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_802880E8: ;
    // 0x802880E8: C0230000
    if (ctx.require_fpu(0x802880E8)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 0); // lfs
    ++ctx.inst_count;
loc_802880EC: ;
    ctx.inst_count += 1;
    // 0x802880EC: C002B0C8
    if (ctx.require_fpu(0x802880EC)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[2] + -20280); // lfs
loc_802880F0: ;
    // 0x802880F0: 9421FFE0
    {
        uint32_t ea = ctx.gpr[1] + -32;
/* NATIVE_V13_CODEGEN_V1 ICAgICAgICBjdHgubW11LndyaXRlMzIoZWEsIGN0eC5ncHJbMV0pOwo= */        nl::rec_write32(ctx.mmu, ea, ctx.gpr[1]);
        ctx.gpr[1] = ea;
    } // stwu r1, -32(r1)
    ++ctx.inst_count;
loc_802880F4: ;
    ctx.inst_count += 2;
    // 0x802880F4: FC010040
    if (ctx.require_fpu(0x802880F4)) { ctx.inst_count -= 2; return; } // MSR.FP exception
    {
        bool un = std::isnan(ctx.fpr[1]) || std::isnan(ctx.fpr[0]);
        ctx.cr[0].lt = !un && (ctx.fpr[1] < ctx.fpr[0]);
        ctx.cr[0].gt = !un && (ctx.fpr[1] > ctx.fpr[0]);
        ctx.cr[0].eq = !un && (ctx.fpr[1] == ctx.fpr[0]);
        ctx.cr[0].so = un;
    }
loc_802880F8: ;
    // 0x802880F8: 4C411382
    ctx.cr[0].eq = ctx.cr[0].gt | ctx.cr[0].eq;
loc_802880FC: ;
    // 0x802880FC: 4082000C
    if (!ctx.cr[0].eq) goto loc_80288108;
    ++ctx.inst_count;
loc_80288100: ;
    ctx.inst_count += 1;
    // 0x80288100: C0440000
    if (ctx.require_fpu(0x80288100)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 0); // lfs
loc_80288104: ;
    // 0x80288104: 48000008
    goto loc_8028810C;
    ++ctx.inst_count;
loc_80288108: ;
    // 0x80288108: C044000C
    if (ctx.require_fpu(0x80288108)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 12); // lfs
    ++ctx.inst_count;
loc_8028810C: ;
    // 0x8028810C: C0230004
    if (ctx.require_fpu(0x8028810C)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 4); // lfs
    ++ctx.inst_count;
loc_80288110: ;
    // 0x80288110: C002B0C8
    if (ctx.require_fpu(0x80288110)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[2] + -20280); // lfs
    ++ctx.inst_count;
loc_80288114: ;
    // 0x80288114: D0410008
    if (ctx.require_fpu(0x80288114)) return; // MSR.FP exception
/* NATIVE_V13_CODEGEN_V1 ICAgIGN0eC5tbXUud3JpdGVfZjMyKGN0eC5ncHJbMV0gKyA4LCAoZmxvYXQpY3R4LmZwclsyXSk7IC8vIHN0ZnMgZjIsIDgocjEpCg== */    nl::rec_write_f32(ctx.mmu, ctx.gpr[1] + 8, (float)ctx.fpr[2]); // stfs f2, 8(r1)
    ++ctx.inst_count;
loc_80288118: ;
    ctx.inst_count += 2;
    // 0x80288118: FC010040
    if (ctx.require_fpu(0x80288118)) { ctx.inst_count -= 2; return; } // MSR.FP exception
    {
        bool un = std::isnan(ctx.fpr[1]) || std::isnan(ctx.fpr[0]);
        ctx.cr[0].lt = !un && (ctx.fpr[1] < ctx.fpr[0]);
        ctx.cr[0].gt = !un && (ctx.fpr[1] > ctx.fpr[0]);
        ctx.cr[0].eq = !un && (ctx.fpr[1] == ctx.fpr[0]);
        ctx.cr[0].so = un;
    }
loc_8028811C: ;
    // 0x8028811C: 4C411382
    ctx.cr[0].eq = ctx.cr[0].gt | ctx.cr[0].eq;
loc_80288120: ;
    // 0x80288120: 4082000C
    if (!ctx.cr[0].eq) goto loc_8028812C;
    ++ctx.inst_count;
loc_80288124: ;
    ctx.inst_count += 1;
    // 0x80288124: C0440004
    if (ctx.require_fpu(0x80288124)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 4); // lfs
loc_80288128: ;
    // 0x80288128: 48000008
    goto loc_80288130;
    ++ctx.inst_count;
loc_8028812C: ;
    // 0x8028812C: C0440010
    if (ctx.require_fpu(0x8028812C)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 16); // lfs
    ++ctx.inst_count;
loc_80288130: ;
    // 0x80288130: C0230008
    if (ctx.require_fpu(0x80288130)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 8); // lfs
    ++ctx.inst_count;
loc_80288134: ;
    // 0x80288134: C002B0C8
    if (ctx.require_fpu(0x80288134)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[2] + -20280); // lfs
    ++ctx.inst_count;
loc_80288138: ;
    // 0x80288138: D041000C
    if (ctx.require_fpu(0x80288138)) return; // MSR.FP exception
/* NATIVE_V13_CODEGEN_V1 ICAgIGN0eC5tbXUud3JpdGVfZjMyKGN0eC5ncHJbMV0gKyAxMiwgKGZsb2F0KWN0eC5mcHJbMl0pOyAvLyBzdGZzIGYyLCAxMihyMSkK */    nl::rec_write_f32(ctx.mmu, ctx.gpr[1] + 12, (float)ctx.fpr[2]); // stfs f2, 12(r1)
    ++ctx.inst_count;
loc_8028813C: ;
    ctx.inst_count += 2;
    // 0x8028813C: FC010040
    if (ctx.require_fpu(0x8028813C)) { ctx.inst_count -= 2; return; } // MSR.FP exception
    {
        bool un = std::isnan(ctx.fpr[1]) || std::isnan(ctx.fpr[0]);
        ctx.cr[0].lt = !un && (ctx.fpr[1] < ctx.fpr[0]);
        ctx.cr[0].gt = !un && (ctx.fpr[1] > ctx.fpr[0]);
        ctx.cr[0].eq = !un && (ctx.fpr[1] == ctx.fpr[0]);
        ctx.cr[0].so = un;
    }
loc_80288140: ;
    // 0x80288140: 4C411382
    ctx.cr[0].eq = ctx.cr[0].gt | ctx.cr[0].eq;
loc_80288144: ;
    // 0x80288144: 4082000C
    if (!ctx.cr[0].eq) goto loc_80288150;
    ++ctx.inst_count;
loc_80288148: ;
    ctx.inst_count += 1;
    // 0x80288148: C0C40008
    if (ctx.require_fpu(0x80288148)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[6] = ctx.ps1[6] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 8); // lfs
loc_8028814C: ;
    // 0x8028814C: 48000008
    goto loc_80288154;
    ++ctx.inst_count;
loc_80288150: ;
    // 0x80288150: C0C40014
    if (ctx.require_fpu(0x80288150)) return; // MSR.FP exception
    ctx.fpr[6] = ctx.ps1[6] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 20); // lfs
    ++ctx.inst_count;
loc_80288154: ;
    // 0x80288154: C061000C
    if (ctx.require_fpu(0x80288154)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.ps1[3] = nl::rec_read_f32(ctx.mmu, ctx.gpr[1] + 12); // lfs
    ++ctx.inst_count;
loc_80288158: ;
    // 0x80288158: FC403018
    if (ctx.require_fpu(0x80288158)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = (double)(float)ctx.fpr[6]; // frsp
    ++ctx.inst_count;
loc_8028815C: ;
    // 0x8028815C: C0230004
    if (ctx.require_fpu(0x8028815C)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 4); // lfs
    ++ctx.inst_count;
loc_80288160: ;
    // 0x80288160: C003000C
    if (ctx.require_fpu(0x80288160)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 12); // lfs
    ++ctx.inst_count;
loc_80288164: ;
    // 0x80288164: ECA30072
    if (ctx.require_fpu(0x80288164)) return; // MSR.FP exception
    ctx.fpr[5] = ctx.ps1[5] = (float)(ctx.fpr[3] * ctx.fpr[1]); // fmuls
    ++ctx.inst_count;
loc_80288168: ;
    // 0x80288168: C0810008
    if (ctx.require_fpu(0x80288168)) return; // MSR.FP exception
    ctx.fpr[4] = ctx.ps1[4] = nl::rec_read_f32(ctx.mmu, ctx.gpr[1] + 8); // lfs
    ++ctx.inst_count;
loc_8028816C: ;
    // 0x8028816C: C0630000
    if (ctx.require_fpu(0x8028816C)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.ps1[3] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 0); // lfs
    ++ctx.inst_count;
loc_80288170: ;
    // 0x80288170: FC000050
    if (ctx.require_fpu(0x80288170)) return; // MSR.FP exception
    ctx.fpr[0] = -ctx.fpr[0]; // fneg
    ++ctx.inst_count;
loc_80288174: ;
    // 0x80288174: C0230008
    if (ctx.require_fpu(0x80288174)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 8); // lfs
    ++ctx.inst_count;
loc_80288178: ;
    // 0x80288178: EC6428FA
    if (ctx.require_fpu(0x80288178)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.ps1[3] = (float)std::fma(ctx.fpr[4], ctx.fpr[3], ctx.fpr[5]); // fmadds
    ++ctx.inst_count;
loc_8028817C: ;
    // 0x8028817C: D0C10010
    if (ctx.require_fpu(0x8028817C)) return; // MSR.FP exception
/* NATIVE_V13_CODEGEN_V1 ICAgIGN0eC5tbXUud3JpdGVfZjMyKGN0eC5ncHJbMV0gKyAxNiwgKGZsb2F0KWN0eC5mcHJbNl0pOyAvLyBzdGZzIGY2LCAxNihyMSkK */    nl::rec_write_f32(ctx.mmu, ctx.gpr[1] + 16, (float)ctx.fpr[6]); // stfs f6, 16(r1)
    ++ctx.inst_count;
loc_80288180: ;
    // 0x80288180: EC22187A
    if (ctx.require_fpu(0x80288180)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = (float)std::fma(ctx.fpr[2], ctx.fpr[1], ctx.fpr[3]); // fmadds
    ++ctx.inst_count;
loc_80288184: ;
    ctx.inst_count += 5;
    // 0x80288184: FC010040
    if (ctx.require_fpu(0x80288184)) { ctx.inst_count -= 5; return; } // MSR.FP exception
    {
        bool un = std::isnan(ctx.fpr[1]) || std::isnan(ctx.fpr[0]);
        ctx.cr[0].lt = !un && (ctx.fpr[1] < ctx.fpr[0]);
        ctx.cr[0].gt = !un && (ctx.fpr[1] > ctx.fpr[0]);
        ctx.cr[0].eq = !un && (ctx.fpr[1] == ctx.fpr[0]);
        ctx.cr[0].so = un;
    }
loc_80288188: ;
    // 0x80288188: 4C401382
    ctx.cr[0].eq = ctx.cr[0].lt | ctx.cr[0].eq;
loc_8028818C: ;
    // 0x8028818C: 7C600026
    {
        uint32_t cr_val = 0;
        for (int i = 0; i < 8; ++i) {
            cr_val |= (ctx.cr[i].lt ? 8 : 0) << (28 - i * 4);
            cr_val |= (ctx.cr[i].gt ? 4 : 0) << (28 - i * 4);
            cr_val |= (ctx.cr[i].eq ? 2 : 0) << (28 - i * 4);
            cr_val |= (ctx.cr[i].so ? 1 : 0) << (28 - i * 4);
        }
        ctx.gpr[3] = cr_val;
    }
loc_80288190: ;
    // 0x80288190: 54631FFE
    ctx.gpr[3] = std::rotl(ctx.gpr[3], 3) & 0x1; // rlwinm r3, r3, 3, 31, 31
loc_80288194: ;
    // 0x80288194: 38210020
    ctx.gpr[1] = ctx.gpr[1] + 32; // addi r1, r1, 32
loc_80288198: ;
    // 0x80288198: 4E800020
    ctx.pc = ctx.lr; return;
}

// ---- extracted from the generated chunk (storage/crash-generated) ----
void translated_8028199C(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x8028199C, ctx);
    if (ctx.pc != 0x8028199C) {
        native_perf_census::GuestSwitch(ctx.pc,56); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x802819A0: goto loc_802819A0;
            case 0x802819AC: goto loc_802819AC;
            case 0x802819B0: goto loc_802819B0;
            case 0x802819B4: goto loc_802819B4;
            case 0x802819B8: goto loc_802819B8;
            case 0x802819BC: goto loc_802819BC;
            case 0x802819C0: goto loc_802819C0;
            case 0x802819C8: goto loc_802819C8;
            case 0x802819CC: goto loc_802819CC;
            case 0x802819D0: goto loc_802819D0;
            case 0x802819D4: goto loc_802819D4;
            case 0x802819DC: goto loc_802819DC;
            case 0x802819E0: goto loc_802819E0;
            case 0x802819E4: goto loc_802819E4;
            case 0x802819E8: goto loc_802819E8;
            case 0x802819EC: goto loc_802819EC;
            case 0x802819F0: goto loc_802819F0;
            case 0x802819F4: goto loc_802819F4;
            case 0x802819F8: goto loc_802819F8;
            case 0x802819FC: goto loc_802819FC;
            case 0x80281A00: goto loc_80281A00;
            case 0x80281A04: goto loc_80281A04;
            case 0x80281A08: goto loc_80281A08;
            case 0x80281A0C: goto loc_80281A0C;
            case 0x80281A10: goto loc_80281A10;
            case 0x80281A18: goto loc_80281A18;
            case 0x80281A1C: goto loc_80281A1C;
            case 0x80281A20: goto loc_80281A20;
            case 0x80281A24: goto loc_80281A24;
            case 0x80281A28: goto loc_80281A28;
            case 0x80281A2C: goto loc_80281A2C;
            case 0x80281A30: goto loc_80281A30;
            case 0x80281A34: goto loc_80281A34;
            case 0x80281A38: goto loc_80281A38;
            case 0x80281A3C: goto loc_80281A3C;
            case 0x80281A40: goto loc_80281A40;
            case 0x80281A44: goto loc_80281A44;
            case 0x80281A48: goto loc_80281A48;
            case 0x80281A4C: goto loc_80281A4C;
            case 0x80281A50: goto loc_80281A50;
            case 0x80281A54: goto loc_80281A54;
            case 0x80281A58: goto loc_80281A58;
            case 0x80281A5C: goto loc_80281A5C;
            case 0x80281A60: goto loc_80281A60;
            case 0x80281A64: goto loc_80281A64;
            case 0x80281A68: goto loc_80281A68;
            case 0x80281A6C: goto loc_80281A6C;
            case 0x80281A70: goto loc_80281A70;
            case 0x80281A74: goto loc_80281A74;
            case 0x80281A78: goto loc_80281A78;
            case 0x80281A7C: goto loc_80281A7C;
            case 0x80281A80: goto loc_80281A80;
            case 0x80281A84: goto loc_80281A84;
            case 0x80281A88: goto loc_80281A88;
            case 0x80281A8C: goto loc_80281A8C;
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_8028199C: ;
    // 0x8028199C: 7C002A2C
    // sync/cache instruction
    ++ctx.inst_count;
loc_802819A0: ;
    ctx.inst_count += 2;
    // 0x802819A0: FD6B5828
    if (ctx.require_fpu(0x802819A0)) { ctx.inst_count -= 2; return; } // MSR.FP exception
    ctx.fpr[11] = ctx.fpr[11] - ctx.fpr[11]; // fsub
loc_802819A4: ;
    // 0x802819A4: 7C0019EC
    // sync/cache instruction
loc_802819A8: ;
    // 0x802819A8: 38C00020
    ctx.gpr[6] = 32; // li r6, 32
    ++ctx.inst_count;
loc_802819AC: ;
    // 0x802819AC: E0040000
    if (ctx.require_fpu(0x802819AC)) return; // MSR.FP exception
    ctx.psq_load(0, ctx.gpr[4] + 0, 0, 0); // psq_l
    ++ctx.inst_count;
loc_802819B0: ;
    // 0x802819B0: C0240008
    if (ctx.require_fpu(0x802819B0)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 8); // lfs
    ++ctx.inst_count;
loc_802819B4: ;
    // 0x802819B4: E0450000
    if (ctx.require_fpu(0x802819B4)) return; // MSR.FP exception
    ctx.psq_load(2, ctx.gpr[5] + 0, 0, 0); // psq_l
    ++ctx.inst_count;
loc_802819B8: ;
    // 0x802819B8: C0650008
    if (ctx.require_fpu(0x802819B8)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.ps1[3] = nl::rec_read_f32(ctx.mmu, ctx.gpr[5] + 8); // lfs
    ++ctx.inst_count;
loc_802819BC: ;
    // 0x802819BC: E0850010
    if (ctx.require_fpu(0x802819BC)) return; // MSR.FP exception
    ctx.psq_load(4, ctx.gpr[5] + 16, 0, 0); // psq_l
    ++ctx.inst_count;
loc_802819C0: ;
    ctx.inst_count += 1;
    // 0x802819C0: C0A50018
    if (ctx.require_fpu(0x802819C0)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[5] = ctx.ps1[5] = nl::rec_read_f32(ctx.mmu, ctx.gpr[5] + 24); // lfs
loc_802819C4: ;
    // 0x802819C4: 7C062A2C
    // sync/cache instruction
    ++ctx.inst_count;
loc_802819C8: ;
    // 0x802819C8: 11820018
    if (ctx.require_fpu(0x802819C8)) return; // MSR.FP exception
    ctx.ps_muls0(12, 2, 0);
    ++ctx.inst_count;
loc_802819CC: ;
    // 0x802819CC: E0C50020
    if (ctx.require_fpu(0x802819CC)) return; // MSR.FP exception
    ctx.psq_load(6, ctx.gpr[5] + 32, 0, 0); // psq_l
    ++ctx.inst_count;
loc_802819D0: ;
    // 0x802819D0: 11A30018
    if (ctx.require_fpu(0x802819D0)) return; // MSR.FP exception
    ctx.ps_muls0(13, 3, 0);
    ++ctx.inst_count;
loc_802819D4: ;
    ctx.inst_count += 1;
    // 0x802819D4: C0E50028
    if (ctx.require_fpu(0x802819D4)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[7] = ctx.ps1[7] = nl::rec_read_f32(ctx.mmu, ctx.gpr[5] + 40); // lfs
loc_802819D8: ;
    // 0x802819D8: 7C06222C
    // sync/cache instruction
    ++ctx.inst_count;
loc_802819DC: ;
    // 0x802819DC: 1184601E
    if (ctx.require_fpu(0x802819DC)) return; // MSR.FP exception
    ctx.ps_madds1(12, 4, 0, 12);
    ++ctx.inst_count;
loc_802819E0: ;
    // 0x802819E0: E1050030
    if (ctx.require_fpu(0x802819E0)) return; // MSR.FP exception
    ctx.psq_load(8, ctx.gpr[5] + 48, 0, 0); // psq_l
    ++ctx.inst_count;
loc_802819E4: ;
    // 0x802819E4: 11A5681E
    if (ctx.require_fpu(0x802819E4)) return; // MSR.FP exception
    ctx.ps_madds1(13, 5, 0, 13);
    ++ctx.inst_count;
loc_802819E8: ;
    // 0x802819E8: E1258038
    if (ctx.require_fpu(0x802819E8)) return; // MSR.FP exception
    ctx.psq_load(9, ctx.gpr[5] + 56, 1, 0); // psq_l
    ++ctx.inst_count;
loc_802819EC: ;
    // 0x802819EC: 1186605C
    if (ctx.require_fpu(0x802819EC)) return; // MSR.FP exception
    ctx.ps_madds0(12, 6, 1, 12);
    ++ctx.inst_count;
loc_802819F0: ;
    // 0x802819F0: E0040010
    if (ctx.require_fpu(0x802819F0)) return; // MSR.FP exception
    ctx.psq_load(0, ctx.gpr[4] + 16, 0, 0); // psq_l
    ++ctx.inst_count;
loc_802819F4: ;
    // 0x802819F4: 11A7685C
    if (ctx.require_fpu(0x802819F4)) return; // MSR.FP exception
    ctx.ps_madds0(13, 7, 1, 13);
    ++ctx.inst_count;
loc_802819F8: ;
    // 0x802819F8: C0240018
    if (ctx.require_fpu(0x802819F8)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 24); // lfs
    ++ctx.inst_count;
loc_802819FC: ;
    // 0x802819FC: F1830000
    if (ctx.require_fpu(0x802819FC)) return; // MSR.FP exception
    ctx.psq_store(12, ctx.gpr[3] + 0, 0, 0); // psq_st
    ++ctx.inst_count;
loc_80281A00: ;
    // 0x80281A00: 11820018
    if (ctx.require_fpu(0x80281A00)) return; // MSR.FP exception
    ctx.ps_muls0(12, 2, 0);
    ++ctx.inst_count;
loc_80281A04: ;
    // 0x80281A04: F1A38008
    if (ctx.require_fpu(0x80281A04)) return; // MSR.FP exception
    ctx.psq_store(13, ctx.gpr[3] + 8, 1, 0); // psq_st
    ++ctx.inst_count;
loc_80281A08: ;
    // 0x80281A08: 11A30018
    if (ctx.require_fpu(0x80281A08)) return; // MSR.FP exception
    ctx.ps_muls0(13, 3, 0);
    ++ctx.inst_count;
loc_80281A0C: ;
    // 0x80281A0C: D163000C
    if (ctx.require_fpu(0x80281A0C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[3] + 12, (float)ctx.fpr[11]); // stfs f11, 12(r3)
    ++ctx.inst_count;
loc_80281A10: ;
    ctx.inst_count += 1;
    // 0x80281A10: 1184601E
    if (ctx.require_fpu(0x80281A10)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.ps_madds1(12, 4, 0, 12);
loc_80281A14: ;
    // 0x80281A14: 7C0619EC
    // sync/cache instruction
    ++ctx.inst_count;
loc_80281A18: ;
    // 0x80281A18: 11A5681E
    if (ctx.require_fpu(0x80281A18)) return; // MSR.FP exception
    ctx.ps_madds1(13, 5, 0, 13);
    ++ctx.inst_count;
loc_80281A1C: ;
    // 0x80281A1C: E0040020
    if (ctx.require_fpu(0x80281A1C)) return; // MSR.FP exception
    ctx.psq_load(0, ctx.gpr[4] + 32, 0, 0); // psq_l
    ++ctx.inst_count;
loc_80281A20: ;
    // 0x80281A20: 1186605C
    if (ctx.require_fpu(0x80281A20)) return; // MSR.FP exception
    ctx.ps_madds0(12, 6, 1, 12);
    ++ctx.inst_count;
loc_80281A24: ;
    // 0x80281A24: 11A7685C
    if (ctx.require_fpu(0x80281A24)) return; // MSR.FP exception
    ctx.ps_madds0(13, 7, 1, 13);
    ++ctx.inst_count;
loc_80281A28: ;
    // 0x80281A28: C0240028
    if (ctx.require_fpu(0x80281A28)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 40); // lfs
    ++ctx.inst_count;
loc_80281A2C: ;
    // 0x80281A2C: F1830010
    if (ctx.require_fpu(0x80281A2C)) return; // MSR.FP exception
    ctx.psq_store(12, ctx.gpr[3] + 16, 0, 0); // psq_st
    ++ctx.inst_count;
loc_80281A30: ;
    // 0x80281A30: 11820018
    if (ctx.require_fpu(0x80281A30)) return; // MSR.FP exception
    ctx.ps_muls0(12, 2, 0);
    ++ctx.inst_count;
loc_80281A34: ;
    // 0x80281A34: F1A38018
    if (ctx.require_fpu(0x80281A34)) return; // MSR.FP exception
    ctx.psq_store(13, ctx.gpr[3] + 24, 1, 0); // psq_st
    ++ctx.inst_count;
loc_80281A38: ;
    // 0x80281A38: 11A30018
    if (ctx.require_fpu(0x80281A38)) return; // MSR.FP exception
    ctx.ps_muls0(13, 3, 0);
    ++ctx.inst_count;
loc_80281A3C: ;
    // 0x80281A3C: D163001C
    if (ctx.require_fpu(0x80281A3C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[3] + 28, (float)ctx.fpr[11]); // stfs f11, 28(r3)
    ++ctx.inst_count;
loc_80281A40: ;
    // 0x80281A40: 1184601E
    if (ctx.require_fpu(0x80281A40)) return; // MSR.FP exception
    ctx.ps_madds1(12, 4, 0, 12);
    ++ctx.inst_count;
loc_80281A44: ;
    // 0x80281A44: 11A5681E
    if (ctx.require_fpu(0x80281A44)) return; // MSR.FP exception
    ctx.ps_madds1(13, 5, 0, 13);
    ++ctx.inst_count;
loc_80281A48: ;
    // 0x80281A48: E0040030
    if (ctx.require_fpu(0x80281A48)) return; // MSR.FP exception
    ctx.psq_load(0, ctx.gpr[4] + 48, 0, 0); // psq_l
    ++ctx.inst_count;
loc_80281A4C: ;
    // 0x80281A4C: 1186605C
    if (ctx.require_fpu(0x80281A4C)) return; // MSR.FP exception
    ctx.ps_madds0(12, 6, 1, 12);
    ++ctx.inst_count;
loc_80281A50: ;
    // 0x80281A50: 11A7685C
    if (ctx.require_fpu(0x80281A50)) return; // MSR.FP exception
    ctx.ps_madds0(13, 7, 1, 13);
    ++ctx.inst_count;
loc_80281A54: ;
    // 0x80281A54: C0240038
    if (ctx.require_fpu(0x80281A54)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 56); // lfs
    ++ctx.inst_count;
loc_80281A58: ;
    // 0x80281A58: F1830020
    if (ctx.require_fpu(0x80281A58)) return; // MSR.FP exception
    ctx.psq_store(12, ctx.gpr[3] + 32, 0, 0); // psq_st
    ++ctx.inst_count;
loc_80281A5C: ;
    // 0x80281A5C: 11820018
    if (ctx.require_fpu(0x80281A5C)) return; // MSR.FP exception
    ctx.ps_muls0(12, 2, 0);
    ++ctx.inst_count;
loc_80281A60: ;
    // 0x80281A60: F1A38028
    if (ctx.require_fpu(0x80281A60)) return; // MSR.FP exception
    ctx.psq_store(13, ctx.gpr[3] + 40, 1, 0); // psq_st
    ++ctx.inst_count;
loc_80281A64: ;
    // 0x80281A64: 11A30018
    if (ctx.require_fpu(0x80281A64)) return; // MSR.FP exception
    ctx.ps_muls0(13, 3, 0);
    ++ctx.inst_count;
loc_80281A68: ;
    // 0x80281A68: 1184601E
    if (ctx.require_fpu(0x80281A68)) return; // MSR.FP exception
    ctx.ps_madds1(12, 4, 0, 12);
    ++ctx.inst_count;
loc_80281A6C: ;
    // 0x80281A6C: 11A5681E
    if (ctx.require_fpu(0x80281A6C)) return; // MSR.FP exception
    ctx.ps_madds1(13, 5, 0, 13);
    ++ctx.inst_count;
loc_80281A70: ;
    // 0x80281A70: D163002C
    if (ctx.require_fpu(0x80281A70)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[3] + 44, (float)ctx.fpr[11]); // stfs f11, 44(r3)
    ++ctx.inst_count;
loc_80281A74: ;
    // 0x80281A74: 1186605C
    if (ctx.require_fpu(0x80281A74)) return; // MSR.FP exception
    ctx.ps_madds0(12, 6, 1, 12);
    ++ctx.inst_count;
loc_80281A78: ;
    // 0x80281A78: 11A7685C
    if (ctx.require_fpu(0x80281A78)) return; // MSR.FP exception
    ctx.ps_madds0(13, 7, 1, 13);
    ++ctx.inst_count;
loc_80281A7C: ;
    // 0x80281A7C: 118C402A
    if (ctx.require_fpu(0x80281A7C)) return; // MSR.FP exception
    ctx.ps_add(12, 12, 8);
    ++ctx.inst_count;
loc_80281A80: ;
    // 0x80281A80: 11AD482A
    if (ctx.require_fpu(0x80281A80)) return; // MSR.FP exception
    ctx.ps_add(13, 13, 9);
    ++ctx.inst_count;
loc_80281A84: ;
    // 0x80281A84: 11AD4C60
    if (ctx.require_fpu(0x80281A84)) return; // MSR.FP exception
    ctx.ps_merge01(13, 13, 9);
    ++ctx.inst_count;
loc_80281A88: ;
    // 0x80281A88: F1830030
    if (ctx.require_fpu(0x80281A88)) return; // MSR.FP exception
    ctx.psq_store(12, ctx.gpr[3] + 48, 0, 0); // psq_st
    ++ctx.inst_count;
loc_80281A8C: ;
    ctx.inst_count += 1;
    // 0x80281A8C: F1A30038
    if (ctx.require_fpu(0x80281A8C)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.psq_store(13, ctx.gpr[3] + 56, 0, 0); // psq_st
loc_80281A90: ;
    // 0x80281A90: 4E800020
    ctx.pc = ctx.lr; return;
}
#endif

#if __has_include("native_lower_inferred.h")
#include "native_lower_inferred.h"
#else
#include "../runtime-overlays/native_lower_inferred.h"
#endif

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>
#include <string>
#include <vector>

namespace nwii::runtime {
void GX_WGPIPE_Write8(uint8_t v) { native_lower_test::record_port(0xCC008000, 1, v); }
void GX_WGPIPE_Write16(uint16_t v) { native_lower_test::record_port(0xCC008000, 2, v); }
void GX_WGPIPE_Write32(uint32_t v) { native_lower_test::record_port(0xCC008000, 4, v); }
void GX_WGPIPE_WriteF32(float v) { uint32_t b; std::memcpy(&b, &v, 4); native_lower_test::record_port(0xCC008000, 4, b); }
void GX_WGPIPE_WriteF64(double v) { uint64_t b; std::memcpy(&b, &v, 8); native_lower_test::record_port(0xCC008000, 8, b); }
void GX_DL_Snapshot(uint32_t, uint32_t) {}
bool GX_DL_Fetch(uint32_t, uint32_t, std::vector<uint8_t>&) { return false; }
bool native_gx_emitter_ready() { return false; }
uint32_t HW_Reg_Read32(uint32_t) { return 0; }
uint16_t HW_Reg_Read16(uint32_t) { return 0; }
uint8_t HW_Reg_Read8(uint32_t) { return 0; }
void HW_Reg_Write16(uint32_t a, uint16_t v) { native_lower_test::record_port(a, 2, v); }
void HW_Reg_Write32(uint32_t a, uint32_t v) { native_lower_test::record_port(a, 4, v); }
bool g_native_fifo_ok = false;
bool process_pending_callbacks(CPUContext&) { return false; }
int32_t g_native_poll_credit = 0;
void watch_hit(uint32_t, uint32_t, int) {}
uint32_t g_watch_addr = 0;
bool g_trace_calls = false;
void trace_call(uint32_t, CPUContext&) {}
void interpret_step(CPUContext&) {}
}  // namespace nwii::runtime

namespace nm = nwii::runtime::native_lower;
using namespace nwii::runtime;

namespace {

// ---------------------------------------------------------------------------
// Harness helpers
// ---------------------------------------------------------------------------
CPUContext g_a;
CPUContext g_b;

volatile uint64_t g_sink = 0;

void reset_state(CPUContext& ctx) {
  for (int i = 0; i < 32; ++i) {
    ctx.gpr[i] = 0;
    ctx.fpr[i] = 0.0;
    ctx.ps1[i] = 0.0;
  }
  for (int i = 0; i < 8; ++i) {
    ctx.cr[i] = nwii::runtime::ConditionField();
    ctx.gqr[i] = 0;
  }
  ctx.lr = 0;
  ctx.ctr = 0;
  ctx.xer = 0;
  ctx.pc = 0;
  ctx.msr = 0;
  ctx.fpscr = 0;
  ctx.inst_count = 0;
}

void put32(CPUContext& ctx, uint32_t address, uint32_t value) { ctx.mmu.write32(address, value); }

float bits_to_f32(uint32_t bits) {
  float f;
  std::memcpy(&f, &bits, 4);
  return f;
}
uint32_t f32_to_bits(float f) {
  uint32_t b;
  std::memcpy(&b, &f, 4);
  return b;
}
double bits_to_f64(uint64_t bits) {
  double d;
  std::memcpy(&d, &bits, 8);
  return d;
}
uint64_t f64_to_bits(double d) {
  uint64_t b;
  std::memcpy(&b, &d, 8);
  return b;
}

// A RAM range clamped to MEM1 so the comparison never reads past the array.
nl::Region mem1_region(uint32_t lo, uint32_t hi) {
  const uint32_t limit = nwii::runtime::MMU::kMem1Limit;
  lo &= 0x3fffffffu;
  hi &= 0x3fffffffu;
  if (lo > hi) std::swap(lo, hi);
  if (lo > limit) lo = limit;
  if (hi > limit) hi = limit;
  return nl::Region{lo, hi};
}

struct Group {
  const char* name;
  int cases = 0;
  int failures = 0;
};

Group g_groups[8];
int g_group_count = 0;

Group& group(const char* name) {
  for (int i = 0; i < g_group_count; ++i)
    if (std::strcmp(g_groups[i].name, name) == 0) return g_groups[i];
  g_groups[g_group_count].name = name;
  return g_groups[g_group_count++];
}

// Runs the translated function and the native lowering on identical states and
// compares everything the guest can observe.  `extra` adds RAM ranges the
// effect log cannot show (the paired-single accesses).
template <typename Setup, typename Translated, typename Native>
void run_pair(Group& grp, const std::string& label, const nl::VolatileSet& vol,
              Setup setup, Translated translated, Native native,
              const std::vector<nl::Region>& extra = {}) {
  reset_state(g_a);
  reset_state(g_b);
  setup(g_a);
  setup(g_b);

  nl::g_effects.clear();
  nl::g_ports.clear();
  nl::g_recording = true;
  translated(g_a);
  const std::vector<nl::Effect> original = nl::g_effects.entries;
  const std::vector<nl::Effect> original_ports = nl::g_ports.entries;

  nl::g_effects.clear();
  nl::g_ports.clear();
  native(g_b);
  const std::vector<nl::Effect> lowered = nl::g_effects.entries;
  const std::vector<nl::Effect> lowered_ports = nl::g_ports.entries;
  nl::g_recording = false;

  nl::Difference d = nl::compare(nl::capture(g_a), nl::capture(g_b), vol);
  nl::Difference e;
#ifdef NATIVE_LOWERING_TRACE_MEMORY
  // Both paths record through the harness, so the ordered effect and port logs
  // must be identical.  The production build has no recorder on the native
  // side (GuestMem's trace compiles out), so only the state, the RAM and the
  // instruction count are compared there; that build is for the timing.
  e = nl::compare_effects(original, lowered, "effect");
  if (e.any) d.note(e.text);
  e = nl::compare_effects(original_ports, lowered_ports, "port");
  if (e.any) d.note(e.text);
#endif
  std::vector<nl::Region> regions = nl::touched_regions(original, lowered);
  for (const nl::Region& r : extra) regions.push_back(mem1_region(r.lo, r.hi));
  e = nl::compare_ram(g_a.mmu, g_b.mmu, regions);
  if (e.any) d.note(e.text);

  ++grp.cases;
  if (d.any) {
    ++grp.failures;
    if (grp.failures <= 4) std::printf("FAIL %s [%s]\n%s", grp.name, label.c_str(), d.text.c_str());
  }
}

// ===========================================================================
// 0x802880E8 - inferred leaf, three float compares and a short FMA chain
// ===========================================================================
void test_802880e8() {
  Group& grp = group("inferred.apply_802880e8");
  // Nothing is allowed to differ: the native path publishes r3, f0..f6,
  // ps1 0..6 and CR0, which is exactly what the translated body writes.
  nl::VolatileSet vol;

  const uint32_t kSp = 0x00400000u;
  const uint32_t kSda = 0x00590000u;         // r2; only r2 - 20280 is read
  const uint32_t kZero = kSda - 20280u;
  const uint32_t kA = 0x00410000u;
  const uint32_t kB = 0x00410040u;
  const uint32_t kLr = 0x80010000u;

  const uint32_t inf = 0x7F800000u, ninf = 0xFF800000u;
  const uint32_t qnan = 0x7FC00000u, snan = 0x7F800001u, negnan = 0xFFC00001u;

  struct Case {
    uint32_t zero_bits;      // the SDA constant
    uint32_t a[4];
    uint32_t b[6];
    uint32_t sp;
    uint32_t a_addr, b_addr;
  };

  auto setup = [&](const Case& c) {
    return [&, c](CPUContext& ctx) {
      reset_state(ctx);
      ctx.gpr[1] = c.sp;
      ctx.gpr[2] = kSda;
      ctx.gpr[3] = c.a_addr;
      ctx.gpr[4] = c.b_addr;
      for (int i = 5; i < 32; ++i) ctx.gpr[i] = 0x10000000u + uint32_t(i) * 0x01010101u;
      for (int i = 0; i < 32; ++i) {
        ctx.fpr[i] = bits_to_f64(0x3FF0000000000000ull + uint64_t(i) * 0x0000100000000001ull);
        ctx.ps1[i] = bits_to_f64(0xBFF0000000000000ull - uint64_t(i) * 0x0000010000000003ull);
      }
      for (int i = 1; i < 8; ++i) {
        ctx.cr[i].lt = (i & 1) != 0;
        ctx.cr[i].gt = (i & 2) != 0;
        ctx.cr[i].eq = (i & 4) != 0;
        ctx.cr[i].so = (i & 3) != 0;
      }
      ctx.msr = 0x2000;                      // MSR.FP, the guard the entry tests
      ctx.fpscr = 0x0004A123u;
      ctx.xer = 0x12345678u;
      ctx.ctr = 0x00424240u;
      ctx.lr = kLr;
      ctx.pc = 0x802880E8u;
      ctx.inst_count = 0x1000u;
      // Wipe the scratch and the inputs so a previous case cannot leak in.
      for (uint32_t p = kSp - 64u; p < kSp + 16u; p += 4u) put32(ctx, p, 0xCCCCCCCCu);
      for (uint32_t p = kA; p < kA + 16u; p += 4u) put32(ctx, p, 0xDDDDDDDDu);
      for (uint32_t p = kB; p < kB + 24u; p += 4u) put32(ctx, p, 0xEEEEEEEEu);
      put32(ctx, kZero, c.zero_bits);
      for (int i = 0; i < 4; ++i) put32(ctx, c.a_addr + uint32_t(i) * 4u, c.a[i]);
      for (int i = 0; i < 6; ++i) put32(ctx, c.b_addr + uint32_t(i) * 4u, c.b[i]);
    };
  };

  auto run = [&](const char* label, const Case& c) {
    run_pair(grp, label, vol, setup(c),
             [](CPUContext& ctx) { func_802880E8(ctx); },
             [](CPUContext& ctx) { nm::apply_802880e8(ctx); },
             {mem1_region(c.sp - 40u, c.sp + 8u), mem1_region(kZero - 4u, kZero + 8u),
              mem1_region(c.a_addr - 4u, c.a_addr + 24u),
              mem1_region(c.b_addr - 4u, c.b_addr + 32u)});
  };

  // The three branch boundaries.  `zero` is 0.0f, so +1.0 / 0.0 / -1.0 / NaN
  // select GT / EQ / LT / unordered independently for each compare.
  const uint32_t probes[4] = {0x3F800000u, 0x00000000u, 0xBF800000u, qnan};
  const char* probe_name[4] = {"gt", "eq", "lt", "nan"};
  int combo = 0;
  for (int p0 = 0; p0 < 4; ++p0) {
    for (int p1 = 0; p1 < 4; ++p1) {
      for (int p2 = 0; p2 < 4; ++p2) {
        for (int p3 = 0; p3 < 3; ++p3) {
          Case c{};
          c.zero_bits = 0x00000000u;
          c.a[0] = probes[p0];
          c.a[1] = probes[p1];
          c.a[2] = probes[p2];
          c.a[3] = (p3 == 0) ? 0x3F800000u : (p3 == 1) ? 0xBF800000u : 0x00000001u;
          c.b[0] = 0x40000000u; c.b[1] = 0xC0000000u; c.b[2] = 0x3F000000u;
          c.b[3] = 0x40400000u; c.b[4] = 0xC0400000u; c.b[5] = 0xBF000000u;
          c.sp = kSp; c.a_addr = kA; c.b_addr = kB;
          char label[96];
          std::snprintf(label, sizeof(label), "boundary %s/%s/%s/%d", probe_name[p0],
                        probe_name[p1], probe_name[p2], p3);
          run(label, c);
          ++combo;
        }
      }
    }
  }

  // Signed zero, denormals, infinities and NaN payloads in every slot.
  const uint32_t specials[] = {0x00000000u, 0x80000000u, 0x00000001u, 0x80000001u,
                               0x007FFFFFu, 0x00800000u, 0x7F7FFFFFu, 0xFF7FFFFFu,
                               inf, ninf, qnan, snan, negnan};
  const int n_special = int(sizeof(specials) / sizeof(specials[0]));
  for (int i = 0; i < n_special; ++i) {
    for (int j = 0; j < n_special; ++j) {
      Case c{};
      c.zero_bits = specials[(i + j) % n_special];
      for (int k = 0; k < 4; ++k) c.a[k] = specials[(i * 3 + k * 5) % n_special];
      for (int k = 0; k < 6; ++k) c.b[k] = specials[(j * 7 + k * 2) % n_special];
      c.a[0] = specials[i];
      c.a[1] = specials[j];
      c.sp = kSp; c.a_addr = kA; c.b_addr = kB;
      char label[96];
      std::snprintf(label, sizeof(label), "special %08X/%08X", c.zero_bits, c.a[0]);
      run(label, c);
    }
  }

  // Wrapping and unaligned stack pointers.
  {
    const uint32_t sps[] = {0x00000010u, 0x00000000u, 0x00010000u, 0x017FFFF0u,
                            0xFFFFFFFFu, 0x80000000u};
    for (uint32_t sp : sps) {
      Case c{};
      c.zero_bits = 0x00000000u;
      for (int k = 0; k < 4; ++k) c.a[k] = specials[k];
      for (int k = 0; k < 6; ++k) c.b[k] = specials[k + 3];
      c.sp = sp; c.a_addr = kA; c.b_addr = kB;
      char label[96];
      std::snprintf(label, sizeof(label), "sp %08X", sp);
      run(label, c);
    }
  }

  // The output vector and the stack scratch overlap the input vectors, so the
  // order of the accesses (not just their values) decides the result.
  {
    struct Alias { uint32_t a_off, b_off; const char* label; };
    const Alias aliases[] = {
        {0u, 0u, "a in the frame, b in the frame"},
        {24u, 28u, "output lands in a[1] and a[2]"},
        {16u, 20u, "output lands in a[0]"},
        {8u, 12u, "back chain overlaps a[0]"},
    };
    for (const Alias& al : aliases) {
      Case c{};
      c.zero_bits = 0x00000000u;
      c.a[0] = 0x3F800000u; c.a[1] = 0xBF800000u; c.a[2] = 0x40000000u; c.a[3] = 0xC0000000u;
      for (int k = 0; k < 6; ++k) c.b[k] = 0x3F000000u + uint32_t(k);
      c.sp = kSp;
      c.a_addr = kSp - 32u + al.a_off;
      c.b_addr = kSp - 32u + al.b_off;
      char label[96];
      std::snprintf(label, sizeof(label), "alias %s", al.label);
      run(label, c);
    }
  }

  // Random cases: an even mix of random bit patterns and the special values.
  std::mt19937 rng(0x80288E8u);
  for (int i = 0; i < 320; ++i) {
    Case c{};
    auto pick = [&]() -> uint32_t {
      return (rng() & 1) ? rng() : specials[rng() % n_special];
    };
    c.zero_bits = pick();
    for (int k = 0; k < 4; ++k) c.a[k] = pick();
    for (int k = 0; k < 6; ++k) c.b[k] = pick();
    c.sp = kSp;
    c.a_addr = kA;
    c.b_addr = kB;
    char label[64];
    std::snprintf(label, sizeof(label), "random %d", i);
    run(label, c);
  }
}

// ===========================================================================
// 0x8028199C - inferred paired-single 3x4 transform of four points
// ===========================================================================
void test_8028199c() {
  Group& grp = group("inferred.apply_8028199c");
  nl::VolatileSet vol;

  const uint32_t kDest = 0x00420000u;
  const uint32_t kPoints = 0x00421000u;
  const uint32_t kBasis = 0x00422000u;
  const uint32_t kLr = 0x80010000u;

  const uint32_t inf = 0x7F800000u, ninf = 0xFF800000u;
  const uint32_t qnan = 0x7FC00000u, snan = 0x7F800001u;
  const uint32_t specials[] = {0x00000000u, 0x80000000u, 0x00000001u, 0x80000001u,
                               0x007FFFFFu, 0x00800000u, 0x7F7FFFFFu, 0xFF7FFFFFu,
                               inf, ninf, qnan, snan, 0x3F800000u, 0xBF800000u,
                               0x40000000u, 0xC0400000u};
  const int n_special = int(sizeof(specials) / sizeof(specials[0]));

  struct Case {
    uint32_t gqr0;
    uint32_t dest, points, basis;
    uint32_t points_bits[16];
    uint32_t basis_bits[16];
    uint32_t dest_bits[16];
    uint64_t f11_bits;
    uint32_t gpr6;
  };

  auto setup = [&](const Case& c) {
    return [&, c](CPUContext& ctx) {
      reset_state(ctx);
      ctx.gpr[1] = 0x00400000u;
      ctx.gpr[2] = 0x00590000u;
      ctx.gpr[3] = c.dest;
      ctx.gpr[4] = c.points;
      ctx.gpr[5] = c.basis;
      ctx.gpr[6] = c.gpr6;
      for (int i = 7; i < 32; ++i) ctx.gpr[i] = 0x20000000u + uint32_t(i) * 0x01010101u;
      // Every FP register starts at a distinctive value.  f11 is the only FP
      // input of the function; the rest are dead and must stay untouched
      // unless the guest writes them.
      for (int i = 0; i < 32; ++i) {
        ctx.fpr[i] = bits_to_f64(0x4000000000000000ull + uint64_t(i) * 0x0000000100000007ull);
        ctx.ps1[i] = bits_to_f64(0xC000000000000000ull + uint64_t(i) * 0x0000000300000011ull);
      }
      ctx.fpr[11] = bits_to_f64(c.f11_bits);
      for (int i = 1; i < 8; ++i) {
        ctx.cr[i].lt = (i & 1) != 0;
        ctx.cr[i].gt = (i & 2) != 0;
        ctx.cr[i].eq = (i & 4) != 0;
        ctx.cr[i].so = (i & 3) != 0;
      }
      // GQR0 drives every psq_l/psq_st of this function; the other GQRs are
      // unused but set to a noisy value.
      ctx.gqr[0] = c.gqr0;
      for (int i = 1; i < 8; ++i) ctx.gqr[i] = 0x01020304u * uint32_t(i);
      ctx.msr = 0x2000;
      ctx.fpscr = 0x0004A123u;
      ctx.xer = 0x12345678u;
      ctx.ctr = 0x00424240u;
      ctx.lr = kLr;
      ctx.pc = 0x8028199Cu;
      ctx.inst_count = 0x2000u;
      for (uint32_t p = kDest; p < kDest + 64u; p += 4u) put32(ctx, p, 0xCCCCCCCCu);
      for (uint32_t p = kPoints; p < kPoints + 64u; p += 4u) put32(ctx, p, 0xDDDDDDDDu);
      for (uint32_t p = kBasis; p < kBasis + 64u; p += 4u) put32(ctx, p, 0xEEEEEEEEu);
      for (int i = 0; i < 16; ++i) {
        put32(ctx, c.dest + uint32_t(i) * 4u, c.dest_bits[i]);
        put32(ctx, c.points + uint32_t(i) * 4u, c.points_bits[i]);
        put32(ctx, c.basis + uint32_t(i) * 4u, c.basis_bits[i]);
      }
    };
  };

  auto regions = [&](const Case& c) {
    return std::vector<nl::Region>{mem1_region(c.dest, c.dest + 64u),
                                   mem1_region(c.points, c.points + 64u),
                                   mem1_region(c.basis, c.basis + 64u)};
  };

  auto run = [&](const char* label, const Case& c) {
    run_pair(grp, label, vol, setup(c),
             [](CPUContext& ctx) { func_8028199C(ctx); },
             [](CPUContext& ctx) { nm::apply_8028199c(ctx); }, regions(c));
  };

  // An ordinary transform: a 3x4 basis with a translation row, four points.
  auto ordinary = [&](Case& c) {
    for (int i = 0; i < 16; ++i) {
      c.basis_bits[i] = f32_to_bits(0.25f * float(i) - 1.0f);
      c.points_bits[i] = f32_to_bits(1.5f * float(i) - 3.0f);
      c.dest_bits[i] = 0x7F7F7F7Fu;
    }
    c.gqr0 = 0;
    c.dest = kDest;
    c.points = kPoints;
    c.basis = kBasis;
    c.f11_bits = 0;
    c.gpr6 = 0;
  };

  {
    Case c{};
    ordinary(c);
    c.f11_bits = f64_to_bits(0.0);
    run("ordinary, f11 = +0", c);
    c.f11_bits = f64_to_bits(-0.0);
    run("ordinary, f11 = -0", c);
    c.f11_bits = f64_to_bits(1.0);
    run("ordinary, f11 = 1", c);
    c.f11_bits = 0x7FF8000000000001ull;
    run("ordinary, f11 = qNaN", c);
    c.f11_bits = 0x7FF0000000000000ull;
    run("ordinary, f11 = +inf", c);
    c.f11_bits = 0x8000000000000001ull;
    run("ordinary, f11 = -denormal", c);
    c.f11_bits = f64_to_bits(3.25);
    c.gpr6 = 0xDEADBEEFu;
    run("ordinary, r6 scratch written", c);
  }

  // GQR0 load/store types and scales.  The kernel goes through the runtime's
  // own psq_l/psq_st so every type must agree without a guard.
  {
    const uint32_t gqrs[] = {
        0x00000000u,              // float / float, scale 0
        0x00040000u,              // ld u8
        0x00050000u,              // ld u16
        0x00060000u,              // ld s8
        0x00070000u,              // ld s16
        0x00000004u,              // st u8
        0x00000005u,              // st u16
        0x00000006u,              // st s8
        0x00000007u,              // st s16
        0x00040004u,              // u8 / u8
        0x00070007u,              // s16 / s16
        0x01000000u | 0x00040004u,  // ld scale 1
        0x00000100u | 0x00000005u,  // st scale 1
        0x0F000000u | 0x00060006u,  // ld scale 15
        0x00000F00u | 0x00000007u,  // st scale 15
        0x00010000u,              // reserved load type 1
        0x00030000u,              // reserved load type 3
        0x00000001u,              // reserved store type 1
        0x00000003u,              // reserved store type 3
        0xFFFFFFFFu,              // every field set
    };
    for (uint32_t gqr : gqrs) {
      Case c{};
      ordinary(c);
      c.gqr0 = gqr;
      c.f11_bits = f64_to_bits(1.5);
      for (int i = 0; i < 16; ++i) {
        c.basis_bits[i] = specials[(i * 5) % n_special];
        c.points_bits[i] = specials[(i * 3 + 1) % n_special];
      }
      char label[64];
      std::snprintf(label, sizeof(label), "gqr0 %08X", gqr);
      run(label, c);
    }
  }

  // NaN / inf / denormal / signed zero in every input slot.
  for (int i = 0; i < n_special; ++i) {
    Case c{};
    ordinary(c);
    for (int k = 0; k < 16; ++k) {
      c.basis_bits[k] = specials[(i + k) % n_special];
      c.points_bits[k] = specials[(i * 2 + k * 3) % n_special];
    }
    c.f11_bits = (i & 1) ? 0x7FF8000000000000ull : 0;
    char label[64];
    std::snprintf(label, sizeof(label), "special %d", i);
    run(label, c);
  }

  // Aliasing: the destination overlaps the points and/or the basis.  The
  // translated body's store/load order is what decides the result.
  {
    struct Alias { uint32_t dest_off; const char* label; };
    const Alias aliases[] = {
        {0u, "dest == points"},
        {8u, "dest = points + 8"},
        {32u, "dest = points + 32"},
        {0x1000u, "dest == basis"},
        {0x1010u, "dest = basis + 16"},
        {0x1020u, "dest = basis + 32"},
        {0x1040u, "dest = basis + 64 (past the basis)"},
    };
    for (const Alias& al : aliases) {
      Case c{};
      ordinary(c);
      c.dest = (al.dest_off < 0x1000u) ? kPoints + al.dest_off : kBasis + (al.dest_off - 0x1000u);
      char label[96];
      std::snprintf(label, sizeof(label), "alias %s", al.label);
      run(label, c);
    }
  }

  // Address boundaries: the last bytes of MEM1, the mirror page, RAM page 0
  // and an address outside every RAM window.
  {
    const uint32_t dests[] = {0x017FFFFCu, 0x017FFFF8u, 0x017FFFC0u, 0x0000FFF0u,
                              0xCC008000u, 0x20000000u, 0x00000010u};
    for (uint32_t d : dests) {
      Case c{};
      ordinary(c);
      c.dest = d;
      char label[64];
      std::snprintf(label, sizeof(label), "dest %08X", d);
      run(label, c);
    }
  }

  // Random cases.
  std::mt19937 rng(0x802199Cu);
  const uint32_t gqr_choices[] = {0u, 0x00040004u, 0x00050005u, 0x00060006u,
                                  0x00070007u, 0x01000000u, 0x00000100u};
  const uint64_t f11_choices[] = {0ull, 0x8000000000000000ull, 0x3FF8000000000000ull,
                                  0x7FF8000000000000ull, 0x7FF0000000000000ull,
                                  0x0000000000000001ull, 0xBFF0000000000000ull};
  for (int i = 0; i < 320; ++i) {
    Case c{};
    auto pick = [&]() -> uint32_t {
      return (rng() & 1) ? rng() : specials[rng() % n_special];
    };
    for (int k = 0; k < 16; ++k) {
      c.basis_bits[k] = pick();
      c.points_bits[k] = pick();
      c.dest_bits[k] = pick();
    }
    c.gqr0 = gqr_choices[rng() % (sizeof(gqr_choices) / sizeof(gqr_choices[0]))];
    c.f11_bits = f11_choices[rng() % (sizeof(f11_choices) / sizeof(f11_choices[0]))];
    c.dest = kDest;
    c.points = kPoints;
    c.basis = kBasis;
    c.gpr6 = rng();
    char label[64];
    std::snprintf(label, sizeof(label), "random %d", i);
    run(label, c);
  }
}

// ===========================================================================
// Paired micro-benchmark
//
// The translated function and the native lowering run alternately on the same
// input state; the results are consumed so the compiler cannot delete the
// work.  These are local component measurements (host nanoseconds per call),
// not a whole-game frame rate.
// ===========================================================================
template <typename F>
double sample_ns(F&& body, int iterations) {
  const auto t0 = std::chrono::steady_clock::now();
  for (int i = 0; i < iterations; ++i) {
    asm volatile("" ::: "memory");
    body();
  }
  const auto t1 = std::chrono::steady_clock::now();
  return std::chrono::duration<double, std::nano>(t1 - t0).count() / iterations;
}

double median_of(std::vector<double> samples) {
  std::sort(samples.begin(), samples.end());
  return samples[samples.size() / 2];
}

template <typename Setup, typename Translated, typename Native>
void benchmark(const char* name, uint32_t address, Setup setup, Translated translated,
               Native native, const char* note) {
  reset_state(g_a);
  setup(g_a);
  nl::g_recording = false;

  const int kIterations = 20000;
  const int kRepeats = 9;

  g_a.pc = address;
  const uint64_t before = g_a.inst_count;
  translated(g_a);
  const uint64_t translated_insts = g_a.inst_count - before;
  g_a.pc = address;
  const uint64_t before2 = g_a.inst_count;
  native(g_a);
  const uint64_t native_insts = g_a.inst_count - before2;

  for (int i = 0; i < 2000; ++i) {
    g_a.pc = address;
    translated(g_a);
    g_a.pc = address;
    native(g_a);
  }

  std::vector<double> translated_samples;
  std::vector<double> native_samples;
  for (int r = 0; r < kRepeats; ++r) {
    translated_samples.push_back(sample_ns([&] {
      g_a.pc = address;
      translated(g_a);
      g_sink += g_a.gpr[3];
    }, kIterations));
    native_samples.push_back(sample_ns([&] {
      g_a.pc = address;
      native(g_a);
      g_sink += g_a.gpr[3];
    }, kIterations));
  }
  const double translated_ns = median_of(translated_samples);
  const double native_ns = median_of(native_samples);
  std::printf("BENCH %-14s translated %8.1f ns/call   native %8.1f ns/call   speedup %5.2fx   "
              "guest insts %llu/%llu   %s\n",
              name, translated_ns, native_ns,
              native_ns > 0.0 ? translated_ns / native_ns : 0.0,
              (unsigned long long)translated_insts, (unsigned long long)native_insts, note);
}

}  // namespace

int main() {
#ifndef NATIVE_LOWERING_TRACE_MEMORY
  std::printf("NATIVE_LOWERING_TESTS note: built without NATIVE_LOWERING_TRACE_MEMORY; the ordered "
              "effect and port logs are not compared in this build (it is the paired-timing "
              "configuration), only the register file, the RAM and the instruction count.\n");
#endif
  test_802880e8();
  test_8028199c();

  int failures = 0;
  for (int i = 0; i < g_group_count; ++i) {
    Group& g = g_groups[i];
    std::printf("NATIVE_LOWERING_TESTS %s %d/%d\n", g.name, g.cases - g.failures, g.cases);
    failures += g.failures;
  }
  if (failures) std::printf("NATIVE_LOWERING_TESTS FAILED\n");

  // Paired micro-benchmark.
  {
    reset_state(g_a);
    g_a.gpr[1] = 0x00400000u;
    g_a.gpr[2] = 0x00590000u;
    g_a.gpr[3] = 0x00410000u;
    g_a.gpr[4] = 0x00410040u;
    g_a.msr = 0x2000;
    put32(g_a, 0x00590000u - 20280u, 0x00000000u);
    put32(g_a, 0x00410000u + 0u, 0x3F800000u);
    put32(g_a, 0x00410000u + 4u, 0x40000000u);
    put32(g_a, 0x00410000u + 8u, 0x40400000u);
    put32(g_a, 0x00410000u + 12u, 0x40800000u);
    put32(g_a, 0x00410040u + 0u, 0x3F000000u);
    put32(g_a, 0x00410040u + 4u, 0x3E800000u);
    put32(g_a, 0x00410040u + 8u, 0xBE800000u);
    put32(g_a, 0x00410040u + 12u, 0x3F400000u);
    put32(g_a, 0x00410040u + 16u, 0xBF400000u);
    put32(g_a, 0x00410040u + 20u, 0x3F600000u);
    benchmark("802880E8", 0x802880E8u,
              [](CPUContext& ctx) {
                ctx.lr = 0x80010000u;
                ctx.msr = 0x2000;   // MSR.FP, the entry guard
                ctx.gpr[1] = 0x00400000u;
                ctx.gpr[2] = 0x00590000u;
                ctx.gpr[3] = 0x00410000u;
                ctx.gpr[4] = 0x00410040u;
              },
              func_802880E8, nm::apply_802880e8,
              "4 reads + 3 stack writes + 4 writes, 3 branches");
  }
  {
    reset_state(g_a);
    g_a.gpr[3] = 0x00420000u;
    g_a.gpr[4] = 0x00421000u;
    g_a.gpr[5] = 0x00422000u;
    g_a.msr = 0x2000;
    g_a.fpr[11] = 0.0;
    for (int i = 0; i < 16; ++i) {
      put32(g_a, 0x00421000u + uint32_t(i) * 4u, f32_to_bits(1.5f * float(i) - 3.0f));
      put32(g_a, 0x00422000u + uint32_t(i) * 4u, f32_to_bits(0.25f * float(i) - 1.0f));
      put32(g_a, 0x00420000u + uint32_t(i) * 4u, 0u);
    }
    benchmark("8028199C", 0x8028199Cu,
              [](CPUContext& ctx) {
                ctx.lr = 0x80010000u;
                ctx.msr = 0x2000;
                ctx.gpr[3] = 0x00420000u;
                ctx.gpr[4] = 0x00421000u;
                ctx.gpr[5] = 0x00422000u;
                ctx.fpr[11] = 0.0;
              },
              func_8028199C, nm::apply_8028199c,
              "16 loads + 11 stores, 17 psq ops");
  }
  std::printf("BENCH sink %llu\n", (unsigned long long)g_sink);
  std::printf("NATIVE_LOWERING_BENCH sink %llu\n", (unsigned long long)g_sink);
  return failures == 0 ? 0 : 1;
}
