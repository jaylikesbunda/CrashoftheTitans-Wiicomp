// NATIVE_COMMAND_REUSE_V1: bounded storage recycling; shared snapshots are never
// modified or recycled. Release happens only after the existing decode join.
// NATIVE_PAYLOAD_BUCKETS_V1: payload reuse is an O(1) free list per capacity
// class (8 floats) over a fixed slot table. The old linear best-fit walked up to
// 256 vectors on every XF acquire, which dominated parse on changing streams.
// A slot is either free (payload_free) or linked in exactly one bucket, so two
// live commands can never alias the same backing vector.
struct NativeCommandReusePool {
 std::mutex mutex;std::array<std::shared_ptr<DrawRaw>,128> draws{};
 std::array<std::vector<float>,256> payloads{};
 std::array<uint16_t,129> payload_head{};   // bucket -> slot+1, 0 = empty
 std::array<uint16_t,256> payload_link{};   // slot  -> next slot+1
 std::array<uint16_t,256> payload_free{};   // unassigned slots
 size_t payload_free_n=0;
 size_t draw_count=0,payload_count=0,draw_bytes=0;
 uint64_t draw_hits=0,payload_hits=0;
};
static void NativeInitPool(NativeCommandReusePool& pool){
 for(size_t i=0;i<pool.payload_free.size();++i)pool.payload_free[i]=uint16_t(i);
 pool.payload_free_n=pool.payload_free.size();
}
static NativeCommandReusePool& NativeCommandPool(){static NativeCommandReusePool pool;static const bool init=(NativeInitPool(pool),true);(void)init;return pool;}
static bool NativeCommandReuseEnabled(){static const bool on=!std::getenv("NWII_NO_COMMAND_REUSE");return on;}
static size_t NativeDrawCapacity(const DrawRaw& r){return sizeof(r)+r.bytes.allocated_bytes()+4*(r.snap_pos_bank.capacity()+r.snap_nrm_bank.capacity());}
static std::shared_ptr<DrawRaw> NativeAcquireDrawRaw(){
 if(!NativeCommandReuseEnabled())return std::make_shared<DrawRaw>();
 std::shared_ptr<DrawRaw> out;
 {auto& pool=NativeCommandPool();std::lock_guard<std::mutex> lock(pool.mutex);
  if(pool.draw_count){out=std::move(pool.draws[--pool.draw_count]);pool.draw_bytes-=NativeDrawCapacity(*out);++pool.draw_hits;}
 }
 if(!out)return std::make_shared<DrawRaw>();
 // Reset every scalar and snapshot flag. Keep only exclusively owned backing
 // storage; bytes.clear() detaches from any independently shared byte stream.
 DrawRaw clean{};out->bytes.swap(clean.bytes);
 out->snap_pos_bank.swap(clean.snap_pos_bank);out->snap_nrm_bank.swap(clean.snap_nrm_bank);
 *out=std::move(clean);out->bytes.clear();out->snap_pos_bank.clear();out->snap_nrm_bank.clear();return out;
}
static size_t NativePayloadBucket(size_t capacity){
 size_t bucket=(capacity+7)>>3;return bucket<1?1:(bucket>128?128:bucket);
}
static void NativeReleasePayload(NativeCommandReusePool& pool,std::vector<float>& payload){
 if(!payload.capacity()||payload.capacity()>1024||pool.payload_free_n==0)return;
 const size_t slot=pool.payload_free[--pool.payload_free_n];
 pool.payloads[slot].swap(payload);
 const size_t bucket=NativePayloadBucket(pool.payloads[slot].capacity());
 pool.payload_link[slot]=pool.payload_head[bucket];
 pool.payload_head[bucket]=uint16_t(slot+1);
 ++pool.payload_count;
}
static void NativeAcquirePayload(std::vector<float>& out,size_t count){
 if(NativeCommandReuseEnabled() && out.capacity()<count){
  auto& pool=NativeCommandPool();std::lock_guard<std::mutex> lock(pool.mutex);
  for(size_t bucket=NativePayloadBucket(count);bucket<pool.payload_head.size();++bucket){
   const uint16_t head=pool.payload_head[bucket];
   if(head){const uint16_t slot=uint16_t(head-1);pool.payload_head[bucket]=pool.payload_link[slot];
    out.swap(pool.payloads[slot]);pool.payloads[slot]={};
    pool.payload_free[pool.payload_free_n++]=slot;--pool.payload_count;++pool.payload_hits;break;}
  }
 }
 out.clear();out.resize(count);
}
void NativeRecycleCommands(std::vector<GXCommand>& commands){
 if(!NativeCommandReuseEnabled())return;auto& pool=NativeCommandPool();std::lock_guard<std::mutex> lock(pool.mutex);
 for(auto& c:commands){
  // A command's two aliases need not prevent recycling once both are retired.
  c.native_gpu_snapshot.reset();
  if(c.raw && c.raw.use_count()==1 && pool.draw_count<pool.draws.size()){
   const size_t bytes=NativeDrawCapacity(*c.raw);
   if(bytes<=(32u<<20)-pool.draw_bytes){pool.draw_bytes+=bytes;pool.draws[pool.draw_count++]=std::move(c.raw);}
  }
  if(c.payload.capacity() && c.payload.capacity()<=1024 && pool.payload_count<pool.payloads.size()){
   NativeReleasePayload(pool,c.payload);
  }
 }
}
