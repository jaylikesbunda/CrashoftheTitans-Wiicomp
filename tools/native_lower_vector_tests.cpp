// NATIVE_SOURCE_LOWERING_V1
//
// Differential test for the source-backed native lowerings of
// runtime-overlays/native_lower_vector.h.
//
// The translated guest function is the oracle.  The driver
// (tools/check_native_lowering.py) extracts it from the generated chunk,
// routes its memory accesses to the harness recorder and writes
// guest_lowering_extracted.h; when that header is not available (a
// stand-alone build) the same extracted bodies are used from the fallback
// below.
//
// For every candidate the test runs the translated function and the native
// lowering on identical input states and compares
//
//   * the ordered guest memory effect log,
//   * the ordered GX hardware port log,
//   * the guest RAM in the ranges the two paths touched,
//   * the final guest register file, condition register, LR/CTR/XER/PC/MSR/
//     FPSCR and the retired guest instruction count.
#include "runtime/cpu_context.h"
#include "runtime/native_guest_fast.h"
#include "runtime/native_fastmem.h"
#include "native_lowering_harness.h"

// The callees of the vector group: the translated bodies of 0x80286D08
// (RadicalMathLibrary::Matrix::Transform) and 0x80286E28
// (RadicalMathLibrary::Matrix::RotateVector).  The plain-leaf stage has
// already replaced the *definitions* of both in the generated tree with its
// own kernel wrappers, so the test provides the translated bodies itself and
// the group kernels are compared against them.
void func_80286D08(nwii::runtime::CPUContext& ctx);
void func_80286E28(nwii::runtime::CPUContext& ctx);

#if __has_include("guest_lowering_extracted.h")
#include "guest_lowering_extracted.h"
#else
namespace nl = nwii::runtime::native_lower_test;
using namespace nwii::runtime;

// ---- extracted from the generated tree: 80219D40 ----
NWII_GUEST_ABI void func_80219D40(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x80219D40, ctx);
    if (ctx.pc != 0x80219D40) {
        native_perf_census::GuestSwitch(ctx.pc,1); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_80219D40: ;
    ctx.inst_count += 3;
    // 0x80219D40: 5480103A
    ctx.gpr[0] = std::rotl(ctx.gpr[4], 2) & 0xFFFFFFFC; // rlwinm r0, r4, 2, 0, 29
loc_80219D44: ;
    // 0x80219D44: 7C630214
    ctx.gpr[3] = ctx.gpr[3] + ctx.gpr[0];
loc_80219D48: ;
    // 0x80219D48: 8063001C
    ctx.gpr[3] = nl::rec_read32(ctx.mmu, ctx.gpr[3] + 28); // lwz r3, 28(r3)
loc_80219D4C: ;
    // 0x80219D4C: 4E800020
    ctx.pc = ctx.lr; return;
}

// ---- extracted from the generated tree: 8026B00C ----
NWII_GUEST_ABI void func_8026B00C(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x8026B00C, ctx);
    if (ctx.pc != 0x8026B00C) {
        native_perf_census::GuestSwitch(ctx.pc,1); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_8026B00C: ;
    ctx.inst_count += 3;
    // 0x8026B00C: 7C601B78
    ctx.gpr[0] = ctx.gpr[3]; // mr r0, r3
loc_8026B010: ;
    // 0x8026B010: 806DD1C8
    ctx.gpr[3] = nl::rec_read32(ctx.mmu, ctx.gpr[13] + -11832); // lwz r3, -11832(r13)
loc_8026B014: ;
    // 0x8026B014: 900DD1C8
    nl::rec_write32(ctx.mmu, ctx.gpr[13] + -11832, ctx.gpr[0]); // stw r0, -11832(r13)
loc_8026B018: ;
    // 0x8026B018: 4E800020
    ctx.pc = ctx.lr; return;
}

// ---- extracted from the generated tree: 8028669C ----
NWII_GUEST_ABI void func_8028669C(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x8028669C, ctx);
    if (ctx.pc != 0x8028669C) {
        native_perf_census::GuestSwitch(ctx.pc,6); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x802866A0: goto loc_802866A0;
            case 0x802866A4: goto loc_802866A4;
            case 0x802866A8: goto loc_802866A8;
            case 0x802866AC: goto loc_802866AC;
            case 0x802866B0: goto loc_802866B0;
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_8028669C: ;
    // 0x8028669C: C0440000
    if (ctx.require_fpu(0x8028669C)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 0); // lfs
    ++ctx.inst_count;
loc_802866A0: ;
    // 0x802866A0: C0240004
    if (ctx.require_fpu(0x802866A0)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 4); // lfs
    ++ctx.inst_count;
loc_802866A4: ;
    // 0x802866A4: C0040008
    if (ctx.require_fpu(0x802866A4)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 8); // lfs
    ++ctx.inst_count;
loc_802866A8: ;
    // 0x802866A8: D0430030
    if (ctx.require_fpu(0x802866A8)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[3] + 48, (float)ctx.fpr[2]); // stfs f2, 48(r3)
    ++ctx.inst_count;
loc_802866AC: ;
    // 0x802866AC: D0230034
    if (ctx.require_fpu(0x802866AC)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[3] + 52, (float)ctx.fpr[1]); // stfs f1, 52(r3)
    ++ctx.inst_count;
loc_802866B0: ;
    ctx.inst_count += 1;
    // 0x802866B0: D0030038
    if (ctx.require_fpu(0x802866B0)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[3] + 56, (float)ctx.fpr[0]); // stfs f0, 56(r3)
loc_802866B4: ;
    // 0x802866B4: 4E800020
    ctx.pc = ctx.lr; return;
}

// ---- extracted from the generated tree: 8028A914 ----
NWII_GUEST_ABI void func_8028A914(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x8028A914, ctx);
    if (ctx.pc != 0x8028A914) {
        native_perf_census::GuestSwitch(ctx.pc,7); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x8028A938: goto loc_8028A938;
            case 0x8028A93C: goto loc_8028A93C;
            case 0x8028A940: goto loc_8028A940;
            case 0x8028A944: goto loc_8028A944;
            case 0x8028A948: goto loc_8028A948;
            case 0x8028A94C: goto loc_8028A94C;
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_8028A914: ;
    ctx.inst_count += 8;
    // 0x8028A914: 9421FFE0
    {
        uint32_t ea = ctx.gpr[1] + -32;
/* NATIVE_V13_CODEGEN_V1 ICAgICAgICBjdHgubW11LndyaXRlMzIoZWEsIGN0eC5ncHJbMV0pOwo= */        nl::rec_write32(ctx.mmu, ea, ctx.gpr[1]);
        ctx.gpr[1] = ea;
    } // stwu r1, -32(r1)
loc_8028A918: ;
    // 0x8028A918: 7C0802A6
    ctx.gpr[0] = ctx.lr; // mflr r0
loc_8028A91C: ;
    // 0x8028A91C: 90010024
/* NATIVE_V13_CODEGEN_V1 ICAgIGN0eC5tbXUud3JpdGUzMihjdHguZ3ByWzFdICsgMzYsIGN0eC5ncHJbMF0pOyAvLyBzdHcgcjAsIDM2KHIxKQo= */    nl::rec_write32(ctx.mmu, ctx.gpr[1] + 36, ctx.gpr[0]); // stw r0, 36(r1)
loc_8028A920: ;
    // 0x8028A920: 38A10008
    ctx.gpr[5] = ctx.gpr[1] + 8; // addi r5, r1, 8
loc_8028A924: ;
    // 0x8028A924: 93E1001C
/* NATIVE_V13_CODEGEN_V1 ICAgIGN0eC5tbXUud3JpdGUzMihjdHguZ3ByWzFdICsgMjgsIGN0eC5ncHJbMzFdKTsgLy8gc3R3IHIzMSwgMjgocjEpCg== */    nl::rec_write32(ctx.mmu, ctx.gpr[1] + 28, ctx.gpr[31]); // stw r31, 28(r1)
loc_8028A928: ;
    // 0x8028A928: 7C7F1B78
    ctx.gpr[31] = ctx.gpr[3]; // mr r31, r3
loc_8028A92C: ;
    // 0x8028A92C: 7C832378
    ctx.gpr[3] = ctx.gpr[4]; // mr r3, r4
loc_8028A930: ;
    // 0x8028A930: 7FE4FB78
    ctx.gpr[4] = ctx.gpr[31]; // mr r4, r31
loc_8028A934: ;
    // 0x8028A934: 4BFFC3D5
    ctx.lr = 0x8028A938; // save return address
    ctx.pc = 0x80286D08; func_80286D08(ctx);
    if (ctx.pc != 0x8028A938) return;
    ++ctx.inst_count;
loc_8028A938: ;
    // 0x8028A938: C0010008
    if (ctx.require_fpu(0x8028A938)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[1] + 8); // lfs
    ++ctx.inst_count;
loc_8028A93C: ;
    // 0x8028A93C: D01F0000
    if (ctx.require_fpu(0x8028A93C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[31] + 0, (float)ctx.fpr[0]); // stfs f0, 0(r31)
    ++ctx.inst_count;
loc_8028A940: ;
    // 0x8028A940: C001000C
    if (ctx.require_fpu(0x8028A940)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[1] + 12); // lfs
    ++ctx.inst_count;
loc_8028A944: ;
    // 0x8028A944: D01F0004
    if (ctx.require_fpu(0x8028A944)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[31] + 4, (float)ctx.fpr[0]); // stfs f0, 4(r31)
    ++ctx.inst_count;
loc_8028A948: ;
    // 0x8028A948: C0010010
    if (ctx.require_fpu(0x8028A948)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[1] + 16); // lfs
    ++ctx.inst_count;
loc_8028A94C: ;
    ctx.inst_count += 5;
    // 0x8028A94C: D01F0008
    if (ctx.require_fpu(0x8028A94C)) { ctx.inst_count -= 5; return; } // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[31] + 8, (float)ctx.fpr[0]); // stfs f0, 8(r31)
loc_8028A950: ;
    // 0x8028A950: 83E1001C
    ctx.gpr[31] = nl::rec_read32(ctx.mmu, ctx.gpr[1] + 28); // lwz r31, 28(r1)
loc_8028A954: ;
    // 0x8028A954: 80010024
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[1] + 36); // lwz r0, 36(r1)
loc_8028A958: ;
    // 0x8028A958: 7C0803A6
    ctx.lr = ctx.gpr[0]; // mtlr r0
loc_8028A95C: ;
    // 0x8028A95C: 38210020
    ctx.gpr[1] = ctx.gpr[1] + 32; // addi r1, r1, 32
loc_8028A960: ;
    // 0x8028A960: 4E800020
    ctx.pc = ctx.lr; return;
}

// ---- extracted from the generated tree: 8028A974 ----
// The fall-back copy carries a different name on purpose: the driver only
// extracts the translated body of an address that the test *references* but
// does not *define* (see tools/check_native_lowering.py::extract_functions),
// so the fall-back must not define func_8028A974 itself.  The alias below
// gives the fall-back copy the guest's name for the stand-alone build.
NWII_GUEST_ABI void translated_8028A974(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x8028A974, ctx);
    if (ctx.pc != 0x8028A974) {
        native_perf_census::GuestSwitch(ctx.pc,7); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x8028A998: goto loc_8028A998;
            case 0x8028A99C: goto loc_8028A99C;
            case 0x8028A9A0: goto loc_8028A9A0;
            case 0x8028A9A4: goto loc_8028A9A4;
            case 0x8028A9A8: goto loc_8028A9A8;
            case 0x8028A9AC: goto loc_8028A9AC;
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_8028A974: ;
    ctx.inst_count += 8;
    // 0x8028A974: 9421FFE0
    {
        uint32_t ea = ctx.gpr[1] + -32;
/* NATIVE_V13_CODEGEN_V1 ICAgICAgICBjdHgubW11LndyaXRlMzIoZWEsIGN0eC5ncHJbMV0pOwo= */        nl::rec_write32(ctx.mmu, ea, ctx.gpr[1]);
        ctx.gpr[1] = ea;
    } // stwu r1, -32(r1)
loc_8028A978: ;
    // 0x8028A978: 7C0802A6
    ctx.gpr[0] = ctx.lr; // mflr r0
loc_8028A97C: ;
    // 0x8028A97C: 90010024
/* NATIVE_V13_CODEGEN_V1 ICAgIGN0eC5tbXUud3JpdGUzMihjdHguZ3ByWzFdICsgMzYsIGN0eC5ncHJbMF0pOyAvLyBzdHcgcjAsIDM2KHIxKQo= */    nl::rec_write32(ctx.mmu, ctx.gpr[1] + 36, ctx.gpr[0]); // stw r0, 36(r1)
loc_8028A980: ;
    // 0x8028A980: 38A10008
    ctx.gpr[5] = ctx.gpr[1] + 8; // addi r5, r1, 8
loc_8028A984: ;
    // 0x8028A984: 93E1001C
/* NATIVE_V13_CODEGEN_V1 ICAgIGN0eC5tbXUud3JpdGUzMihjdHguZ3ByWzFdICsgMjgsIGN0eC5ncHJbMzFdKTsgLy8gc3R3IHIzMSwgMjgocjEpCg== */    nl::rec_write32(ctx.mmu, ctx.gpr[1] + 28, ctx.gpr[31]); // stw r31, 28(r1)
loc_8028A988: ;
    // 0x8028A988: 7C7F1B78
    ctx.gpr[31] = ctx.gpr[3]; // mr r31, r3
loc_8028A98C: ;
    // 0x8028A98C: 7C832378
    ctx.gpr[3] = ctx.gpr[4]; // mr r3, r4
loc_8028A990: ;
    // 0x8028A990: 7FE4FB78
    ctx.gpr[4] = ctx.gpr[31]; // mr r4, r31
loc_8028A994: ;
    // 0x8028A994: 4BFFC495
    ctx.lr = 0x8028A998; // save return address
    ctx.pc = 0x80286E28; func_80286E28(ctx);
    if (ctx.pc != 0x8028A998) return;
    ++ctx.inst_count;
loc_8028A998: ;
    // 0x8028A998: C0010008
    if (ctx.require_fpu(0x8028A998)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[1] + 8); // lfs
    ++ctx.inst_count;
loc_8028A99C: ;
    // 0x8028A99C: D01F0000
    if (ctx.require_fpu(0x8028A99C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[31] + 0, (float)ctx.fpr[0]); // stfs f0, 0(r31)
    ++ctx.inst_count;
loc_8028A9A0: ;
    // 0x8028A9A0: C001000C
    if (ctx.require_fpu(0x8028A9A0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[1] + 12); // lfs
    ++ctx.inst_count;
loc_8028A9A4: ;
    // 0x8028A9A4: D01F0004
    if (ctx.require_fpu(0x8028A9A4)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[31] + 4, (float)ctx.fpr[0]); // stfs f0, 4(r31)
    ++ctx.inst_count;
loc_8028A9A8: ;
    // 0x8028A9A8: C0010010
    if (ctx.require_fpu(0x8028A9A8)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[1] + 16); // lfs
    ++ctx.inst_count;
loc_8028A9AC: ;
    ctx.inst_count += 5;
    // 0x8028A9AC: D01F0008
    if (ctx.require_fpu(0x8028A9AC)) { ctx.inst_count -= 5; return; } // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[31] + 8, (float)ctx.fpr[0]); // stfs f0, 8(r31)
loc_8028A9B0: ;
    // 0x8028A9B0: 83E1001C
    ctx.gpr[31] = nl::rec_read32(ctx.mmu, ctx.gpr[1] + 28); // lwz r31, 28(r1)
loc_8028A9B4: ;
    // 0x8028A9B4: 80010024
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[1] + 36); // lwz r0, 36(r1)
loc_8028A9B8: ;
    // 0x8028A9B8: 7C0803A6
    ctx.lr = ctx.gpr[0]; // mtlr r0
loc_8028A9BC: ;
    // 0x8028A9BC: 38210020
    ctx.gpr[1] = ctx.gpr[1] + 32; // addi r1, r1, 32
loc_8028A9C0: ;
    // 0x8028A9C0: 4E800020
    ctx.pc = ctx.lr; return;
}

// ---- extracted from the generated tree: 803A2CC8 ----
NWII_GUEST_ABI void func_803A2CC8(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x803A2CC8, ctx);
    if (ctx.pc != 0x803A2CC8) {
        native_perf_census::GuestSwitch(ctx.pc,81); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x803A2CD4: goto loc_803A2CD4;
            case 0x803A2D10: goto loc_803A2D10;
            case 0x803A2D28: goto loc_803A2D28;
            case 0x803A2D2C: goto loc_803A2D2C;
            case 0x803A2D30: goto loc_803A2D30;
            case 0x803A2D40: goto loc_803A2D40;
            case 0x803A2D44: goto loc_803A2D44;
            case 0x803A2D48: goto loc_803A2D48;
            case 0x803A2D4C: goto loc_803A2D4C;
            case 0x803A2D6C: goto loc_803A2D6C;
            case 0x803A2D70: goto loc_803A2D70;
            case 0x803A2D74: goto loc_803A2D74;
            case 0x803A2D78: goto loc_803A2D78;
            case 0x803A2D90: goto loc_803A2D90;
            case 0x803A2DA8: goto loc_803A2DA8;
            case 0x803A2DB0: goto loc_803A2DB0;
            case 0x803A2DB4: goto loc_803A2DB4;
            case 0x803A2DB8: goto loc_803A2DB8;
            case 0x803A2DBC: goto loc_803A2DBC;
            case 0x803A2DC0: goto loc_803A2DC0;
            case 0x803A2DC4: goto loc_803A2DC4;
            case 0x803A2DCC: goto loc_803A2DCC;
            case 0x803A2DD4: goto loc_803A2DD4;
            case 0x803A2DD8: goto loc_803A2DD8;
            case 0x803A2DDC: goto loc_803A2DDC;
            case 0x803A2DE0: goto loc_803A2DE0;
            case 0x803A2DF8: goto loc_803A2DF8;
            case 0x803A2E00: goto loc_803A2E00;
            case 0x803A2E04: goto loc_803A2E04;
            case 0x803A2E08: goto loc_803A2E08;
            case 0x803A2E0C: goto loc_803A2E0C;
            case 0x803A2E10: goto loc_803A2E10;
            case 0x803A2E14: goto loc_803A2E14;
            case 0x803A2E1C: goto loc_803A2E1C;
            case 0x803A2E24: goto loc_803A2E24;
            case 0x803A2E28: goto loc_803A2E28;
            case 0x803A2E2C: goto loc_803A2E2C;
            case 0x803A2E34: goto loc_803A2E34;
            case 0x803A2E3C: goto loc_803A2E3C;
            case 0x803A2E40: goto loc_803A2E40;
            case 0x803A2E44: goto loc_803A2E44;
            case 0x803A2E48: goto loc_803A2E48;
            case 0x803A2E4C: goto loc_803A2E4C;
            case 0x803A2E50: goto loc_803A2E50;
            case 0x803A2E54: goto loc_803A2E54;
            case 0x803A2E58: goto loc_803A2E58;
            case 0x803A2E5C: goto loc_803A2E5C;
            case 0x803A2E60: goto loc_803A2E60;
            case 0x803A2E64: goto loc_803A2E64;
            case 0x803A2E68: goto loc_803A2E68;
            case 0x803A2E6C: goto loc_803A2E6C;
            case 0x803A2E70: goto loc_803A2E70;
            case 0x803A2E74: goto loc_803A2E74;
            case 0x803A2E78: goto loc_803A2E78;
            case 0x803A2E7C: goto loc_803A2E7C;
            case 0x803A2E80: goto loc_803A2E80;
            case 0x803A2E84: goto loc_803A2E84;
            case 0x803A2E88: goto loc_803A2E88;
            case 0x803A2E8C: goto loc_803A2E8C;
            case 0x803A2E90: goto loc_803A2E90;
            case 0x803A2E94: goto loc_803A2E94;
            case 0x803A2E98: goto loc_803A2E98;
            case 0x803A2E9C: goto loc_803A2E9C;
            case 0x803A2EA0: goto loc_803A2EA0;
            case 0x803A2EA4: goto loc_803A2EA4;
            case 0x803A2EA8: goto loc_803A2EA8;
            case 0x803A2EAC: goto loc_803A2EAC;
            case 0x803A2EB0: goto loc_803A2EB0;
            case 0x803A2EB4: goto loc_803A2EB4;
            case 0x803A2EBC: goto loc_803A2EBC;
            case 0x803A2EC0: goto loc_803A2EC0;
            case 0x803A2EC4: goto loc_803A2EC4;
            case 0x803A2ECC: goto loc_803A2ECC;
            case 0x803A2EDC: goto loc_803A2EDC;
            case 0x803A2EE4: goto loc_803A2EE4;
            case 0x803A2EE8: goto loc_803A2EE8;
            case 0x803A2EEC: goto loc_803A2EEC;
            case 0x803A2EF0: goto loc_803A2EF0;
            case 0x803A2EF4: goto loc_803A2EF4;
            case 0x803A2EFC: goto loc_803A2EFC;
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_803A2CC8: ;
    ctx.inst_count += 2;
    // 0x803A2CC8: 9421FFF0
    {
        uint32_t ea = ctx.gpr[1] + -16;
/* NATIVE_V13_CODEGEN_V1 ICAgICAgICBjdHgubW11LndyaXRlMzIoZWEsIGN0eC5ncHJbMV0pOwo= */        nl::rec_write32(ctx.mmu, ea, ctx.gpr[1]);
        ctx.gpr[1] = ea;
    } // stwu r1, -16(r1)
loc_803A2CCC: ;
    // 0x803A2CCC: 3CA08048
    ctx.gpr[5] = -2142765056; // lis
loc_803A2CD0: ;
    // 0x803A2CD0: 3C004410
    ctx.gpr[0] = 1141899264; // lis
    ++ctx.inst_count;
loc_803A2CD4: ;
    ctx.inst_count += 5;
    // 0x803A2CD4: D8210008
    if (ctx.require_fpu(0x803A2CD4)) { ctx.inst_count -= 5; return; } // MSR.FP exception
/* NATIVE_V13_CODEGEN_V1 ICAgIGN0eC5tbXUud3JpdGVfZjY0KGN0eC5ncHJbMV0gKyA4LCBjdHguZnByWzFdKTsgLy8gc3RmZAo= */    nl::rec_write_f64(ctx.mmu, ctx.gpr[1] + 8, ctx.fpr[1]); // stfd
loc_803A2CD8: ;
    // 0x803A2CD8: 38A55C38
    ctx.gpr[5] = ctx.gpr[5] + 23608; // addi r5, r5, 23608
loc_803A2CDC: ;
    // 0x803A2CDC: 80C10008
    ctx.gpr[6] = nl::rec_read32(ctx.mmu, ctx.gpr[1] + 8); // lwz r6, 8(r1)
loc_803A2CE0: ;
    // 0x803A2CE0: 54C4007E
    ctx.gpr[4] = std::rotl(ctx.gpr[6], 0) & 0x7FFFFFFF; // rlwinm r4, r6, 0, 1, 31
loc_803A2CE4: ;
    // 0x803A2CE4: 7C040000
    ctx.cr[0].lt = ((int32_t)ctx.gpr[4] < (int32_t)ctx.gpr[0]);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[4] > (int32_t)ctx.gpr[0]);
    ctx.cr[0].eq = ((int32_t)ctx.gpr[4] == (int32_t)ctx.gpr[0]);
loc_803A2CE8: ;
    // 0x803A2CE8: 4180006C
    if (ctx.cr[0].lt) goto loc_803A2D54;
    ++ctx.inst_count;
loc_803A2CEC: ;
    ctx.inst_count += 2;
    // 0x803A2CEC: 3C007FF0
    ctx.gpr[0] = 2146435072; // lis
loc_803A2CF0: ;
    // 0x803A2CF0: 7C040000
    ctx.cr[0].lt = ((int32_t)ctx.gpr[4] < (int32_t)ctx.gpr[0]);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[4] > (int32_t)ctx.gpr[0]);
    ctx.cr[0].eq = ((int32_t)ctx.gpr[4] == (int32_t)ctx.gpr[0]);
loc_803A2CF4: ;
    // 0x803A2CF4: 4181001C
    if (ctx.cr[0].gt) goto loc_803A2D10;
    ++ctx.inst_count;
loc_803A2CF8: ;
    ctx.inst_count += 2;
    // 0x803A2CF8: 3C048010
    ctx.gpr[0] = ctx.gpr[4] + -2146435072; // addis
loc_803A2CFC: ;
    // 0x803A2CFC: 28000000
    ctx.cr[0].lt = (ctx.gpr[0] < 0);
    ctx.cr[0].gt = (ctx.gpr[0] > 0);
    ctx.cr[0].eq = (ctx.gpr[0] == 0);
loc_803A2D00: ;
    // 0x803A2D00: 40820018
    if (!ctx.cr[0].eq) goto loc_803A2D18;
    ++ctx.inst_count;
loc_803A2D04: ;
    ctx.inst_count += 2;
    // 0x803A2D04: 8001000C
    ctx.gpr[0] = nl::rec_read32(ctx.mmu, ctx.gpr[1] + 12); // lwz r0, 12(r1)
loc_803A2D08: ;
    // 0x803A2D08: 2C000000
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = ((int32_t)ctx.gpr[0] == 0);
loc_803A2D0C: ;
    // 0x803A2D0C: 4182000C
    if (ctx.cr[0].eq) goto loc_803A2D18;
    ++ctx.inst_count;
loc_803A2D10: ;
    ctx.inst_count += 1;
    // 0x803A2D10: FC21082A
    if (ctx.require_fpu(0x803A2D10)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.fpr[1] + ctx.fpr[1]; // fadd
loc_803A2D14: ;
    // 0x803A2D14: 480001EC
    goto loc_803A2F00;
    ++ctx.inst_count;
loc_803A2D18: ;
    ctx.inst_count += 1;
    // 0x803A2D18: 2C060000
    ctx.cr[0].lt = ((int32_t)ctx.gpr[6] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[6] > 0);
    ctx.cr[0].eq = ((int32_t)ctx.gpr[6] == 0);
loc_803A2D1C: ;
    // 0x803A2D1C: 4081001C
    if (!ctx.cr[0].gt) goto loc_803A2D38;
    ++ctx.inst_count;
loc_803A2D20: ;
    ctx.inst_count += 1;
    // 0x803A2D20: 38850000
    ctx.gpr[4] = ctx.gpr[5] + 0; // addi r4, r5, 0
loc_803A2D24: ;
    // 0x803A2D24: 38650020
    ctx.gpr[3] = ctx.gpr[5] + 32; // addi r3, r5, 32
    ++ctx.inst_count;
loc_803A2D28: ;
    // 0x803A2D28: C8240018
    if (ctx.require_fpu(0x803A2D28)) return; // MSR.FP exception
    ctx.fpr[1] = nl::rec_read_f64(ctx.mmu, ctx.gpr[4] + 24); // lfd
    ++ctx.inst_count;
loc_803A2D2C: ;
    // 0x803A2D2C: C8030018
    if (ctx.require_fpu(0x803A2D2C)) return; // MSR.FP exception
    ctx.fpr[0] = nl::rec_read_f64(ctx.mmu, ctx.gpr[3] + 24); // lfd
    ++ctx.inst_count;
loc_803A2D30: ;
    ctx.inst_count += 1;
    // 0x803A2D30: FC21002A
    if (ctx.require_fpu(0x803A2D30)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.fpr[1] + ctx.fpr[0]; // fadd
loc_803A2D34: ;
    // 0x803A2D34: 480001CC
    goto loc_803A2F00;
    ++ctx.inst_count;
loc_803A2D38: ;
    ctx.inst_count += 1;
    // 0x803A2D38: 38850000
    ctx.gpr[4] = ctx.gpr[5] + 0; // addi r4, r5, 0
loc_803A2D3C: ;
    // 0x803A2D3C: 38650020
    ctx.gpr[3] = ctx.gpr[5] + 32; // addi r3, r5, 32
    ++ctx.inst_count;
loc_803A2D40: ;
    // 0x803A2D40: C8240018
    if (ctx.require_fpu(0x803A2D40)) return; // MSR.FP exception
    ctx.fpr[1] = nl::rec_read_f64(ctx.mmu, ctx.gpr[4] + 24); // lfd
    ++ctx.inst_count;
loc_803A2D44: ;
    // 0x803A2D44: C8030018
    if (ctx.require_fpu(0x803A2D44)) return; // MSR.FP exception
    ctx.fpr[0] = nl::rec_read_f64(ctx.mmu, ctx.gpr[3] + 24); // lfd
    ++ctx.inst_count;
loc_803A2D48: ;
    // 0x803A2D48: FC200850
    if (ctx.require_fpu(0x803A2D48)) return; // MSR.FP exception
    ctx.fpr[1] = -ctx.fpr[1]; // fneg
    ++ctx.inst_count;
loc_803A2D4C: ;
    ctx.inst_count += 1;
    // 0x803A2D4C: FC210028
    if (ctx.require_fpu(0x803A2D4C)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.fpr[1] - ctx.fpr[0]; // fsub
loc_803A2D50: ;
    // 0x803A2D50: 480001B0
    goto loc_803A2F00;
    ++ctx.inst_count;
loc_803A2D54: ;
    ctx.inst_count += 2;
    // 0x803A2D54: 3C003FDC
    ctx.gpr[0] = 1071382528; // lis
loc_803A2D58: ;
    // 0x803A2D58: 7C040000
    ctx.cr[0].lt = ((int32_t)ctx.gpr[4] < (int32_t)ctx.gpr[0]);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[4] > (int32_t)ctx.gpr[0]);
    ctx.cr[0].eq = ((int32_t)ctx.gpr[4] == (int32_t)ctx.gpr[0]);
loc_803A2D5C: ;
    // 0x803A2D5C: 40800030
    if (!ctx.cr[0].lt) goto loc_803A2D8C;
    ++ctx.inst_count;
loc_803A2D60: ;
    ctx.inst_count += 2;
    // 0x803A2D60: 3C003E20
    ctx.gpr[0] = 1042284544; // lis
loc_803A2D64: ;
    // 0x803A2D64: 7C040000
    ctx.cr[0].lt = ((int32_t)ctx.gpr[4] < (int32_t)ctx.gpr[0]);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[4] > (int32_t)ctx.gpr[0]);
    ctx.cr[0].eq = ((int32_t)ctx.gpr[4] == (int32_t)ctx.gpr[0]);
loc_803A2D68: ;
    // 0x803A2D68: 4080001C
    if (!ctx.cr[0].lt) goto loc_803A2D84;
    ++ctx.inst_count;
loc_803A2D6C: ;
    // 0x803A2D6C: C842C4E8
    if (ctx.require_fpu(0x803A2D6C)) return; // MSR.FP exception
    ctx.fpr[2] = nl::rec_read_f64(ctx.mmu, ctx.gpr[2] + -15128); // lfd
    ++ctx.inst_count;
loc_803A2D70: ;
    // 0x803A2D70: C802C4F0
    if (ctx.require_fpu(0x803A2D70)) return; // MSR.FP exception
    ctx.fpr[0] = nl::rec_read_f64(ctx.mmu, ctx.gpr[2] + -15120); // lfd
    ++ctx.inst_count;
loc_803A2D74: ;
    // 0x803A2D74: FC42082A
    if (ctx.require_fpu(0x803A2D74)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.fpr[2] + ctx.fpr[1]; // fadd
    ++ctx.inst_count;
loc_803A2D78: ;
    ctx.inst_count += 1;
    // 0x803A2D78: FC020040
    if (ctx.require_fpu(0x803A2D78)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    {
        bool un = std::isnan(ctx.fpr[2]) || std::isnan(ctx.fpr[0]);
        ctx.cr[0].lt = !un && (ctx.fpr[2] < ctx.fpr[0]);
        ctx.cr[0].gt = !un && (ctx.fpr[2] > ctx.fpr[0]);
        ctx.cr[0].eq = !un && (ctx.fpr[2] == ctx.fpr[0]);
        ctx.cr[0].so = un;
    }
loc_803A2D7C: ;
    // 0x803A2D7C: 40810008
    if (!ctx.cr[0].gt) goto loc_803A2D84;
    ++ctx.inst_count;
loc_803A2D80: ;
    // 0x803A2D80: 48000180
    goto loc_803A2F00;
    ++ctx.inst_count;
loc_803A2D84: ;
    ctx.inst_count += 1;
    // 0x803A2D84: 3800FFFF
    ctx.gpr[0] = -1; // li r0, -1
loc_803A2D88: ;
    // 0x803A2D88: 480000A4
    goto loc_803A2E2C;
    ++ctx.inst_count;
loc_803A2D8C: ;
    // 0x803A2D8C: 3C003FF3
    ctx.gpr[0] = 1072889856; // lis
    ++ctx.inst_count;
loc_803A2D90: ;
    ctx.inst_count += 2;
    // 0x803A2D90: FC600A10
    if (ctx.require_fpu(0x803A2D90)) { ctx.inst_count -= 2; return; } // MSR.FP exception
    ctx.fpr[3] = std::fabs(ctx.fpr[1]); // fabs
loc_803A2D94: ;
    // 0x803A2D94: 7C040000
    ctx.cr[0].lt = ((int32_t)ctx.gpr[4] < (int32_t)ctx.gpr[0]);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[4] > (int32_t)ctx.gpr[0]);
    ctx.cr[0].eq = ((int32_t)ctx.gpr[4] == (int32_t)ctx.gpr[0]);
loc_803A2D98: ;
    // 0x803A2D98: 40800050
    if (!ctx.cr[0].lt) goto loc_803A2DE8;
    ++ctx.inst_count;
loc_803A2D9C: ;
    ctx.inst_count += 2;
    // 0x803A2D9C: 3C003FE6
    ctx.gpr[0] = 1072037888; // lis
loc_803A2DA0: ;
    // 0x803A2DA0: 7C040000
    ctx.cr[0].lt = ((int32_t)ctx.gpr[4] < (int32_t)ctx.gpr[0]);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[4] > (int32_t)ctx.gpr[0]);
    ctx.cr[0].eq = ((int32_t)ctx.gpr[4] == (int32_t)ctx.gpr[0]);
loc_803A2DA4: ;
    // 0x803A2DA4: 40800028
    if (!ctx.cr[0].lt) goto loc_803A2DCC;
    ++ctx.inst_count;
loc_803A2DA8: ;
    ctx.inst_count += 1;
    // 0x803A2DA8: C802C4F8
    if (ctx.require_fpu(0x803A2DA8)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[0] = nl::rec_read_f64(ctx.mmu, ctx.gpr[2] + -15112); // lfd
loc_803A2DAC: ;
    // 0x803A2DAC: 38000000
    ctx.gpr[0] = 0; // li r0, 0
    ++ctx.inst_count;
loc_803A2DB0: ;
    // 0x803A2DB0: C822C4F0
    if (ctx.require_fpu(0x803A2DB0)) return; // MSR.FP exception
    ctx.fpr[1] = nl::rec_read_f64(ctx.mmu, ctx.gpr[2] + -15120); // lfd
    ++ctx.inst_count;
loc_803A2DB4: ;
    // 0x803A2DB4: FC4000F2
    if (ctx.require_fpu(0x803A2DB4)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.fpr[0] * ctx.fpr[3]; // fmul
    ++ctx.inst_count;
loc_803A2DB8: ;
    // 0x803A2DB8: FC00182A
    if (ctx.require_fpu(0x803A2DB8)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.fpr[0] + ctx.fpr[3]; // fadd
    ++ctx.inst_count;
loc_803A2DBC: ;
    // 0x803A2DBC: FC220828
    if (ctx.require_fpu(0x803A2DBC)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.fpr[2] - ctx.fpr[1]; // fsub
    ++ctx.inst_count;
loc_803A2DC0: ;
    // 0x803A2DC0: FC210024
    if (ctx.require_fpu(0x803A2DC0)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.fpr[1] / ctx.fpr[0]; // fdiv
    ++ctx.inst_count;
loc_803A2DC4: ;
    ctx.inst_count += 1;
    // 0x803A2DC4: D8210008
    if (ctx.require_fpu(0x803A2DC4)) { ctx.inst_count -= 1; return; } // MSR.FP exception
/* NATIVE_V13_CODEGEN_V1 ICAgIGN0eC5tbXUud3JpdGVfZjY0KGN0eC5ncHJbMV0gKyA4LCBjdHguZnByWzFdKTsgLy8gc3RmZAo= */    nl::rec_write_f64(ctx.mmu, ctx.gpr[1] + 8, ctx.fpr[1]); // stfd
loc_803A2DC8: ;
    // 0x803A2DC8: 48000064
    goto loc_803A2E2C;
    ++ctx.inst_count;
loc_803A2DCC: ;
    ctx.inst_count += 1;
    // 0x803A2DCC: C802C4F0
    if (ctx.require_fpu(0x803A2DCC)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[0] = nl::rec_read_f64(ctx.mmu, ctx.gpr[2] + -15120); // lfd
loc_803A2DD0: ;
    // 0x803A2DD0: 38000001
    ctx.gpr[0] = 1; // li r0, 1
    ++ctx.inst_count;
loc_803A2DD4: ;
    // 0x803A2DD4: FC230028
    if (ctx.require_fpu(0x803A2DD4)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.fpr[3] - ctx.fpr[0]; // fsub
    ++ctx.inst_count;
loc_803A2DD8: ;
    // 0x803A2DD8: FC00182A
    if (ctx.require_fpu(0x803A2DD8)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.fpr[0] + ctx.fpr[3]; // fadd
    ++ctx.inst_count;
loc_803A2DDC: ;
    // 0x803A2DDC: FC210024
    if (ctx.require_fpu(0x803A2DDC)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.fpr[1] / ctx.fpr[0]; // fdiv
    ++ctx.inst_count;
loc_803A2DE0: ;
    ctx.inst_count += 1;
    // 0x803A2DE0: D8210008
    if (ctx.require_fpu(0x803A2DE0)) { ctx.inst_count -= 1; return; } // MSR.FP exception
/* NATIVE_V13_CODEGEN_V1 ICAgIGN0eC5tbXUud3JpdGVfZjY0KGN0eC5ncHJbMV0gKyA4LCBjdHguZnByWzFdKTsgLy8gc3RmZAo= */    nl::rec_write_f64(ctx.mmu, ctx.gpr[1] + 8, ctx.fpr[1]); // stfd
loc_803A2DE4: ;
    // 0x803A2DE4: 48000048
    goto loc_803A2E2C;
    ++ctx.inst_count;
loc_803A2DE8: ;
    ctx.inst_count += 3;
    // 0x803A2DE8: 3C604004
    ctx.gpr[3] = 1074003968; // lis
loc_803A2DEC: ;
    // 0x803A2DEC: 38038000
    ctx.gpr[0] = ctx.gpr[3] + -32768; // addi r0, r3, -32768
loc_803A2DF0: ;
    // 0x803A2DF0: 7C040000
    ctx.cr[0].lt = ((int32_t)ctx.gpr[4] < (int32_t)ctx.gpr[0]);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[4] > (int32_t)ctx.gpr[0]);
    ctx.cr[0].eq = ((int32_t)ctx.gpr[4] == (int32_t)ctx.gpr[0]);
loc_803A2DF4: ;
    // 0x803A2DF4: 40800028
    if (!ctx.cr[0].lt) goto loc_803A2E1C;
    ++ctx.inst_count;
loc_803A2DF8: ;
    ctx.inst_count += 1;
    // 0x803A2DF8: C842C500
    if (ctx.require_fpu(0x803A2DF8)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[2] = nl::rec_read_f64(ctx.mmu, ctx.gpr[2] + -15104); // lfd
loc_803A2DFC: ;
    // 0x803A2DFC: 38000002
    ctx.gpr[0] = 2; // li r0, 2
    ++ctx.inst_count;
loc_803A2E00: ;
    // 0x803A2E00: C802C4F0
    if (ctx.require_fpu(0x803A2E00)) return; // MSR.FP exception
    ctx.fpr[0] = nl::rec_read_f64(ctx.mmu, ctx.gpr[2] + -15120); // lfd
    ++ctx.inst_count;
loc_803A2E04: ;
    // 0x803A2E04: FC2200F2
    if (ctx.require_fpu(0x803A2E04)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.fpr[2] * ctx.fpr[3]; // fmul
    ++ctx.inst_count;
loc_803A2E08: ;
    // 0x803A2E08: FC431028
    if (ctx.require_fpu(0x803A2E08)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.fpr[3] - ctx.fpr[2]; // fsub
    ++ctx.inst_count;
loc_803A2E0C: ;
    // 0x803A2E0C: FC00082A
    if (ctx.require_fpu(0x803A2E0C)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.fpr[0] + ctx.fpr[1]; // fadd
    ++ctx.inst_count;
loc_803A2E10: ;
    // 0x803A2E10: FC220024
    if (ctx.require_fpu(0x803A2E10)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.fpr[2] / ctx.fpr[0]; // fdiv
    ++ctx.inst_count;
loc_803A2E14: ;
    ctx.inst_count += 1;
    // 0x803A2E14: D8210008
    if (ctx.require_fpu(0x803A2E14)) { ctx.inst_count -= 1; return; } // MSR.FP exception
/* NATIVE_V13_CODEGEN_V1 ICAgIGN0eC5tbXUud3JpdGVfZjY0KGN0eC5ncHJbMV0gKyA4LCBjdHguZnByWzFdKTsgLy8gc3RmZAo= */    nl::rec_write_f64(ctx.mmu, ctx.gpr[1] + 8, ctx.fpr[1]); // stfd
loc_803A2E18: ;
    // 0x803A2E18: 48000014
    goto loc_803A2E2C;
    ++ctx.inst_count;
loc_803A2E1C: ;
    ctx.inst_count += 1;
    // 0x803A2E1C: C802C508
    if (ctx.require_fpu(0x803A2E1C)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[0] = nl::rec_read_f64(ctx.mmu, ctx.gpr[2] + -15096); // lfd
loc_803A2E20: ;
    // 0x803A2E20: 38000003
    ctx.gpr[0] = 3; // li r0, 3
    ++ctx.inst_count;
loc_803A2E24: ;
    // 0x803A2E24: FC201824
    if (ctx.require_fpu(0x803A2E24)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.fpr[0] / ctx.fpr[3]; // fdiv
    ++ctx.inst_count;
loc_803A2E28: ;
    // 0x803A2E28: D8210008
    if (ctx.require_fpu(0x803A2E28)) return; // MSR.FP exception
/* NATIVE_V13_CODEGEN_V1 ICAgIGN0eC5tbXUud3JpdGVfZjY0KGN0eC5ncHJbMV0gKyA4LCBjdHguZnByWzFdKTsgLy8gc3RmZAo= */    nl::rec_write_f64(ctx.mmu, ctx.gpr[1] + 8, ctx.fpr[1]); // stfd
    ++ctx.inst_count;
loc_803A2E2C: ;
    ctx.inst_count += 1;
    // 0x803A2E2C: FC010072
    if (ctx.require_fpu(0x803A2E2C)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[0] = ctx.fpr[1] * ctx.fpr[1]; // fmul
loc_803A2E30: ;
    // 0x803A2E30: 38650040
    ctx.gpr[3] = ctx.gpr[5] + 64; // addi r3, r5, 64
    ++ctx.inst_count;
loc_803A2E34: ;
    ctx.inst_count += 1;
    // 0x803A2E34: C8630050
    if (ctx.require_fpu(0x803A2E34)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[3] = nl::rec_read_f64(ctx.mmu, ctx.gpr[3] + 80); // lfd
loc_803A2E38: ;
    // 0x803A2E38: 2C000000
    ctx.cr[0].lt = ((int32_t)ctx.gpr[0] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[0] > 0);
    ctx.cr[0].eq = ((int32_t)ctx.gpr[0] == 0);
    ++ctx.inst_count;
loc_803A2E3C: ;
    // 0x803A2E3C: C8430048
    if (ctx.require_fpu(0x803A2E3C)) return; // MSR.FP exception
    ctx.fpr[2] = nl::rec_read_f64(ctx.mmu, ctx.gpr[3] + 72); // lfd
    ++ctx.inst_count;
loc_803A2E40: ;
    // 0x803A2E40: C9630040
    if (ctx.require_fpu(0x803A2E40)) return; // MSR.FP exception
    ctx.fpr[11] = nl::rec_read_f64(ctx.mmu, ctx.gpr[3] + 64); // lfd
    ++ctx.inst_count;
loc_803A2E44: ;
    // 0x803A2E44: FDA00032
    if (ctx.require_fpu(0x803A2E44)) return; // MSR.FP exception
    ctx.fpr[13] = ctx.fpr[0] * ctx.fpr[0]; // fmul
    ++ctx.inst_count;
loc_803A2E48: ;
    // 0x803A2E48: C8A30038
    if (ctx.require_fpu(0x803A2E48)) return; // MSR.FP exception
    ctx.fpr[5] = nl::rec_read_f64(ctx.mmu, ctx.gpr[3] + 56); // lfd
    ++ctx.inst_count;
loc_803A2E4C: ;
    // 0x803A2E4C: C9430030
    if (ctx.require_fpu(0x803A2E4C)) return; // MSR.FP exception
    ctx.fpr[10] = nl::rec_read_f64(ctx.mmu, ctx.gpr[3] + 48); // lfd
    ++ctx.inst_count;
loc_803A2E50: ;
    // 0x803A2E50: C8830028
    if (ctx.require_fpu(0x803A2E50)) return; // MSR.FP exception
    ctx.fpr[4] = nl::rec_read_f64(ctx.mmu, ctx.gpr[3] + 40); // lfd
    ++ctx.inst_count;
loc_803A2E54: ;
    // 0x803A2E54: C9230020
    if (ctx.require_fpu(0x803A2E54)) return; // MSR.FP exception
    ctx.fpr[9] = nl::rec_read_f64(ctx.mmu, ctx.gpr[3] + 32); // lfd
    ++ctx.inst_count;
loc_803A2E58: ;
    // 0x803A2E58: FD8D00F2
    if (ctx.require_fpu(0x803A2E58)) return; // MSR.FP exception
    ctx.fpr[12] = ctx.fpr[13] * ctx.fpr[3]; // fmul
    ++ctx.inst_count;
loc_803A2E5C: ;
    // 0x803A2E5C: C8630018
    if (ctx.require_fpu(0x803A2E5C)) return; // MSR.FP exception
    ctx.fpr[3] = nl::rec_read_f64(ctx.mmu, ctx.gpr[3] + 24); // lfd
    ++ctx.inst_count;
loc_803A2E60: ;
    // 0x803A2E60: FCCD00B2
    if (ctx.require_fpu(0x803A2E60)) return; // MSR.FP exception
    ctx.fpr[6] = ctx.fpr[13] * ctx.fpr[2]; // fmul
    ++ctx.inst_count;
loc_803A2E64: ;
    // 0x803A2E64: C9030010
    if (ctx.require_fpu(0x803A2E64)) return; // MSR.FP exception
    ctx.fpr[8] = nl::rec_read_f64(ctx.mmu, ctx.gpr[3] + 16); // lfd
    ++ctx.inst_count;
loc_803A2E68: ;
    // 0x803A2E68: C8430008
    if (ctx.require_fpu(0x803A2E68)) return; // MSR.FP exception
    ctx.fpr[2] = nl::rec_read_f64(ctx.mmu, ctx.gpr[3] + 8); // lfd
    ++ctx.inst_count;
loc_803A2E6C: ;
    // 0x803A2E6C: FD6B602A
    if (ctx.require_fpu(0x803A2E6C)) return; // MSR.FP exception
    ctx.fpr[11] = ctx.fpr[11] + ctx.fpr[12]; // fadd
    ++ctx.inst_count;
loc_803A2E70: ;
    // 0x803A2E70: C8E50040
    if (ctx.require_fpu(0x803A2E70)) return; // MSR.FP exception
    ctx.fpr[7] = nl::rec_read_f64(ctx.mmu, ctx.gpr[5] + 64); // lfd
    ++ctx.inst_count;
loc_803A2E74: ;
    // 0x803A2E74: FCA5302A
    if (ctx.require_fpu(0x803A2E74)) return; // MSR.FP exception
    ctx.fpr[5] = ctx.fpr[5] + ctx.fpr[6]; // fadd
    ++ctx.inst_count;
loc_803A2E78: ;
    // 0x803A2E78: FCCD02F2
    if (ctx.require_fpu(0x803A2E78)) return; // MSR.FP exception
    ctx.fpr[6] = ctx.fpr[13] * ctx.fpr[11]; // fmul
    ++ctx.inst_count;
loc_803A2E7C: ;
    // 0x803A2E7C: FCAD0172
    if (ctx.require_fpu(0x803A2E7C)) return; // MSR.FP exception
    ctx.fpr[5] = ctx.fpr[13] * ctx.fpr[5]; // fmul
    ++ctx.inst_count;
loc_803A2E80: ;
    // 0x803A2E80: FCCA302A
    if (ctx.require_fpu(0x803A2E80)) return; // MSR.FP exception
    ctx.fpr[6] = ctx.fpr[10] + ctx.fpr[6]; // fadd
    ++ctx.inst_count;
loc_803A2E84: ;
    // 0x803A2E84: FC84282A
    if (ctx.require_fpu(0x803A2E84)) return; // MSR.FP exception
    ctx.fpr[4] = ctx.fpr[4] + ctx.fpr[5]; // fadd
    ++ctx.inst_count;
loc_803A2E88: ;
    // 0x803A2E88: FCAD01B2
    if (ctx.require_fpu(0x803A2E88)) return; // MSR.FP exception
    ctx.fpr[5] = ctx.fpr[13] * ctx.fpr[6]; // fmul
    ++ctx.inst_count;
loc_803A2E8C: ;
    // 0x803A2E8C: FC8D0132
    if (ctx.require_fpu(0x803A2E8C)) return; // MSR.FP exception
    ctx.fpr[4] = ctx.fpr[13] * ctx.fpr[4]; // fmul
    ++ctx.inst_count;
loc_803A2E90: ;
    // 0x803A2E90: FCA9282A
    if (ctx.require_fpu(0x803A2E90)) return; // MSR.FP exception
    ctx.fpr[5] = ctx.fpr[9] + ctx.fpr[5]; // fadd
    ++ctx.inst_count;
loc_803A2E94: ;
    // 0x803A2E94: FC63202A
    if (ctx.require_fpu(0x803A2E94)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.fpr[3] + ctx.fpr[4]; // fadd
    ++ctx.inst_count;
loc_803A2E98: ;
    // 0x803A2E98: FC8D0172
    if (ctx.require_fpu(0x803A2E98)) return; // MSR.FP exception
    ctx.fpr[4] = ctx.fpr[13] * ctx.fpr[5]; // fmul
    ++ctx.inst_count;
loc_803A2E9C: ;
    // 0x803A2E9C: FC6D00F2
    if (ctx.require_fpu(0x803A2E9C)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.fpr[13] * ctx.fpr[3]; // fmul
    ++ctx.inst_count;
loc_803A2EA0: ;
    // 0x803A2EA0: FC88202A
    if (ctx.require_fpu(0x803A2EA0)) return; // MSR.FP exception
    ctx.fpr[4] = ctx.fpr[8] + ctx.fpr[4]; // fadd
    ++ctx.inst_count;
loc_803A2EA4: ;
    // 0x803A2EA4: FC42182A
    if (ctx.require_fpu(0x803A2EA4)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.fpr[2] + ctx.fpr[3]; // fadd
    ++ctx.inst_count;
loc_803A2EA8: ;
    // 0x803A2EA8: FC6D0132
    if (ctx.require_fpu(0x803A2EA8)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.fpr[13] * ctx.fpr[4]; // fmul
    ++ctx.inst_count;
loc_803A2EAC: ;
    // 0x803A2EAC: FC8D00B2
    if (ctx.require_fpu(0x803A2EAC)) return; // MSR.FP exception
    ctx.fpr[4] = ctx.fpr[13] * ctx.fpr[2]; // fmul
    ++ctx.inst_count;
loc_803A2EB0: ;
    // 0x803A2EB0: FC47182A
    if (ctx.require_fpu(0x803A2EB0)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.fpr[7] + ctx.fpr[3]; // fadd
    ++ctx.inst_count;
loc_803A2EB4: ;
    ctx.inst_count += 1;
    // 0x803A2EB4: FC0000B2
    if (ctx.require_fpu(0x803A2EB4)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[0] = ctx.fpr[0] * ctx.fpr[2]; // fmul
loc_803A2EB8: ;
    // 0x803A2EB8: 40800014
    if (!ctx.cr[0].lt) goto loc_803A2ECC;
    ++ctx.inst_count;
loc_803A2EBC: ;
    // 0x803A2EBC: FC00202A
    if (ctx.require_fpu(0x803A2EBC)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.fpr[0] + ctx.fpr[4]; // fadd
    ++ctx.inst_count;
loc_803A2EC0: ;
    // 0x803A2EC0: FC010032
    if (ctx.require_fpu(0x803A2EC0)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.fpr[1] * ctx.fpr[0]; // fmul
    ++ctx.inst_count;
loc_803A2EC4: ;
    ctx.inst_count += 1;
    // 0x803A2EC4: FC210028
    if (ctx.require_fpu(0x803A2EC4)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.fpr[1] - ctx.fpr[0]; // fsub
loc_803A2EC8: ;
    // 0x803A2EC8: 48000038
    goto loc_803A2F00;
    ++ctx.inst_count;
loc_803A2ECC: ;
    ctx.inst_count += 3;
    // 0x803A2ECC: FC00202A
    if (ctx.require_fpu(0x803A2ECC)) { ctx.inst_count -= 3; return; } // MSR.FP exception
    ctx.fpr[0] = ctx.fpr[0] + ctx.fpr[4]; // fadd
loc_803A2ED0: ;
    // 0x803A2ED0: 54001838
    ctx.gpr[0] = std::rotl(ctx.gpr[0], 3) & 0xFFFFFFF8; // rlwinm r0, r0, 3, 0, 28
loc_803A2ED4: ;
    // 0x803A2ED4: 38650020
    ctx.gpr[3] = ctx.gpr[5] + 32; // addi r3, r5, 32
loc_803A2ED8: ;
    // 0x803A2ED8: 38850000
    ctx.gpr[4] = ctx.gpr[5] + 0; // addi r4, r5, 0
    ++ctx.inst_count;
loc_803A2EDC: ;
    ctx.inst_count += 1;
    // 0x803A2EDC: 7C4304AE
    if (ctx.require_fpu(0x803A2EDC)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[2] = nl::rec_read_f64(ctx.mmu, ctx.gpr[3] + ctx.gpr[0]); // lfdx
loc_803A2EE0: ;
    // 0x803A2EE0: 2C060000
    ctx.cr[0].lt = ((int32_t)ctx.gpr[6] < 0);
    ctx.cr[0].gt = ((int32_t)ctx.gpr[6] > 0);
    ctx.cr[0].eq = ((int32_t)ctx.gpr[6] == 0);
    ++ctx.inst_count;
loc_803A2EE4: ;
    // 0x803A2EE4: FC610032
    if (ctx.require_fpu(0x803A2EE4)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.fpr[1] * ctx.fpr[0]; // fmul
    ++ctx.inst_count;
loc_803A2EE8: ;
    // 0x803A2EE8: 7C0404AE
    if (ctx.require_fpu(0x803A2EE8)) return; // MSR.FP exception
    ctx.fpr[0] = nl::rec_read_f64(ctx.mmu, ctx.gpr[4] + ctx.gpr[0]); // lfdx
    ++ctx.inst_count;
loc_803A2EEC: ;
    // 0x803A2EEC: FC431028
    if (ctx.require_fpu(0x803A2EEC)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.fpr[3] - ctx.fpr[2]; // fsub
    ++ctx.inst_count;
loc_803A2EF0: ;
    // 0x803A2EF0: FC220828
    if (ctx.require_fpu(0x803A2EF0)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.fpr[2] - ctx.fpr[1]; // fsub
    ++ctx.inst_count;
loc_803A2EF4: ;
    ctx.inst_count += 1;
    // 0x803A2EF4: FC200828
    if (ctx.require_fpu(0x803A2EF4)) { ctx.inst_count -= 1; return; } // MSR.FP exception
    ctx.fpr[1] = ctx.fpr[0] - ctx.fpr[1]; // fsub
loc_803A2EF8: ;
    // 0x803A2EF8: 40800008
    if (!ctx.cr[0].lt) goto loc_803A2F00;
    ++ctx.inst_count;
loc_803A2EFC: ;
    // 0x803A2EFC: FC200850
    if (ctx.require_fpu(0x803A2EFC)) return; // MSR.FP exception
    ctx.fpr[1] = -ctx.fpr[1]; // fneg
    ++ctx.inst_count;
loc_803A2F00: ;
    ctx.inst_count += 1;
    // 0x803A2F00: 38210010
    ctx.gpr[1] = ctx.gpr[1] + 16; // addi r1, r1, 16
loc_803A2F04: ;
    // 0x803A2F04: 4E800020
    ctx.pc = ctx.lr; return;
}
#endif

// The stand-alone build (no guest_lowering_extracted.h) has no extracted body
// of 0x8028A974, so the fall-back copy above takes the guest's name.  The
// driver build uses the body it extracted from the generated tree.
//
// The fall-back copy deliberately does not carry the guest's name: the driver
// only extracts the translated body of an address that the test *references*
// but does not *define* (tools/check_native_lowering.py::extract_functions),
// so a self-definition under the guest's name would make the driver treat
// 0x8028A974 as test-provided and never extract it.
#if __has_include("guest_lowering_extracted.h")
// func_8028A974 comes from the extracted header.
#else
#define func_8028A974 translated_8028A974
#endif

// The callees of the vector group: the translated bodies of 0x80286D08
// (RadicalMathLibrary::Matrix::Transform) and 0x80286E28
// (RadicalMathLibrary::Matrix::RotateVector).  The plain-leaf stage has
// already replaced the *definitions* of both in the generated tree with
// its own kernel wrappers, so the test provides the translated bodies
// itself and the group kernels are compared against them.
void func_80286D08(CPUContext& ctx) {
    NWII_TRACE_CALL(0x80286D08, ctx);
    if (ctx.pc != 0x80286D08) {
        native_perf_census::GuestSwitch(ctx.pc,30); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x80286D0C: goto loc_80286D0C;
            case 0x80286D10: goto loc_80286D10;
            case 0x80286D14: goto loc_80286D14;
            case 0x80286D18: goto loc_80286D18;
            case 0x80286D1C: goto loc_80286D1C;
            case 0x80286D20: goto loc_80286D20;
            case 0x80286D24: goto loc_80286D24;
            case 0x80286D28: goto loc_80286D28;
            case 0x80286D2C: goto loc_80286D2C;
            case 0x80286D30: goto loc_80286D30;
            case 0x80286D34: goto loc_80286D34;
            case 0x80286D38: goto loc_80286D38;
            case 0x80286D3C: goto loc_80286D3C;
            case 0x80286D40: goto loc_80286D40;
            case 0x80286D44: goto loc_80286D44;
            case 0x80286D48: goto loc_80286D48;
            case 0x80286D4C: goto loc_80286D4C;
            case 0x80286D50: goto loc_80286D50;
            case 0x80286D54: goto loc_80286D54;
            case 0x80286D58: goto loc_80286D58;
            case 0x80286D5C: goto loc_80286D5C;
            case 0x80286D60: goto loc_80286D60;
            case 0x80286D64: goto loc_80286D64;
            case 0x80286D68: goto loc_80286D68;
            case 0x80286D6C: goto loc_80286D6C;
            case 0x80286D70: goto loc_80286D70;
            case 0x80286D74: goto loc_80286D74;
            case 0x80286D78: goto loc_80286D78;
            case 0x80286D7C: goto loc_80286D7C;
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_80286D08: ;
    // 0x80286D08: C0630010
    if (ctx.require_fpu(0x80286D08)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.ps1[3] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 16); // lfs
    ++ctx.inst_count;
loc_80286D0C: ;
    // 0x80286D0C: C0440004
    if (ctx.require_fpu(0x80286D0C)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 4); // lfs
    ++ctx.inst_count;
loc_80286D10: ;
    // 0x80286D10: C0230014
    if (ctx.require_fpu(0x80286D10)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 20); // lfs
    ++ctx.inst_count;
loc_80286D14: ;
    // 0x80286D14: ECA300B2
    if (ctx.require_fpu(0x80286D14)) return; // MSR.FP exception
    ctx.fpr[5] = ctx.ps1[5] = (float)(ctx.fpr[3] * ctx.fpr[2]); // fmuls
    ++ctx.inst_count;
loc_80286D18: ;
    // 0x80286D18: C0030018
    if (ctx.require_fpu(0x80286D18)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 24); // lfs
    ++ctx.inst_count;
loc_80286D1C: ;
    // 0x80286D1C: EC6100B2
    if (ctx.require_fpu(0x80286D1C)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.ps1[3] = (float)(ctx.fpr[1] * ctx.fpr[2]); // fmuls
    ++ctx.inst_count;
loc_80286D20: ;
    // 0x80286D20: C0830000
    if (ctx.require_fpu(0x80286D20)) return; // MSR.FP exception
    ctx.fpr[4] = ctx.ps1[4] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 0); // lfs
    ++ctx.inst_count;
loc_80286D24: ;
    // 0x80286D24: EC2000B2
    if (ctx.require_fpu(0x80286D24)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = (float)(ctx.fpr[0] * ctx.fpr[2]); // fmuls
    ++ctx.inst_count;
loc_80286D28: ;
    // 0x80286D28: C0E40000
    if (ctx.require_fpu(0x80286D28)) return; // MSR.FP exception
    ctx.fpr[7] = ctx.ps1[7] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 0); // lfs
    ++ctx.inst_count;
loc_80286D2C: ;
    // 0x80286D2C: C0430004
    if (ctx.require_fpu(0x80286D2C)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 4); // lfs
    ++ctx.inst_count;
loc_80286D30: ;
    // 0x80286D30: ECA429FA
    if (ctx.require_fpu(0x80286D30)) return; // MSR.FP exception
    ctx.fpr[5] = ctx.ps1[5] = (float)std::fma(ctx.fpr[4], ctx.fpr[7], ctx.fpr[5]); // fmadds
    ++ctx.inst_count;
loc_80286D34: ;
    // 0x80286D34: C0030008
    if (ctx.require_fpu(0x80286D34)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 8); // lfs
    ++ctx.inst_count;
loc_80286D38: ;
    // 0x80286D38: EC6219FA
    if (ctx.require_fpu(0x80286D38)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.ps1[3] = (float)std::fma(ctx.fpr[2], ctx.fpr[7], ctx.fpr[3]); // fmadds
    ++ctx.inst_count;
loc_80286D3C: ;
    // 0x80286D3C: C0830020
    if (ctx.require_fpu(0x80286D3C)) return; // MSR.FP exception
    ctx.fpr[4] = ctx.ps1[4] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 32); // lfs
    ++ctx.inst_count;
loc_80286D40: ;
    // 0x80286D40: EC2009FA
    if (ctx.require_fpu(0x80286D40)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = (float)std::fma(ctx.fpr[0], ctx.fpr[7], ctx.fpr[1]); // fmadds
    ++ctx.inst_count;
loc_80286D44: ;
    // 0x80286D44: C0C40008
    if (ctx.require_fpu(0x80286D44)) return; // MSR.FP exception
    ctx.fpr[6] = ctx.ps1[6] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 8); // lfs
    ++ctx.inst_count;
loc_80286D48: ;
    // 0x80286D48: C0430024
    if (ctx.require_fpu(0x80286D48)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 36); // lfs
    ++ctx.inst_count;
loc_80286D4C: ;
    // 0x80286D4C: C0030028
    if (ctx.require_fpu(0x80286D4C)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 40); // lfs
    ++ctx.inst_count;
loc_80286D50: ;
    // 0x80286D50: ECA429BA
    if (ctx.require_fpu(0x80286D50)) return; // MSR.FP exception
    ctx.fpr[5] = ctx.ps1[5] = (float)std::fma(ctx.fpr[4], ctx.fpr[6], ctx.fpr[5]); // fmadds
    ++ctx.inst_count;
loc_80286D54: ;
    // 0x80286D54: C0830030
    if (ctx.require_fpu(0x80286D54)) return; // MSR.FP exception
    ctx.fpr[4] = ctx.ps1[4] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 48); // lfs
    ++ctx.inst_count;
loc_80286D58: ;
    // 0x80286D58: EC6219BA
    if (ctx.require_fpu(0x80286D58)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.ps1[3] = (float)std::fma(ctx.fpr[2], ctx.fpr[6], ctx.fpr[3]); // fmadds
    ++ctx.inst_count;
loc_80286D5C: ;
    // 0x80286D5C: C0430034
    if (ctx.require_fpu(0x80286D5C)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 52); // lfs
    ++ctx.inst_count;
loc_80286D60: ;
    // 0x80286D60: EC2009BA
    if (ctx.require_fpu(0x80286D60)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = (float)std::fma(ctx.fpr[0], ctx.fpr[6], ctx.fpr[1]); // fmadds
    ++ctx.inst_count;
loc_80286D64: ;
    // 0x80286D64: C0030038
    if (ctx.require_fpu(0x80286D64)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 56); // lfs
    ++ctx.inst_count;
loc_80286D68: ;
    // 0x80286D68: EC84282A
    if (ctx.require_fpu(0x80286D68)) return; // MSR.FP exception
    ctx.fpr[4] = ctx.ps1[4] = (float)(ctx.fpr[4] + ctx.fpr[5]); // fadds
    ++ctx.inst_count;
loc_80286D6C: ;
    // 0x80286D6C: EC42182A
    if (ctx.require_fpu(0x80286D6C)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = (float)(ctx.fpr[2] + ctx.fpr[3]); // fadds
    ++ctx.inst_count;
loc_80286D70: ;
    // 0x80286D70: EC00082A
    if (ctx.require_fpu(0x80286D70)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = (float)(ctx.fpr[0] + ctx.fpr[1]); // fadds
    ++ctx.inst_count;
loc_80286D74: ;
    // 0x80286D74: D0850000
    if (ctx.require_fpu(0x80286D74)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[5] + 0, (float)ctx.fpr[4]); // stfs f4, 0(r5)
    ++ctx.inst_count;
loc_80286D78: ;
    // 0x80286D78: D0450004
    if (ctx.require_fpu(0x80286D78)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[5] + 4, (float)ctx.fpr[2]); // stfs f2, 4(r5)
    ++ctx.inst_count;
loc_80286D7C: ;
    // 0x80286D7C: D0050008
    if (ctx.require_fpu(0x80286D7C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[5] + 8, (float)ctx.fpr[0]); // stfs f0, 8(r5)
    ++ctx.inst_count;
loc_80286D80: ;
    // 0x80286D80: 4E800020
    ctx.pc = ctx.lr; return;
}

void func_80286E28(CPUContext& ctx) {
    NWII_TRACE_CALL(0x80286E28, ctx);
    if (ctx.pc != 0x80286E28) {
        native_perf_census::GuestSwitch(ctx.pc,24); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x80286E2C: goto loc_80286E2C;
            case 0x80286E30: goto loc_80286E30;
            case 0x80286E34: goto loc_80286E34;
            case 0x80286E38: goto loc_80286E38;
            case 0x80286E3C: goto loc_80286E3C;
            case 0x80286E40: goto loc_80286E40;
            case 0x80286E44: goto loc_80286E44;
            case 0x80286E48: goto loc_80286E48;
            case 0x80286E4C: goto loc_80286E4C;
            case 0x80286E50: goto loc_80286E50;
            case 0x80286E54: goto loc_80286E54;
            case 0x80286E58: goto loc_80286E58;
            case 0x80286E5C: goto loc_80286E5C;
            case 0x80286E60: goto loc_80286E60;
            case 0x80286E64: goto loc_80286E64;
            case 0x80286E68: goto loc_80286E68;
            case 0x80286E6C: goto loc_80286E6C;
            case 0x80286E70: goto loc_80286E70;
            case 0x80286E74: goto loc_80286E74;
            case 0x80286E78: goto loc_80286E78;
            case 0x80286E7C: goto loc_80286E7C;
            case 0x80286E80: goto loc_80286E80;
            case 0x80286E84: goto loc_80286E84;
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_80286E28: ;
    // 0x80286E28: C0630010
    if (ctx.require_fpu(0x80286E28)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.ps1[3] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 16); // lfs
    ++ctx.inst_count;
loc_80286E2C: ;
    // 0x80286E2C: C0440004
    if (ctx.require_fpu(0x80286E2C)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 4); // lfs
    ++ctx.inst_count;
loc_80286E30: ;
    // 0x80286E30: C0230014
    if (ctx.require_fpu(0x80286E30)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 20); // lfs
    ++ctx.inst_count;
loc_80286E34: ;
    // 0x80286E34: ECA300B2
    if (ctx.require_fpu(0x80286E34)) return; // MSR.FP exception
    ctx.fpr[5] = ctx.ps1[5] = (float)(ctx.fpr[3] * ctx.fpr[2]); // fmuls
    ++ctx.inst_count;
loc_80286E38: ;
    // 0x80286E38: C0030018
    if (ctx.require_fpu(0x80286E38)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 24); // lfs
    ++ctx.inst_count;
loc_80286E3C: ;
    // 0x80286E3C: EC6100B2
    if (ctx.require_fpu(0x80286E3C)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.ps1[3] = (float)(ctx.fpr[1] * ctx.fpr[2]); // fmuls
    ++ctx.inst_count;
loc_80286E40: ;
    // 0x80286E40: C0830000
    if (ctx.require_fpu(0x80286E40)) return; // MSR.FP exception
    ctx.fpr[4] = ctx.ps1[4] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 0); // lfs
    ++ctx.inst_count;
loc_80286E44: ;
    // 0x80286E44: EC2000B2
    if (ctx.require_fpu(0x80286E44)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = (float)(ctx.fpr[0] * ctx.fpr[2]); // fmuls
    ++ctx.inst_count;
loc_80286E48: ;
    // 0x80286E48: C0E40000
    if (ctx.require_fpu(0x80286E48)) return; // MSR.FP exception
    ctx.fpr[7] = ctx.ps1[7] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 0); // lfs
    ++ctx.inst_count;
loc_80286E4C: ;
    // 0x80286E4C: ECC429FA
    if (ctx.require_fpu(0x80286E4C)) return; // MSR.FP exception
    ctx.fpr[6] = ctx.ps1[6] = (float)std::fma(ctx.fpr[4], ctx.fpr[7], ctx.fpr[5]); // fmadds
    ++ctx.inst_count;
loc_80286E50: ;
    // 0x80286E50: C0430004
    if (ctx.require_fpu(0x80286E50)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 4); // lfs
    ++ctx.inst_count;
loc_80286E54: ;
    // 0x80286E54: C0030008
    if (ctx.require_fpu(0x80286E54)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 8); // lfs
    ++ctx.inst_count;
loc_80286E58: ;
    // 0x80286E58: EC6219FA
    if (ctx.require_fpu(0x80286E58)) return; // MSR.FP exception
    ctx.fpr[3] = ctx.ps1[3] = (float)std::fma(ctx.fpr[2], ctx.fpr[7], ctx.fpr[3]); // fmadds
    ++ctx.inst_count;
loc_80286E5C: ;
    // 0x80286E5C: C0A30020
    if (ctx.require_fpu(0x80286E5C)) return; // MSR.FP exception
    ctx.fpr[5] = ctx.ps1[5] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 32); // lfs
    ++ctx.inst_count;
loc_80286E60: ;
    // 0x80286E60: EC2009FA
    if (ctx.require_fpu(0x80286E60)) return; // MSR.FP exception
    ctx.fpr[1] = ctx.ps1[1] = (float)std::fma(ctx.fpr[0], ctx.fpr[7], ctx.fpr[1]); // fmadds
    ++ctx.inst_count;
loc_80286E64: ;
    // 0x80286E64: C0840008
    if (ctx.require_fpu(0x80286E64)) return; // MSR.FP exception
    ctx.fpr[4] = ctx.ps1[4] = nl::rec_read_f32(ctx.mmu, ctx.gpr[4] + 8); // lfs
    ++ctx.inst_count;
loc_80286E68: ;
    // 0x80286E68: C0430024
    if (ctx.require_fpu(0x80286E68)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 36); // lfs
    ++ctx.inst_count;
loc_80286E6C: ;
    // 0x80286E6C: C0030028
    if (ctx.require_fpu(0x80286E6C)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = nl::rec_read_f32(ctx.mmu, ctx.gpr[3] + 40); // lfs
    ++ctx.inst_count;
loc_80286E70: ;
    // 0x80286E70: ECA5313A
    if (ctx.require_fpu(0x80286E70)) return; // MSR.FP exception
    ctx.fpr[5] = ctx.ps1[5] = (float)std::fma(ctx.fpr[5], ctx.fpr[4], ctx.fpr[6]); // fmadds
    ++ctx.inst_count;
loc_80286E74: ;
    // 0x80286E74: EC42193A
    if (ctx.require_fpu(0x80286E74)) return; // MSR.FP exception
    ctx.fpr[2] = ctx.ps1[2] = (float)std::fma(ctx.fpr[2], ctx.fpr[4], ctx.fpr[3]); // fmadds
    ++ctx.inst_count;
loc_80286E78: ;
    // 0x80286E78: EC00093A
    if (ctx.require_fpu(0x80286E78)) return; // MSR.FP exception
    ctx.fpr[0] = ctx.ps1[0] = (float)std::fma(ctx.fpr[0], ctx.fpr[4], ctx.fpr[1]); // fmadds
    ++ctx.inst_count;
loc_80286E7C: ;
    // 0x80286E7C: D0A50000
    if (ctx.require_fpu(0x80286E7C)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[5] + 0, (float)ctx.fpr[5]); // stfs f5, 0(r5)
    ++ctx.inst_count;
loc_80286E80: ;
    // 0x80286E80: D0450004
    if (ctx.require_fpu(0x80286E80)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[5] + 4, (float)ctx.fpr[2]); // stfs f2, 4(r5)
    ++ctx.inst_count;
loc_80286E84: ;
    // 0x80286E84: D0050008
    if (ctx.require_fpu(0x80286E84)) return; // MSR.FP exception
    nl::rec_write_f32(ctx.mmu, ctx.gpr[5] + 8, (float)ctx.fpr[0]); // stfs f0, 8(r5)
    ++ctx.inst_count;
loc_80286E88: ;
    // 0x80286E88: 4E800020
    ctx.pc = ctx.lr; return;
}

#if __has_include("native_lower_vector.h")
#include "native_lower_vector.h"
#else
#include "../runtime-overlays/native_lower_vector.h"
#endif

#include <algorithm>
#include <chrono>
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
void GX_DL_Snapshot(uint32_t) {}
bool GX_DL_Fetch(uint32_t, std::vector<uint8_t>&) { return false; }
bool native_gx_emitter_ready() { return false; }
uint32_t HW_Reg_Read32(uint32_t) { return 0; }
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

namespace nl = nwii::runtime::native_lower_test;
namespace nm = nwii::runtime::native_lower;
using nwii::runtime::CPUContext;

// ---------------------------------------------------------------------------
// Test driver
// ---------------------------------------------------------------------------

namespace {

// The two guest states the two paths run on.  They are reused across the
// cases: every case writes the same setup into both, so the two states stay
// identical.
CPUContext g_a;
CPUContext g_b;

volatile uint64_t g_sink = 0;

void reset_state(CPUContext& ctx) {
  for (int i = 0; i < 32; ++i) {
    ctx.gpr[i] = 0;
    ctx.fpr[i] = 0.0;
    ctx.ps1[i] = 0.0;
  }
  for (int i = 0; i < 8; ++i) ctx.cr[i] = nwii::runtime::ConditionField();
  ctx.lr = 0;
  ctx.ctr = 0;
  ctx.xer = 0;
  ctx.pc = 0;
  // MSR.FP is enabled: the guest's vector code uses floating point, and a
  // translated body that meets a disabled FP raises an exception instead of
  // computing.  The native entry has the same guard.
  ctx.msr = 0x2000u;
  ctx.fpscr = 0;
  ctx.inst_count = 0;
}

void put32(CPUContext& ctx, uint32_t address, uint32_t value) {
  ctx.mmu.write32(address, value);
}
void putf32(CPUContext& ctx, uint32_t address, float value) {
  ctx.mmu.write_f32(address, value);
}
void putf64(CPUContext& ctx, uint32_t address, double value) {
  ctx.mmu.write_f64(address, value);
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

// Runs the translated function and the native lowering on identical states
// and compares everything the guest can observe.
template <typename Setup, typename Translated, typename Native>
void run_pair(Group& grp, const std::string& label, const nl::VolatileSet& vol,
              Setup setup, Translated translated, Native native) {
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
  nl::Difference e = nl::compare_effects(original, lowered, "effect");
  if (e.any) d.note(e.text);
  e = nl::compare_effects(original_ports, lowered_ports, "port");
  if (e.any) d.note(e.text);
  std::vector<nl::Region> regions = nl::touched_regions(original, lowered);
  e = nl::compare_ram(g_a.mmu, g_b.mmu, regions);
  if (e.any) d.note(e.text);

  ++grp.cases;
  if (d.any) {
    ++grp.failures;
    if (grp.failures <= 4)
      std::printf("FAIL %s [%s]\n%s", grp.name, label.c_str(), d.text.c_str());
  }
}

// The guest's coefficient tables and literals of the atan lowering.
const uint32_t kAtanTable = 0x80485C38u;   // atanhi at +0, atanlo at +32, aT at +64
const uint32_t kSda = 0x8059FDC0u;          // the guest's small data area (r2)

void setup_atan_tables(CPUContext& ctx) {
  const double atanhi[4] = {4.63647609000806093515e-01, 7.85398163397448278999e-01,
                            9.82793723247329054082e-01, 1.57079632679489655800e+00};
  const double atanlo[4] = {2.26987774529616870924e-17, 3.06161699786838301793e-17,
                            1.39033110312309984516e-17, 6.12323399573676603587e-17};
  const double aT[11] = {3.33333333333329318027e-01, -1.99999999998764832476e-01,
                         1.42857142725034663711e-01, -1.11111104054623557880e-01,
                         9.09088713343650656196e-02, -7.69187620504482999495e-02,
                         6.66107313738753120669e-02, -5.83357013379057348645e-02,
                         4.97687799461593236017e-02, -3.65315727442169155270e-02,
                         1.62858201153657823623e-02};
  for (int i = 0; i < 4; ++i) putf64(ctx, kAtanTable + 8u * i, atanhi[i]);
  for (int i = 0; i < 4; ++i) putf64(ctx, kAtanTable + 32u + 8u * i, atanlo[i]);
  for (int i = 0; i < 11; ++i) putf64(ctx, kAtanTable + 64u + 8u * i, aT[i]);
  putf64(ctx, kSda - 15128u, 1.0e300);
  putf64(ctx, kSda - 15120u, 1.0);
  putf64(ctx, kSda - 15112u, 2.0);
  putf64(ctx, kSda - 15104u, 1.5);
  putf64(ctx, kSda - 15096u, -1.0);
}

// ===========================================================================
// 0x80219D40 - indexed_int_load
// ===========================================================================
void test_indexed_int_load() {
  Group& grp = group("vector.apply_indexed_int_load");
  nl::VolatileSet vol;   // the native path reproduces gpr0 and gpr3

  const uint32_t kBase = 0x80410000u;

  struct Case { uint32_t index; const char* label; };
  const Case cases[] = {
      {0u, "index 0"},
      {1u, "index 1"},
      {7u, "index 7"},
      {0x3FFFFFFFu, "index 0x3FFFFFFF (the scaled value wraps)"},
      {0x40000000u, "index 0x40000000 (the scaled value wraps to 0)"},
      {0x40000001u, "index 0x40000001"},
      {0xFFFFFFFFu, "index -1"},
      {0x80000000u, "index 0x80000000"},
  };

  for (const Case& c : cases) {
    run_pair(grp, c.label, vol,
             [&](CPUContext& ctx) {
               for (uint32_t i = 0; i < 8; ++i) put32(ctx, kBase + 28u + 4u * i, 0x1000u + i);
               ctx.gpr[3] = kBase;
               ctx.gpr[4] = c.index;
               ctx.pc = 0x80219D40u;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_80219D40(ctx); },
             [](CPUContext& ctx) { nm::apply_indexed_int_load(ctx); });
  }

  // The base pointer is the table itself (the read hits the first slot).
  run_pair(grp, "the table is the base", vol,
           [&](CPUContext& ctx) {
             put32(ctx, kBase + 28u, 0xDEADBEEFu);
             ctx.gpr[3] = kBase;
             ctx.gpr[4] = 0u;
             ctx.pc = 0x80219D40u;
             ctx.lr = 0x80010000u;
           },
           [](CPUContext& ctx) { func_80219D40(ctx); },
           [](CPUContext& ctx) { nm::apply_indexed_int_load(ctx); });

  // The base pointer is the table itself (the read hits the first slot).
  run_pair(grp, "the read hits the base word", vol,
           [&](CPUContext& ctx) {
             put32(ctx, kBase, 0x0BADF00Du);
             ctx.gpr[3] = kBase - 28u;
             ctx.gpr[4] = 0u;
             ctx.pc = 0x80219D40u;
             ctx.lr = 0x80010000u;
           },
           [](CPUContext& ctx) { func_80219D40(ctx); },
           [](CPUContext& ctx) { nm::apply_indexed_int_load(ctx); });
}

// ===========================================================================
// 0x8026B00C - sda2_global_swap
// ===========================================================================
void test_sda2_global_swap() {
  Group& grp = group("vector.apply_sda2_global_swap");
  nl::VolatileSet vol;   // the native path reproduces gpr0 and gpr3

  const uint32_t kSda2 = 0x8059A0E0u;        // the guest's small data area 2 (r13)
  const uint32_t kGlobal = kSda2 - 11832u;

  struct Case { uint32_t old_value; uint32_t new_value; const char* label; };
  const Case cases[] = {
      {0u, 0u, "zero to zero"},
      {0xCAFEBABEu, 0x12345678u, "ordinary swap"},
      {0xFFFFFFFFu, 0u, "all ones to zero"},
      {0u, 0xFFFFFFFFu, "zero to all ones"},
      {0x80000000u, 0x7FFFFFFFu, "sign bits"},
  };

  for (const Case& c : cases) {
    run_pair(grp, c.label, vol,
             [&](CPUContext& ctx) {
               put32(ctx, kGlobal, c.old_value);
               ctx.gpr[3] = c.new_value;
               ctx.gpr[13] = kSda2;
               ctx.pc = 0x8026B00Cu;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_8026B00C(ctx); },
             [](CPUContext& ctx) { nm::apply_sda2_global_swap(ctx); });
  }
}

// ===========================================================================
// 0x8028669C - fill_translate
// ===========================================================================
void test_fill_translate() {
  Group& grp = group("vector.apply_fill_translate");
  nl::VolatileSet vol;   // the native path reproduces fpr0..fpr2 and ps1 0..2

  const uint32_t kMatrix = 0x80420000u;
  const uint32_t kVector = 0x80430000u;

  struct Case { float v0, v1, v2; const char* label; };
  const Case cases[] = {
      {0.0f, 0.0f, 0.0f, "zeros"},
      {1.0f, 2.0f, 3.0f, "ordinary"},
      {-1.0f, -2.0f, -3.0f, "negative"},
      {1.0f, -0.0f, 0.0f, "signed zero"},
      {3.4028235e38f, -3.4028235e38f, 1.1754944e-38f, "extremes"},
      {1.4012985e-45f, 0.0f, -1.4012985e-45f, "denormals"},
      {std::numeric_limits<float>::infinity(), 1.0f, -std::numeric_limits<float>::infinity(), "infinities"},
  };

  for (const Case& c : cases) {
    run_pair(grp, c.label, vol,
             [&](CPUContext& ctx) {
               putf32(ctx, kVector + 0u, c.v0);
               putf32(ctx, kVector + 4u, c.v1);
               putf32(ctx, kVector + 8u, c.v2);
               for (uint32_t i = 0; i < 16; ++i) putf32(ctx, kMatrix + 4u * i, 0.0f);
               ctx.gpr[3] = kMatrix;
               ctx.gpr[4] = kVector;
               ctx.pc = 0x8028669Cu;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_8028669C(ctx); },
             [](CPUContext& ctx) { nm::apply_fill_translate(ctx); });
  }

  // The vector is the matrix's own translation row: the guest reads the three
  // floats before it writes them, so the copy is a no-op.
  run_pair(grp, "the vector is the translation row", vol,
           [&](CPUContext& ctx) {
             putf32(ctx, kMatrix + 48u, 1.5f);
             putf32(ctx, kMatrix + 52u, -2.5f);
             putf32(ctx, kMatrix + 56u, 3.5f);
             for (uint32_t i = 0; i < 12; ++i) putf32(ctx, kMatrix + 4u * i, 0.0f);
             ctx.gpr[3] = kMatrix;
             ctx.gpr[4] = kMatrix + 48u;
             ctx.pc = 0x8028669Cu;
             ctx.lr = 0x80010000u;
           },
           [](CPUContext& ctx) { func_8028669C(ctx); },
           [](CPUContext& ctx) { nm::apply_fill_translate(ctx); });
}

// ===========================================================================
// 0x80286D08 - matrix_transform
// ===========================================================================
void test_matrix_transform() {
  Group& grp = group("vector.apply_matrix_transform");
  nl::VolatileSet vol;   // the native path reproduces fpr0..fpr7 and ps1 0..7

  const uint32_t kMatrix = 0x80440000u;
  const uint32_t kSource = 0x80450000u;
  const uint32_t kDest = 0x80460000u;

  auto setup_matrix = [&](CPUContext& ctx, const float* m, const float* v) {
    for (uint32_t i = 0; i < 16; ++i) putf32(ctx, kMatrix + 4u * i, m[i]);
    for (uint32_t i = 0; i < 3; ++i) putf32(ctx, kSource + 4u * i, v[i]);
    for (uint32_t i = 0; i < 3; ++i) putf32(ctx, kDest + 4u * i, 0.0f);
    ctx.gpr[3] = kMatrix;
    ctx.gpr[4] = kSource;
    ctx.gpr[5] = kDest;
    ctx.pc = 0x80286D08u;
    ctx.lr = 0x80010000u;
  };

  const float identity[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
  const float affine[16] = {2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 4, 0, 5, 6, 7, 1};
  const float rotation[16] = {0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
  const float wild[16] = {1e20f, 1e-20f, -3.5f, 0, 0.1f, 12345.678f, -0.0001f, 0,
                          7.0f, 0, 0, 0, -1e-30f, 2e30f, 3, 1};
  const float v_plain[3] = {1.0f, 2.0f, 3.0f};
  const float v_zero[3] = {0.0f, 0.0f, 0.0f};
  const float v_neg[3] = {-1.5f, 0.25f, 1e10f};
  const float v_special[3] = {std::numeric_limits<float>::infinity(), 0.0f, -0.0f};

  run_pair(grp, "identity", vol,
           [&](CPUContext& ctx) { setup_matrix(ctx, identity, v_plain); },
           [](CPUContext& ctx) { func_80286D08(ctx); },
           [](CPUContext& ctx) { nm::apply_matrix_transform(ctx); });
  run_pair(grp, "affine", vol,
           [&](CPUContext& ctx) { setup_matrix(ctx, affine, v_plain); },
           [](CPUContext& ctx) { func_80286D08(ctx); },
           [](CPUContext& ctx) { nm::apply_matrix_transform(ctx); });
  run_pair(grp, "rotation", vol,
           [&](CPUContext& ctx) { setup_matrix(ctx, rotation, v_plain); },
           [](CPUContext& ctx) { func_80286D08(ctx); },
           [](CPUContext& ctx) { nm::apply_matrix_transform(ctx); });
  run_pair(grp, "zero vector", vol,
           [&](CPUContext& ctx) { setup_matrix(ctx, affine, v_zero); },
           [](CPUContext& ctx) { func_80286D08(ctx); },
           [](CPUContext& ctx) { nm::apply_matrix_transform(ctx); });
  run_pair(grp, "negative vector", vol,
           [&](CPUContext& ctx) { setup_matrix(ctx, affine, v_neg); },
           [](CPUContext& ctx) { func_80286D08(ctx); },
           [](CPUContext& ctx) { nm::apply_matrix_transform(ctx); });
  run_pair(grp, "wide range", vol,
           [&](CPUContext& ctx) { setup_matrix(ctx, wild, v_plain); },
           [](CPUContext& ctx) { func_80286D08(ctx); },
           [](CPUContext& ctx) { nm::apply_matrix_transform(ctx); });
  run_pair(grp, "infinite component", vol,
           [&](CPUContext& ctx) { setup_matrix(ctx, affine, v_special); },
           [](CPUContext& ctx) { func_80286D08(ctx); },
           [](CPUContext& ctx) { nm::apply_matrix_transform(ctx); });

  // The destination is the source: the guest reads all fifteen elements
  // before it writes the three results.
  run_pair(grp, "the destination is the source", vol,
           [&](CPUContext& ctx) {
             for (uint32_t i = 0; i < 16; ++i) putf32(ctx, kMatrix + 4u * i, affine[i]);
             for (uint32_t i = 0; i < 3; ++i) putf32(ctx, kSource + 4u * i, v_plain[i]);
             for (uint32_t i = 0; i < 3; ++i) putf32(ctx, kSource + 12u + 4u * i, 0.0f);
             ctx.gpr[3] = kMatrix;
             ctx.gpr[4] = kSource;
             ctx.gpr[5] = kSource + 12u;
             ctx.pc = 0x80286D08u;
             ctx.lr = 0x80010000u;
           },
           [](CPUContext& ctx) { func_80286D08(ctx); },
           [](CPUContext& ctx) { nm::apply_matrix_transform(ctx); });
}

// ===========================================================================
// 0x8028A914 / 0x8028A974 - vector_x_transform
// ===========================================================================
void test_vector_x_transform() {
  Group& grp = group("vector.apply_vector_x_transform");
  nl::VolatileSet vol;   // the native path reproduces fpr0..fpr7, ps1 0..7, gpr0,
                         // gpr3, gpr4 and gpr5; gpr1 and gpr31 keep their entry
                         // values in both paths.

  const uint32_t kMatrix = 0x80440000u;
  const uint32_t kThis = 0x80470000u;
  const uint32_t kSp = 0x81000000u;

  const float affine[16] = {2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 4, 0, 5, 6, 7, 1};
  const float rotation[16] = {0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
  const float special[16] = {std::numeric_limits<float>::infinity(), 0.0f, 0.0f, 0.0f,
                             0.0f, -std::numeric_limits<float>::infinity(), 0.0f, 0.0f,
                             0.0f, 0.0f, 1.4012985e-45f, 0.0f,
                             0.0f, 0.0f, 0.0f, 1.0f};

  auto setup = [&](CPUContext& ctx, const float* m, float x, float y, float z) {
    for (uint32_t i = 0; i < 16; ++i) putf32(ctx, kMatrix + 4u * i, m[i]);
    putf32(ctx, kThis + 0u, x);
    putf32(ctx, kThis + 4u, y);
    putf32(ctx, kThis + 8u, z);
    ctx.gpr[1] = kSp;
    ctx.gpr[3] = kThis;
    ctx.gpr[4] = kMatrix;
    ctx.gpr[31] = 0x12345678u;
    ctx.lr = 0x80010000u;
  };

  run_pair(grp, "affine", vol,
           [&](CPUContext& ctx) {
             setup(ctx, affine, 1.0f, 2.0f, 3.0f);
             ctx.pc = 0x8028A914u;
           },
           [](CPUContext& ctx) { func_8028A914(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_x_transform(ctx); });
  run_pair(grp, "rotation", vol,
           [&](CPUContext& ctx) {
             setup(ctx, rotation, 1.0f, 2.0f, 3.0f);
             ctx.pc = 0x8028A914u;
           },
           [](CPUContext& ctx) { func_8028A914(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_x_transform(ctx); });
  run_pair(grp, "infinities and denormals in the matrix", vol,
           [&](CPUContext& ctx) {
             setup(ctx, special, 1.0f, 2.0f, 3.0f);
             ctx.pc = 0x8028A914u;
           },
           [](CPUContext& ctx) { func_8028A914(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_x_transform(ctx); });
  run_pair(grp, "the vector is the matrix's translation row", vol,
           [&](CPUContext& ctx) {
             setup(ctx, affine, 1.0f, 2.0f, 3.0f);
             ctx.gpr[3] = kMatrix + 48u;
             ctx.pc = 0x8028A914u;
           },
           [](CPUContext& ctx) { func_8028A914(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_x_transform(ctx); });
}

// ===========================================================================
// 0x8028A974 - vector_rotate
//
// The rotation twin of 0x8028A914: the same wrapper, but it calls 0x80286E28
// (RadicalMathLibrary::Matrix::RotateVector) instead of 0x80286D08
// (Matrix::Transform), so the vector is rotated without the translation row.
//
// The oracle is the translated body of 0x8028A974; the translated body of its
// callee 0x80286E28 is provided by this file (the plain-leaf stage has
// already replaced the definition of 0x80286E28 in the generated tree with
// its own kernel wrapper).
// ===========================================================================
void test_vector_rotate() {
  Group& grp = group("vector.apply_vector_rotate");
  nl::VolatileSet vol;   // the native path reproduces fpr0..fpr7, ps1 0..7, gpr0,
                         // gpr3, gpr4 and gpr5; gpr1 and gpr31 keep their entry
                         // values in both paths.

  const uint32_t kMatrix = 0x80440000u;
  const uint32_t kThis = 0x80470000u;
  const uint32_t kSp = 0x81000000u;

  const float affine[16] = {2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 4, 0, 5, 6, 7, 1};
  const float rotation[16] = {0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
  const float identity[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
  const float special[16] = {std::numeric_limits<float>::infinity(), 0.0f, 0.0f, 0.0f,
                             0.0f, -std::numeric_limits<float>::infinity(), 0.0f, 0.0f,
                             0.0f, 0.0f, 1.4012985e-45f, 0.0f,
                             0.0f, 0.0f, 0.0f, 1.0f};
  const float wild[16] = {1e20f, 1e-20f, -3.5f, 0, 0.1f, 12345.678f, -0.0001f, 0,
                          7.0f, 0, 0, 0, -1e-30f, 2e30f, 3, 1};

  auto setup = [&](CPUContext& ctx, const float* m, float x, float y, float z) {
    for (uint32_t i = 0; i < 16; ++i) putf32(ctx, kMatrix + 4u * i, m[i]);
    putf32(ctx, kThis + 0u, x);
    putf32(ctx, kThis + 4u, y);
    putf32(ctx, kThis + 8u, z);
    ctx.gpr[1] = kSp;
    ctx.gpr[3] = kThis;
    ctx.gpr[4] = kMatrix;
    ctx.gpr[31] = 0x12345678u;
    ctx.lr = 0x80010000u;
  };

  // Ordinary.
  run_pair(grp, "affine", vol,
           [&](CPUContext& ctx) {
             setup(ctx, affine, 1.0f, 2.0f, 3.0f);
             ctx.pc = 0x8028A974u;
           },
           [](CPUContext& ctx) { func_8028A974(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_rotate(ctx); });
  run_pair(grp, "rotation", vol,
           [&](CPUContext& ctx) {
             setup(ctx, rotation, 1.0f, 2.0f, 3.0f);
             ctx.pc = 0x8028A974u;
           },
           [](CPUContext& ctx) { func_8028A974(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_rotate(ctx); });
  // Boundary.
  run_pair(grp, "the identity matrix", vol,
           [&](CPUContext& ctx) {
             setup(ctx, identity, 1.0f, 2.0f, 3.0f);
             ctx.pc = 0x8028A974u;
           },
           [](CPUContext& ctx) { func_8028A974(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_rotate(ctx); });
  run_pair(grp, "a zero vector", vol,
           [&](CPUContext& ctx) {
             setup(ctx, affine, 0.0f, 0.0f, 0.0f);
             ctx.pc = 0x8028A974u;
           },
           [](CPUContext& ctx) { func_8028A974(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_rotate(ctx); });
  run_pair(grp, "a negative zero component", vol,
           [&](CPUContext& ctx) {
             setup(ctx, affine, -0.0f, 2.0f, -0.0f);
             ctx.pc = 0x8028A974u;
           },
           [](CPUContext& ctx) { func_8028A974(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_rotate(ctx); });
  // Aliasing: the vector is the matrix's own translation row, so the source
  // overlaps the matrix the callee reads.
  run_pair(grp, "the vector is the matrix's translation row", vol,
           [&](CPUContext& ctx) {
             setup(ctx, affine, 1.0f, 2.0f, 3.0f);
             ctx.gpr[3] = kMatrix + 48u;
             ctx.pc = 0x8028A974u;
           },
           [](CPUContext& ctx) { func_8028A974(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_rotate(ctx); });
  // Aliasing: the source overlaps the three rows the callee reads.
  run_pair(grp, "the vector overlaps the matrix rows", vol,
           [&](CPUContext& ctx) {
             setup(ctx, affine, 1.0f, 2.0f, 3.0f);
             ctx.gpr[3] = kMatrix + 16u;
             ctx.pc = 0x8028A974u;
           },
           [](CPUContext& ctx) { func_8028A974(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_rotate(ctx); });
  // Adversarial.
  run_pair(grp, "infinities and denormals in the matrix", vol,
           [&](CPUContext& ctx) {
             setup(ctx, special, 1.0f, 2.0f, 3.0f);
             ctx.pc = 0x8028A974u;
           },
           [](CPUContext& ctx) { func_8028A974(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_rotate(ctx); });
  run_pair(grp, "a NaN in the vector", vol,
           [&](CPUContext& ctx) {
             setup(ctx, affine, std::numeric_limits<float>::quiet_NaN(), 2.0f, 3.0f);
             ctx.pc = 0x8028A974u;
           },
           [](CPUContext& ctx) { func_8028A974(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_rotate(ctx); });
  run_pair(grp, "an infinite vector component", vol,
           [&](CPUContext& ctx) {
             setup(ctx, affine, 1.0f, std::numeric_limits<float>::infinity(), 3.0f);
             ctx.pc = 0x8028A974u;
           },
           [](CPUContext& ctx) { func_8028A974(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_rotate(ctx); });
  run_pair(grp, "a very large matrix", vol,
           [&](CPUContext& ctx) {
             setup(ctx, wild, 1.0f, 2.0f, 3.0f);
             ctx.pc = 0x8028A974u;
           },
           [](CPUContext& ctx) { func_8028A974(ctx); },
           [](CPUContext& ctx) { nm::apply_vector_rotate(ctx); });
}

// ===========================================================================
// 0x803A2CC8 - atan_double
// ===========================================================================
void test_atan_double() {
  Group& grp = group("vector.apply_atan_double");
  // The guest's argument reduction and polynomial leave their scratch
  // registers and the condition register behind; the native path reproduces
  // fpr1, gpr0, gpr3, gpr4, gpr5 and gpr6 and leaves the rest EABI-volatile.
  nl::VolatileSet vol;
  vol.allow_fpr(0);
  vol.allow_fpr(2);
  vol.allow_fpr(3);
  vol.allow_fpr(4);
  vol.allow_fpr(5);
  vol.allow_fpr(6);
  vol.allow_fpr(7);
  vol.allow_fpr(8);
  vol.allow_fpr(9);
  vol.allow_fpr(10);
  vol.allow_fpr(11);
  vol.allow_fpr(12);
  vol.allow_fpr(13);
  vol.allow_cr(0);

  const uint32_t kSp = 0x81000000u;

  struct Case { double x; const char* label; };
  const Case cases[] = {
      {std::nan(""), "NaN"},
      {std::numeric_limits<double>::infinity(), "+infinity"},
      {-std::numeric_limits<double>::infinity(), "-infinity"},
      {1e300, "1e300"},
      {-1e300, "-1e300"},
      {1e-30, "tiny (the early return)"},
      {-1e-30, "tiny negative"},
      {1e-320, "the smallest normal"},
      {0.0, "zero"},
      {-0.0, "minus zero"},
      {0.1, "0.1 (id = -1)"},
      {-0.1, "-0.1 (id = -1)"},
      {0.4, "0.4 (id = -1)"},
      {0.4375, "0.4375 (the id = -1 boundary)"},
      {0.5, "0.5 (id = 0)"},
      {-0.5, "-0.5 (id = 0)"},
      {0.7, "0.7 (id = 1)"},
      {-0.7, "-0.7 (id = 1)"},
      {1.0, "1.0 (id = 1)"},
      {1.1875, "1.1875 (the id = 1 boundary)"},
      {2.0, "2.0 (id = 2)"},
      {-2.0, "-2.0 (id = 2)"},
      {2.4375, "2.4375 (the id = 2 boundary)"},
      {100.0, "100 (id = 3)"},
      {-100.0, "-100 (id = 3)"},
      {3.5e33, "3.5e33 (the id = 3 boundary)"},
      {3.6e33, "3.6e33 (the large argument)"},
  };

  for (const Case& c : cases) {
    run_pair(grp, c.label, vol,
             [&](CPUContext& ctx) {
               setup_atan_tables(ctx);
               ctx.fpr[1] = c.x;
               ctx.gpr[1] = kSp;
               ctx.gpr[2] = kSda;
               ctx.pc = 0x803A2CC8u;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_803A2CC8(ctx); },
             [](CPUContext& ctx) { nm::apply_atan_double(ctx); });
  }

  // The bit patterns around the argument-reduction thresholds.
  std::mt19937 rng(0xA7A41234u);
  for (int i = 0; i < 24; ++i) {
    uint64_t bits = rng();
    // Keep the exponent in the range the reduction uses.
    bits = (bits & 0x800FFFFFFFFFFFFFull) | (0x3F0000000000000ull + (uint64_t)(rng() % 0x00C0000000000000ull));
    double x;
    std::memcpy(&x, &bits, 8);
    char label[64];
    std::snprintf(label, sizeof(label), "random %d", i);
    run_pair(grp, label, vol,
             [&](CPUContext& ctx) {
               setup_atan_tables(ctx);
               ctx.fpr[1] = x;
               ctx.gpr[1] = kSp;
               ctx.gpr[2] = kSda;
               ctx.pc = 0x803A2CC8u;
               ctx.lr = 0x80010000u;
             },
             [](CPUContext& ctx) { func_803A2CC8(ctx); },
             [](CPUContext& ctx) { nm::apply_atan_double(ctx); });
  }
}

// ===========================================================================
// Paired micro-benchmark
//
// The translated function and the native lowering run alternately on the same
// input state; the results are consumed so the compiler cannot delete the
// work.  The reported numbers are a local component measurement (host
// nanoseconds per call), not a whole-game frame rate.
// ===========================================================================
template <typename F>
double sample_ns(F&& body, int iterations) {
  const auto t0 = std::chrono::steady_clock::now();
  for (int i = 0; i < iterations; ++i) {
    // A compiler barrier keeps the guest loads inside the loop: without it
    // the compiler hoists the (loop invariant) reads of the translated
    // function out of the benchmark and measures nothing.
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

template <typename Translated, typename Native>
void benchmark(const char* name, uint32_t address, Translated translated, Native native,
               const char* note) {
  // The caller sets the input state up; the benchmark must not reset it.
  g_a.lr = 0x80010000u;
  nl::g_recording = false;

  const int kIterations = 20000;
  const int kRepeats = 9;

  // The guest instructions both paths retire for one call.
  g_a.pc = address;
  const uint64_t before = g_a.inst_count;
  translated(g_a);
  const uint64_t translated_insts = g_a.inst_count - before;
  g_a.pc = address;
  const uint64_t before2 = g_a.inst_count;
  native(g_a);
  const uint64_t native_insts = g_a.inst_count - before2;

  for (int i = 0; i < 2000; ++i) {  // warm up
    g_a.pc = address;
    translated(g_a);
    g_a.pc = address;
    native(g_a);
  }

  std::vector<double> translated_samples;
  std::vector<double> native_samples;
  for (int r = 0; r < kRepeats; ++r) {  // alternate the two paths
    translated_samples.push_back(sample_ns([&] {
      g_a.pc = address;
      translated(g_a);
      g_sink += g_a.gpr[3] + (uint64_t)g_a.fpr[0];
    }, kIterations));
    native_samples.push_back(sample_ns([&] {
      g_a.pc = address;
      native(g_a);
      g_sink += g_a.gpr[3] + (uint64_t)g_a.fpr[0];
    }, kIterations));
  }
  const double translated_ns = median_of(translated_samples);
  const double native_ns = median_of(native_samples);
  // The spread of the repetitions is the noise of the measurement.
  double tmin = translated_samples[0], tmax = translated_samples[0];
  double nmin = native_samples[0], nmax = native_samples[0];
  for (double s : translated_samples) { tmin = std::min(tmin, s); tmax = std::max(tmax, s); }
  for (double s : native_samples) { nmin = std::min(nmin, s); nmax = std::max(nmax, s); }

  std::printf("NATIVE_LOWERING_BENCH %-10s translated %8.1f ns/call [%7.1f,%7.1f]   "
              "native %8.1f ns/call [%7.1f,%7.1f]   speedup %5.2fx   "
              "guest insts %llu/%llu   %s\n",
              name, translated_ns, tmin, tmax, native_ns, nmin, nmax,
              native_ns > 0.0 ? translated_ns / native_ns : 0.0,
              (unsigned long long)translated_insts, (unsigned long long)native_insts, note);
}

}  // namespace

int main() {
#ifndef NATIVE_LOWERING_TRACE_MEMORY
  std::printf("NATIVE_LOWERING_TESTS note: built without NATIVE_LOWERING_TRACE_MEMORY; the native "
              "path records no memory effects, so the effect-log comparison cannot pass.  The "
              "differential test needs -DNATIVE_LOWERING_TRACE_MEMORY.\n");
#endif
  test_indexed_int_load();
  test_sda2_global_swap();
  test_fill_translate();
  test_matrix_transform();
  test_vector_x_transform();
  test_vector_rotate();
  test_atan_double();

  int failures = 0;
  for (int i = 0; i < g_group_count; ++i) {
    const Group& g = g_groups[i];
    std::printf("NATIVE_LOWERING_TESTS %s %d/%d\n", g.name, g.cases - g.failures, g.cases);
    failures += g.failures;
  }
  if (failures) std::printf("NATIVE_LOWERING_TESTS FAILED\n");

  // ---------------------------------------------------------------------
  // The paired micro-benchmark.  Both paths see the same input state.
  // ---------------------------------------------------------------------
  {
    reset_state(g_a);
    g_a.lr = 0x80010000u;
    g_a.gpr[3] = 0x80410000u;
    g_a.gpr[4] = 3u;
    g_a.gpr[13] = 0x8059A0E0u;
    put32(g_a, 0x80410000u + 28u + 12u, 0x12345678u);
    put32(g_a, 0x8059A0E0u - 11832u, 0xCAFEBABEu);
    benchmark("80219D40", 0x80219D40u, func_80219D40, nm::apply_indexed_int_load,
              "one 32 bit RAM read");
  }
  {
    reset_state(g_a);
    g_a.lr = 0x80010000u;
    g_a.gpr[3] = 0x1234u;
    g_a.gpr[13] = 0x8059A0E0u;
    put32(g_a, 0x8059A0E0u - 11832u, 0xCAFEBABEu);
    benchmark("8026B00C", 0x8026B00Cu, func_8026B00C, nm::apply_sda2_global_swap,
              "one RAM read and one RAM write");
  }
  {
    reset_state(g_a);
    g_a.lr = 0x80010000u;
    g_a.gpr[3] = 0x80420000u;
    g_a.gpr[4] = 0x80430000u;
    for (int i = 0; i < 3; ++i) putf32(g_a, 0x80430000u + 4u * i, 1.0f + i);
    benchmark("8028669C", 0x8028669Cu, func_8028669C, nm::apply_fill_translate,
              "three float reads and three float writes");
  }
  {
    reset_state(g_a);
    g_a.lr = 0x80010000u;
    g_a.gpr[3] = 0x80440000u;
    g_a.gpr[4] = 0x80450000u;
    g_a.gpr[5] = 0x80460000u;
    for (int i = 0; i < 16; ++i) putf32(g_a, 0x80440000u + 4u * i, 0.5f + i);
    for (int i = 0; i < 3; ++i) putf32(g_a, 0x80450000u + 4u * i, 1.0f + i);
    benchmark("80286D08", 0x80286D08u, func_80286D08, nm::apply_matrix_transform,
              "15 float reads, 12 fused multiply-adds, 3 float writes");
  }
  {
    reset_state(g_a);
    g_a.lr = 0x80010000u;
    g_a.gpr[1] = 0x81000000u;
    g_a.gpr[3] = 0x80470000u;
    g_a.gpr[4] = 0x80440000u;
    for (int i = 0; i < 16; ++i) putf32(g_a, 0x80440000u + 4u * i, 0.5f + i);
    for (int i = 0; i < 3; ++i) putf32(g_a, 0x80470000u + 4u * i, 1.0f + i);
    benchmark("8028A914", 0x8028A914u, func_8028A914, nm::apply_vector_x_transform,
              "the group: the matrix transform and the copy back");
  }
  {
    reset_state(g_a);
    g_a.lr = 0x80010000u;
    g_a.gpr[1] = 0x81000000u;
    g_a.gpr[3] = 0x80470000u;
    g_a.gpr[4] = 0x80440000u;
    for (int i = 0; i < 16; ++i) putf32(g_a, 0x80440000u + 4u * i, 0.5f + i);
    for (int i = 0; i < 3; ++i) putf32(g_a, 0x80470000u + 4u * i, 1.0f + i);
    benchmark("8028A974", 0x8028A974u, func_8028A974, nm::apply_vector_rotate,
              "the rotation group: the vector rotation and the copy back");
  }
  {
    reset_state(g_a);
    g_a.lr = 0x80010000u;
    setup_atan_tables(g_a);
    g_a.fpr[1] = 0.5;
    g_a.gpr[1] = 0x81000000u;
    g_a.gpr[2] = kSda;
    benchmark("803A2CC8", 0x803A2CC8u, func_803A2CC8, nm::apply_atan_double,
              "the argument reduction and the polynomial");
  }
  std::printf("NATIVE_LOWERING_BENCH sink %llu\n", (unsigned long long)g_sink);
  return failures ? 1 : 0;
}
