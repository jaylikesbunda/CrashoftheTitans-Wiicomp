#pragma once
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <sstream>
namespace native_gx_work {
enum Metric : unsigned { ObservedVertices, PrepackedVertices, BankBytesAvoided,
 OwnedListBytesMoved, CommandsMoved, ConstructedBp, ConstructedCp, ConstructedXf,
 ConstructedDraws, UnchangedXfBoundaries, ProjectionUploadsSkipped, MaterialBuildsSkipped,
 LightBuildsSkipped, ShaderHashRequests, ShaderHashSkipped, XfInputChecks,
 XfInputUnchanged, GenericPackedDraws, GenericPackedVerticesAttempted,
 GenericPositionSpecialized, GenericPositionFallback, FreshPackedVertices,
 FreshPackedBytes, DirectFifoDrains, DirectFifoRecords, DirectFifoInlineRecords,
 DirectFifoHeavyRecords, XfPayloadsSkipped,
 // NATIVE_FALLBACK_CENSUS_V1: measurement only. Fallback draws by position type (U8..F32, other), by posMatIdx VAT bit;
 // >2-input packed draws (scalar AoS decode); texture tracker answers and memcmp bytes.
 FallbackPosU8, FallbackPosS8, FallbackPosU16, FallbackPosS16, FallbackPosF32, FallbackPosOther,
 FallbackPosMatIdx, FallbackPosNoMatIdx, ScalarAosDecodes, ScalarAosVertices,
 TexTrackClean, TexTrackDirty, TexTrackUntracked, TexMemcmpBytes,
 // NATIVE_TEXTURE_VALIDATION_MEMO_V1: validations entering unchanged(), and skips answered by the write-epoch memo.
 TexValidations, TexMemoSkips,
 // NATIVE_FALLBACK_REASONS_V1: the gates a generic-path draw fails (FallbackGates; one bit per gate, so a draw can count under
 // several rows). Order matters: FbCountBelow32 + bit index.
 FbCountBelow32, FbPosDirect, FbPosNone, FbPosTypeNotF32, FbPos2D, FbPosMatIdx, FbTexMatIdx, FbExtraTexcoordSets,
 FbTexcoordMaskMismatch, FbTexcoordTypeU8S8, FbColourSecond, FbColourMismatch, FbNormalMismatch, FbNormalNbt, FbNormalFormat,
 FbDirectIndexMix, FbStrideNotDense, FbWideInputs, FbLoaderDisabled,
 // NATIVE_TEXTURE_ATTRIBUTION_V1: tracker pages armed by validation queries, eviction passes, entries evicted, GL names deleted.
 TexPagesArmed, TexEvictPasses, TexEvicted, TexEvictDeletes, Count };
inline const char* names[Count]={"observed_vertices_processed","vertices_prepacked",
 "stale_matrix_bank_bytes_not_copied","owned_list_bytes_not_recopied","commands_moved",
 "bp_packets_parsed","cp_packets_parsed","xf_commands_constructed",
 "draw_commands_constructed","unchanged_xf_boundaries","projection_uploads_skipped",
 "absent_material_builds_skipped","absent_light_builds_skipped",
 "shader_hash_requests","shader_hash_skipped","xf_block_input_checks",
 "xf_block_inputs_unchanged","generic_packed_draws","generic_packed_vertices_attempted",
 "generic_position_specialized_draws","generic_position_fallback_draws",
 "fresh_packed_vertices","fresh_packed_bytes","direct_fifo_drains",
 "direct_fifo_records","direct_fifo_inline_register_records","direct_fifo_heavy_records","xf_payloads_skipped",
 "generic_position_fallback_pos_u8_draws","generic_position_fallback_pos_s8_draws","generic_position_fallback_pos_u16_draws",
 "generic_position_fallback_pos_s16_draws","generic_position_fallback_pos_f32_draws","generic_position_fallback_pos_other_draws",
 "generic_position_fallback_posmatidx_draws","generic_position_fallback_no_posmatidx_draws","scalar_aos_decode_draws",
 "scalar_aos_decode_vertices","texture_track_clean","texture_track_dirty","texture_track_untracked","texture_memcmp_bytes",
 "texture_validations","texture_validation_memo_skips",
 "fallback_gate_count_below_32_draws","fallback_gate_pos_direct_draws","fallback_gate_pos_none_draws","fallback_gate_pos_type_not_f32_draws",
 "fallback_gate_pos_2d_draws","fallback_gate_pos_matidx_draws","fallback_gate_tex_matidx_draws","fallback_gate_extra_texcoord_sets_draws",
 "fallback_gate_texcoord_mask_mismatch_draws","fallback_gate_texcoord_type_u8_s8_draws","fallback_gate_colour_second_draws",
 "fallback_gate_colour_mismatch_draws","fallback_gate_normal_mismatch_draws","fallback_gate_normal_nbt_draws","fallback_gate_normal_format_draws",
 "fallback_gate_direct_index_mix_draws","fallback_gate_stride_not_dense_draws","fallback_gate_wide_inputs_draws","fallback_gate_loader_disabled_draws",
 "texture_pages_armed","texture_evict_passes","texture_evicted_entries","texture_evict_delete_names"};
inline std::atomic<bool> capture{std::getenv("NWII_PERF_CENSUS")!=nullptr};
inline std::atomic<uint64_t> totals[Count]{};
inline uint64_t baseline[Count]{},base_frames=0;
inline void Add(Metric key,uint64_t amount=1){
 if(capture.load(std::memory_order_relaxed))totals[key].fetch_add(amount,std::memory_order_relaxed);
}
struct ConstructionBatch {
 bool active=capture.load(std::memory_order_relaxed);
 uint64_t values[4]{};
 uint64_t xf_skipped=0;
 ~ConstructionBatch(){if(active){for(unsigned i=0;i<4;++i)if(values[i])Add(Metric(ConstructedBp+i),values[i]);if(xf_skipped)Add(XfPayloadsSkipped,xf_skipped);}}
};
// NATIVE_FALLBACK_CENSUS_V1 helpers (relaxed, gated by the same capture flag as Add).
inline void AddFallbackPos(unsigned type,bool matIdx){
 Add(Metric(FallbackPosU8+(type<5?type:5)));
 Add(matIdx?FallbackPosMatIdx:FallbackPosNoMatIdx);
}
// Clean: the tracker answered and no page changed after the entry was last verified (same test as CleanSince in the texture cache).
inline void AddTexTrack(uint64_t newest,uint64_t verified_gen){
 if(newest==UINT64_MAX)Add(TexTrackUntracked);
 else if(verified_gen!=0 && newest<=verified_gen)Add(TexTrackClean);
 else Add(TexTrackDirty);
}
// NATIVE_FALLBACK_REASONS_V1: gates of the packed decode that a generic-path draw fails. Mirrors NativeTryPackedDecode,
// NativeFormatDispatch (all three attributes indexed), NativeObservedFormat and NativePackedGenericPosition (F32 index8/16 with
// count >= 32). Bit i of the result is Metric FbCountBelow32 + i. Measurement only: the decision is made by the caller.
template<class Raw> inline unsigned FallbackGates(const Raw& r,unsigned inputs){
 const auto& v=r.vat;
 const unsigned pos=unsigned(v.posMask),nrm=unsigned(v.nrmMask),tex=unsigned(v.texMask[0]),clr0=unsigned(v.clrMask[0]);
 const auto indexed=[](unsigned m){return m==2u || m==3u;};
 unsigned bits=0;
 const auto gate=[&](unsigned bit,bool fails){if(fails)bits|=1u<<bit;};
 bool texmat=false,extra=false;
 for(unsigned t=0;t<8;++t){texmat=texmat||v.texMatIdx[t];if(t)extra=extra||unsigned(v.texMask[t])!=0;}
 gate(0,r.count<32);
 gate(1,pos==1u);
 gate(2,pos==0u);
 gate(3,unsigned(v.posType)!=4u);
 gate(4,!v.posElements);
 gate(5,v.posMatIdx);
 gate(6,texmat);
 gate(7,extra);
 gate(8,tex!=pos || !v.texElements[0]);
 gate(9,unsigned(v.texType[0])<2u);
 gate(10,unsigned(v.clrMask[1])!=0u);
 gate(11,clr0!=0u && clr0!=pos);
 gate(12,nrm!=0u && (nrm!=pos || v.nrmElements));
 gate(13,v.nrmIndex3);
 gate(14,nrm!=0u && unsigned(v.nrmType)!=1u && unsigned(v.nrmType)!=4u);
 gate(15,indexed(pos)!=indexed(tex) || (nrm!=0u && indexed(nrm)!=indexed(pos)));
 gate(16,unsigned(v.posType)==4u && v.posElements && r.arrayStride[0]!=12u);
 gate(17,inputs>2);
 static const bool loader_disabled=std::getenv("NWII_NO_GENERIC_POSITION_LOADER")!=nullptr;
 gate(18,loader_disabled);
 return bits;
}
inline void AddFallbackGates(unsigned bits){
 for(unsigned b=0;b<=FbLoaderDisabled-FbCountBelow32;++b)if(bits&(1u<<b))Add(Metric(FbCountBelow32+b));
}
inline int CountedMemcmp(const void* a,const void* b,size_t n){Add(TexMemcmpBytes,n);return std::memcmp(a,b,n);}
inline void SetCapture(bool visible){capture.store(visible||std::getenv("NWII_PERF_CENSUS")!=nullptr,std::memory_order_relaxed);}
inline uint64_t Delta(uint64_t value,uint64_t base){return value>=base?value-base:0;}
inline void Reset(uint64_t frames){for(unsigned i=0;i<Count;++i)baseline[i]=totals[i].load(std::memory_order_relaxed);base_frames=frames;}
inline uint64_t Value(unsigned i){return Delta(totals[i].load(std::memory_order_relaxed),baseline[i]);}
inline std::string Text(uint64_t frames){
 std::ostringstream out;const uint64_t count=Delta(frames,base_frames);
 out<<"GX optimization work: "<<count<<" completed frames since recording/reset; processed vertices can include discarded successful chunks. Unchanged XF boundaries are candidates, not proven eliminated draws.\n";
 for(unsigned i=0;i<Count;++i){out<<"  "<<names[i]<<": "<<Value(i);if(count)out<<" ("<<double(Value(i))/count<<"/XFB)";out<<'\n';}
 return out.str();
}
inline std::string Json(uint64_t frames){
 std::ostringstream out;out<<"{\"frames\":"<<Delta(frames,base_frames)<<",\"counters\":{";
 for(unsigned i=0;i<Count;++i){if(i)out<<',';out<<'"'<<names[i]<<"\":"<<Value(i);}return out.str()+"}}";
}
}
