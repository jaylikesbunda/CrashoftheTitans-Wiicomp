// Preparation/commit run on the owner. Workers write disjoint float ranges,
// with no vector resize, command mutation, cache publication or GPU API calls.
struct NativeChunkedDraw {
 GXCommand* command=nullptr;std::shared_ptr<DrawRaw> raw;unsigned observed=0,inputs=0,words=0;
 float defaults[40]{},*storage=nullptr;NativePackedBuffer output;
 std::atomic<bool> failed{false};bool finished=false,retain=false;
 std::vector<uint8_t> key;uint64_t hash=0;std::vector<NativeGeometrySpan> dependencies;
};
std::shared_ptr<NativeChunkedDraw> NativePrepareDecodeChunks(GXCommand& command){
 static const bool enabled=!std::getenv("NWII_NO_DECODE_CHUNKS") && !std::getenv("NWII_NO_PACKED_DECODE") && !std::getenv("NWII_NO_FUSED_BATCH") && !std::getenv("NWII_DRAW_ARCHIVE") && !std::getenv("NWII_EFB_PROBE") && !std::getenv("NWII_STATE_CENSUS");
 if(!enabled || !command.raw || command.raw->count<8192 || !command.vertices.empty() || !command.native_packed.empty() || !NativeGpuVertexEnabled())return {};
 const auto& r=*command.raw;const unsigned observed=NativeObservedFormat(r);if(!observed || g_skindbg)return {};
 // F32 PNT uses tuple dedup; never replace its logical/storage representation.
 if(r.vat.nrmMask!=VtxAttrMask::None && r.vat.nrmType==VtxAttrType::F32 && r.vat.texType[0]==VtxAttrType::F32)return {};
 if((!r.have_snap_mtx && r.snap_pos_bank.size()!=256) || (r.snap_pos_bank.size()==256 && r.snap_nrm_bank.size()!=96))return {};
 if(command.prim_type!=0x80 && command.prim_type!=0x90 && command.prim_type!=0x98 && command.prim_type!=0xa0)return {};
 const unsigned inputs=NativePackedStorageInputs(r,command.native_packed_inputs);if(inputs>2 || size_t(VertexSize(r.vat))*r.count>r.bytes.size())return {};
 if(unsigned(r.defPosMtxIdx)*4+12>256 || 3u*(r.defPosMtxIdx&31)+9>96)return {};
 auto plan=std::make_shared<NativeChunkedDraw>();plan->command=&command;plan->raw=command.raw;plan->observed=observed;plan->inputs=inputs;plan->words=16+3*inputs;
 NativePackedDefaults(r,plan->defaults,inputs);
 {native_trace::Scope validate("gx.geometry_validate");plan->retain=NativeGeometryKey(r,inputs,plan->key);}
 if(plan->retain){plan->hash=NativeGeometryHash(plan->key);std::vector<uint32_t> indices;
  if(NativeGeometryFind(plan->key,plan->hash,plan->output,indices)){
   command.native_packed=std::move(plan->output);command.native_vertex_indices=std::move(indices);command.native_packed_stride=uint8_t(plan->words);
   command.native_gpu_snapshot=command.raw;command.raw.reset();command.native_geometry_retained=true;plan->finished=true;
   native_hud::work_decoded(command.NativeStoredVertexCount(),command.NativeStoredVertexCount());return plan;
  }
  plan->retain=NativeGeometryCapture(r,plan->dependencies);
 }
 if(native_hud::on.load(std::memory_order_relaxed))native_geometry::format_draws.fetch_add(1,std::memory_order_relaxed);
 plan->output.resize(size_t(r.count)*plan->words);plan->storage=plan->output.data();return plan;
}
bool NativeChunksNeedWork(const std::shared_ptr<NativeChunkedDraw>& plan){return !plan->finished;}
void NativeRunDecodeChunk(const std::shared_ptr<NativeChunkedDraw>& plan,size_t first,size_t count){
 native_trace::Scope decode("gx.decode");
 if(first>plan->raw->count || count>plan->raw->count-first || !NativeObservedRangeDispatch(plan->observed,*plan->raw,plan->storage,first,count,plan->inputs,plan->words,plan->defaults))plan->failed.store(true,std::memory_order_relaxed);
}
void NativeFinishDecodeChunks(const std::shared_ptr<NativeChunkedDraw>& plan){
 if(plan->finished)return;auto& command=*plan->command;
 if(plan->failed.load(std::memory_order_relaxed)){FifoParser::DecodeDraw(command);return;}
 if(plan->retain)NativeGeometryStore(std::move(plan->key),plan->hash,plan->output,{},std::move(plan->dependencies));
 command.native_packed=std::move(plan->output);command.native_vertex_indices.clear();command.native_packed_stride=uint8_t(plan->words);
 command.native_gpu_snapshot=command.raw;command.raw.reset();command.native_geometry_retained=false;
 native_hud::work_decoded(command.NativeStoredVertexCount(),command.NativeStoredVertexCount());plan->finished=true;
}
