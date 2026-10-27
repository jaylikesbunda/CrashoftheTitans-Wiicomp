// Unit test for native_gx_work::FallbackGates (runtime-overlays/native_gx_work_metrics.h, NATIVE_FALLBACK_REASONS_V1).
// The VAT and draw types are stand-ins with the same field names and enum values as runtime/gx_state.h and gx_command.h.
// Build & run: tools/check_fallback_gates.py (it provides runtime/native_gx_work_metrics.h on the include path).
#include <cstdint>
#include <cstdio>
#include "runtime/native_gx_work_metrics.h"

namespace {
enum class VtxAttrMask { None = 0, Direct = 1, Index8 = 2, Index16 = 3 };
enum class VtxAttrType { U8 = 0, S8 = 1, U16 = 2, S16 = 3, F32 = 4 };

struct VAT {
    bool posMatIdx = false;
    bool texMatIdx[8] = {};
    VtxAttrMask posMask = VtxAttrMask::None;
    VtxAttrType posType = VtxAttrType::F32;
    bool posElements = true;
    VtxAttrMask nrmMask = VtxAttrMask::None;
    VtxAttrType nrmType = VtxAttrType::F32;
    bool nrmElements = false;
    bool nrmIndex3 = false;
    VtxAttrMask clrMask[2] = {VtxAttrMask::None, VtxAttrMask::None};
    VtxAttrType clrType[2] = {VtxAttrType::U8, VtxAttrType::U8};
    bool clrElements[2] = {};
    VtxAttrMask texMask[8] = {};
    VtxAttrType texType[8] = {VtxAttrType::F32};
    bool texElements[8] = {true};
};

struct Raw {
    VAT vat;
    unsigned count = 40;
    unsigned arrayStride[12] = {12};
};

int failures = 0, checks = 0;
void expect(bool ok, const char* what) {
    ++checks;
    if (!ok) { ++failures; std::printf("FAIL %s\n", what); }
}
bool gate(unsigned bits, unsigned bit) { return (bits >> bit) & 1u; }

// Bit indices, in FallbackGates order.
enum {
    kCountBelow32 = 0, kPosDirect = 1, kPosNone = 2, kPosTypeNotF32 = 3, kPos2D = 4, kPosMatIdx = 5, kTexMatIdx = 6,
    kExtraTex = 7, kTexMaskMismatch = 8, kTexTypeU8S8 = 9, kColourSecond = 10, kColourMismatch = 11, kNormalMismatch = 12,
    kNormalNbt = 13, kNormalFormat = 14, kDirectIndexMix = 15, kStride = 16, kWideInputs = 17
};

// Index16 position, normals and texcoords, F32 throughout: the fast path. No gate fails.
Raw indexed() {
    Raw r;
    r.vat.posMask = VtxAttrMask::Index16;
    r.vat.nrmMask = VtxAttrMask::Index16;
    r.vat.texMask[0] = VtxAttrMask::Index16;
    return r;
}
}  // namespace

int main() {
    using native_gx_work::FallbackGates;
    unsigned bits = FallbackGates(indexed(), 2);
    expect(bits == 0, "an all-indexed F32 draw of 40 vertices fails no gate");

    Raw small = indexed();
    small.count = 20;
    expect(gate(FallbackGates(small, 2), kCountBelow32), "fewer than 32 vertices fails the specialisation count gate");

    Raw direct = indexed();
    direct.vat.posMask = VtxAttrMask::Direct;
    bits = FallbackGates(direct, 2);
    expect(gate(bits, kPosDirect) && !gate(bits, kPosNone), "a direct position is counted as direct");
    expect(gate(bits, kDirectIndexMix), "direct position with indexed texcoords is a direct/indexed mix");

    Raw none = indexed();
    none.vat.posMask = VtxAttrMask::None;
    expect(gate(FallbackGates(none, 2), kPosNone), "an absent position is counted as none");

    Raw u8pos = indexed();
    u8pos.vat.posType = VtxAttrType::U8;
    expect(gate(FallbackGates(u8pos, 2), kPosTypeNotF32), "a non-F32 position fails the type gate");

    Raw twod = indexed();
    twod.vat.posElements = false;
    expect(gate(FallbackGates(twod, 2), kPos2D), "a 2D position fails the element gate");

    Raw matidx = indexed();
    matidx.vat.posMatIdx = true;
    expect(gate(FallbackGates(matidx, 2), kPosMatIdx), "a position matrix index is counted");

    Raw texmat = indexed();
    texmat.vat.texMatIdx[3] = true;
    expect(gate(FallbackGates(texmat, 2), kTexMatIdx), "a texture matrix index is counted");

    Raw extra = indexed();
    extra.vat.texMask[2] = VtxAttrMask::Direct;
    expect(gate(FallbackGates(extra, 2), kExtraTex), "a second texcoord set is counted");

    Raw texmask = indexed();
    texmask.vat.texMask[0] = VtxAttrMask::Direct;
    expect(gate(FallbackGates(texmask, 2), kTexMaskMismatch), "texcoords with another index type are counted");

    Raw texu8 = indexed();
    texu8.vat.texType[0] = VtxAttrType::U8;
    expect(gate(FallbackGates(texu8, 2), kTexTypeU8S8), "U8 texcoords fail the texcoord format gate");

    Raw clr2 = indexed();
    clr2.vat.clrMask[1] = VtxAttrMask::Direct;
    expect(gate(FallbackGates(clr2, 2), kColourSecond), "a second colour is counted");

    Raw clr = indexed();
    clr.vat.clrMask[0] = VtxAttrMask::Direct;
    expect(gate(FallbackGates(clr, 2), kColourMismatch), "a colour with another index type is counted");

    Raw nrm = indexed();
    nrm.vat.nrmMask = VtxAttrMask::Direct;
    expect(gate(FallbackGates(nrm, 2), kNormalMismatch), "normals with another index type are counted");

    Raw nbt = indexed();
    nbt.vat.nrmIndex3 = true;
    expect(gate(FallbackGates(nbt, 2), kNormalNbt), "NBT normal indices are counted");

    Raw nrmfmt = indexed();
    nrmfmt.vat.nrmType = VtxAttrType::S16;
    expect(gate(FallbackGates(nrmfmt, 2), kNormalFormat), "a normal format other than byte or float is counted");

    Raw stride = indexed();
    stride.vat.posMask = VtxAttrMask::Direct;
    stride.arrayStride[0] = 16;
    expect(gate(FallbackGates(stride, 2), kStride), "a position stride other than 12 bytes is counted");

    expect(gate(FallbackGates(indexed(), 3), kWideInputs), "more than two packed inputs is counted");

    std::printf("fallback gates: %d checks, failures %d\n", checks, failures);
    std::printf("%s\n", failures ? "FALLBACK_GATES_TESTS FAIL" : "FALLBACK_GATES_TESTS PASS");
    return failures ? 1 : 0;
}
