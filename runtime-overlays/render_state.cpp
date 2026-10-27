// NATIVE_RENDERER_STATE_SPLIT_V1: see tools/fix_renderer_state_split.py and render_state.h.
#include "runtime/gx/render_state.h"
#include <cstring>

namespace nwii::runtime::gx {

namespace {
// Copies the parse-owned fields between a GXState and a ParseSnapshot.
void ParseOwnedOut(ParseSnapshot& s, const GXState& g) {
    std::memcpy(s.cp, g.cp, sizeof(s.cp));
    std::memcpy(s.vat, g.vat, sizeof(s.vat));
    std::memcpy(s.arrayBase, g.arrayBase, sizeof(s.arrayBase));
    std::memcpy(s.arrayStride, g.arrayStride, sizeof(s.arrayStride));
    s.defPosMtxIdx = g.defPosMtxIdx;
    std::memcpy(s.defTexMtxIdx, g.defTexMtxIdx, sizeof(s.defTexMtxIdx));
}
void ParseOwnedIn(GXState& g, const ParseSnapshot& s) {
    std::memcpy(g.cp, s.cp, sizeof(g.cp));
    std::memcpy(g.vat, s.vat, sizeof(g.vat));
    std::memcpy(g.arrayBase, s.arrayBase, sizeof(g.arrayBase));
    std::memcpy(g.arrayStride, s.arrayStride, sizeof(g.arrayStride));
    g.defPosMtxIdx = s.defPosMtxIdx;
    std::memcpy(g.defTexMtxIdx, s.defTexMtxIdx, sizeof(g.defTexMtxIdx));
}
} // namespace

GXState& RenderState() {
    static GXState* state = new GXState();
    return *state;
}

ParseSnapshot& RenderBatchSnapshot() {
    static ParseSnapshot snap{};
    return snap;
}

void RenderStateBegin(const ParseSnapshot& snap) {
    ParseOwnedIn(RenderState(), snap);
}

void RenderStateExportToParse() {
    ParseSnapshot keep;
    ParseOwnedOut(keep, gx::g_state);
    std::memcpy(&gx::g_state, &RenderState(), sizeof(GXState));
    ParseOwnedIn(gx::g_state, keep);
}

// NATIVE_TEXTURE_CPU_GL_SPLIT_V1: bumped whenever RenderState() is replaced wholesale, so the texture prepass rebuilds its mirror.
static uint64_t g_render_state_epoch = 0;
uint64_t RenderStateEpoch() { return g_render_state_epoch; }

void RenderStateImportFromParse() {
    std::memcpy(&RenderState(), &gx::g_state, sizeof(GXState));
    ++g_render_state_epoch;
}

} // namespace nwii::runtime::gx
