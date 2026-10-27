#pragma once
// NATIVE_RENDERER_STATE_SPLIT_V1: renderer-owned GXState (see tools/fix_renderer_state_split.py).
// The parser owns the CP / VAT / array / default-matrix fields it writes while parsing (ParseSnapshot).
// Every field the renderer derives from BP or XF writes is applied in command order to RenderState() only.
// The BP words are the renderer's shadow (NativeRestoreBpSnapshot), exactly as before.
#include <cstdint>
#include "runtime/gx_state.h"

namespace nwii::runtime::gx {

// Parse-owned fields of one drain, captured right after FifoParser::Parse.
struct ParseSnapshot {
    uint32_t cp[256];
    VATSlot vat[8];
    uint32_t arrayBase[16];
    uint32_t arrayStride[16];
    uint8_t defPosMtxIdx;
    uint8_t defTexMtxIdx[8];
};

// Renderer-owned state. Written only by the renderer, in command order.
GXState& RenderState();
// Snapshot slot filled by ProcessGXFifo after Parse and consumed by RendererGL::Render.
ParseSnapshot& RenderBatchSnapshot();
// Copies the parse-owned view of snap into RenderState() before the batch is applied.
void RenderStateBegin(const ParseSnapshot& snap);
// Savestates still serialise one GXState: export/import keep it equal to the split state.
void RenderStateExportToParse();   // RenderState() -> g_state (parse-owned fields of g_state are kept)
void RenderStateImportFromParse(); // g_state -> RenderState()
uint64_t RenderStateEpoch();          // NATIVE_TEXTURE_CPU_GL_SPLIT_V1: changes whenever RenderState() is replaced wholesale

// Parser side (defined in fifo_parser.cpp).
void NativeTakeParseSnapshot(ParseSnapshot& out);
const uint32_t* NativeParseXfCommitted();
void ApplyBPRegisterTo(GXState& st, uint8_t reg, uint32_t val);

} // namespace nwii::runtime::gx
