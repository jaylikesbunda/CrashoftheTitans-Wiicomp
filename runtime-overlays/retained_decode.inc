// Exact-content reuse: matrices remain in the current command's GPU snapshot.
// Only ordinary RAM and the common indexed F32 layout qualify. The complete
// structural key resolves hash collisions; exact RAM comparisons validate hits.
struct NativeGeometrySpan {const uint8_t* source=nullptr;std::vector<uint8_t> bytes;};
struct NativeRetainedDraw {
 std::vector<uint8_t> key;NativePackedBuffer packed;std::vector<uint32_t> indices;
 std::vector<NativeGeometrySpan> spans;uint64_t hash=0,stamp=0;size_t cost=0;
};
static std::mutex s_geometry_mutex;
static std::unordered_map<uint64_t,std::shared_ptr<NativeRetainedDraw>> s_geometry_cache;
static size_t s_geometry_bytes=0;
static uint64_t s_geometry_stamp=0;
static uint64_t NativeGeometryHash(const std::vector<uint8_t>& key){
 uint64_t h=14695981039346656037ull;size_t i=0;
 for(;i+8<=key.size();i+=8){uint64_t w;std::memcpy(&w,key.data()+i,8);h=(h^w)*1099511628211ull;h^=h>>29;}
 for(;i<key.size();++i)h=(h^key[i])*1099511628211ull;
 return h;
}
struct NativeGeometryAttr {
 unsigned mask=0,bytes=0,slot=0;uint32_t stride=0;const uint8_t* base=nullptr;size_t available=0;
};
struct NativeGeometryAttrs {
 std::array<NativeGeometryAttr,5> values{};size_t count=0;
 void reserve(size_t){}void push_back(const NativeGeometryAttr& a){values[count++]=a;}
 size_t size()const{return count;}const NativeGeometryAttr& operator[](size_t i)const{return values[i];}
 auto begin()const{return values.begin();}auto end()const{return values.begin()+count;}
};
template<class Attributes> static bool NativeGeometryAttributes(const DrawRaw& r,Attributes& attrs){
 if(!nwii::runtime::g_mmu)return false;const auto& v=r.vat;
 auto append=[&](VtxAttrMask mask,unsigned bytes,unsigned slot){
  if(mask==VtxAttrMask::None)return true;NativeGeometryAttr attr;attr.mask=unsigned(mask);attr.bytes=bytes;attr.slot=slot;
  if(attr.mask>1){const uint64_t physical=r.arrayBase[slot]&0x3fffffffu;auto& m=*nwii::runtime::g_mmu;attr.stride=r.arrayStride[slot];
   if(physical<m.mem1.size()){attr.base=m.mem1.data()+physical;attr.available=m.mem1.size()-physical;}
   else if(physical>=0x10000000 && physical-0x10000000<m.mem2.size()){attr.base=m.mem2.data()+physical-0x10000000;attr.available=m.mem2.size()-(physical-0x10000000);}
   else return false;
  }attrs.push_back(attr);return true;
 };
 auto width=[](VtxAttrType type){return unsigned(type)<2?1u:unsigned(type)<4?2u:4u;};
 return append(v.posMask,width(v.posType)*(v.posElements?3:2),0) && append(v.nrmMask,width(v.nrmType)*3,1) &&
  append(v.clrMask[0],ColorBytes(v.clrType[0]),2) && append(v.clrMask[1],ColorBytes(v.clrType[1]),3) && append(v.texMask[0],width(v.texType[0])*(v.texElements[0]?2:1),4);
}
static bool NativeGeometryKey(const DrawRaw& r,unsigned inputs,std::vector<uint8_t>& key){
 static const bool enabled=!std::getenv("NWII_NO_RETAINED_GEOMETRY");
 if(!enabled || g_skindbg || !nwii::runtime::g_mmu || r.count<32 || r.count>32768)return false;
 const auto& v=r.vat;
 if(v.posMask!=VtxAttrMask::Index8 && v.posMask!=VtxAttrMask::Index16)return false;
 if(v.nrmElements)return false;
 for(unsigned t=1;t<8;++t)if(v.texMask[t]!=VtxAttrMask::None || v.texMatIdx[t])return false;
 const size_t record=VertexSize(v);if(record*r.count>r.bytes.size())return false;
 NativeGeometryAttrs attrs;if(!NativeGeometryAttributes(r,attrs))return false;
 key.clear();key.reserve(192+r.bytes.size());
 auto byte=[&](unsigned n){key.push_back(uint8_t(n));};auto word=[&](uint64_t n){for(unsigned i=0;i<8;++i)byte(unsigned(n>>(8*i)));};
 byte(inputs);byte(r.count);byte(r.count>>8);byte(r.defPosMtxIdx);for(unsigned t=0;t<8;++t)byte(r.defTexMtxIdx[t]);
 byte(v.posMatIdx);byte(v.texMatIdx[0]);byte(unsigned(v.posType));byte(v.posElements);byte(v.posShift);
 byte(unsigned(v.nrmType));byte(v.nrmElements);byte(v.nrmIndex3);byte(unsigned(v.texType[0]));byte(v.texElements[0]);byte(v.texShift[0]);
 for(unsigned j=0;j<2;++j){byte(unsigned(v.clrMask[j]));byte(unsigned(v.clrType[j]));}
 for(const auto& attr:attrs){byte(attr.mask);byte(attr.bytes);byte(attr.slot);word(reinterpret_cast<uintptr_t>(attr.base));word(attr.stride);word(attr.available);}
 key.insert(key.end(),r.bytes.begin(),r.bytes.end());
 if(native_hud::on.load(std::memory_order_relaxed))native_geometry::validated_bytes.fetch_add(key.size(),std::memory_order_relaxed);
 return true;
}
static bool NativeGeometryUnchanged(const NativeRetainedDraw& entry){
 for(const auto& span:entry.spans)if(std::memcmp(span.source,span.bytes.data(),span.bytes.size()))return false;
 return true;
}
static bool NativeGeometryFind(const std::vector<uint8_t>& key,uint64_t hash,NativePackedBuffer& out,std::vector<uint32_t>& indices){
 native_trace::Scope scope("gx.geometry_lookup");
 std::shared_ptr<const NativeRetainedDraw> found_entry;
 {
  std::lock_guard<std::mutex> lock(s_geometry_mutex);
  auto found=s_geometry_cache.find(hash);
  if(found!=s_geometry_cache.end()){found->second->stamp=++s_geometry_stamp;found_entry=found->second;}
 }
 // Validate and share outside the map lock: decode lanes never serialize RAM
 // comparisons or index-vector copies. Entries outlive replacement/eviction.
 if(found_entry && found_entry->key==key && NativeGeometryUnchanged(*found_entry)){
  const auto& entry=*found_entry;out=entry.packed;indices=entry.indices;
  if(native_hud::on.load(std::memory_order_relaxed)){native_geometry::decoded_hits.fetch_add(1,std::memory_order_relaxed);for(const auto& span:entry.spans)native_geometry::validated_bytes.fetch_add(span.bytes.size(),std::memory_order_relaxed);}
  return true;
 }
 if(native_hud::on.load(std::memory_order_relaxed))native_geometry::decoded_misses.fetch_add(1,std::memory_order_relaxed);
 return false;
}
// Index domains are at most 16 bits. Stamp-cleared bitsets deduplicate in
// bounded space and enumerate in index/address order without O(n log n) sort.
// Each decode lane owns its scratch; nothing escapes into cached dependencies.
struct NativeGeometryScratch {
 uint32_t epoch=0,tags[5][1024]{};uint64_t bits[5][1024]{},active[5][16]{};
 void reset(){if(++epoch==0){std::memset(tags,0,sizeof(tags));epoch=1;}std::memset(active,0,sizeof(active));}
 void insert(unsigned attr,unsigned index){const unsigned word=index>>6;
  if(tags[attr][word]!=epoch){tags[attr][word]=epoch;bits[attr][word]=0;}
  bits[attr][word]|=uint64_t(1)<<(index&63);active[attr][word>>6]|=uint64_t(1)<<(word&63);
 }
};
static bool NativeGeometryCapture(const DrawRaw& r,std::vector<NativeGeometrySpan>& spans){
 NativeGeometryAttrs attrs;if(!NativeGeometryAttributes(r,attrs))return false;
 static thread_local NativeGeometryScratch scratch;scratch.reset();
 const size_t record=VertexSize(r.vat),matrix=size_t(r.vat.posMatIdx)+size_t(r.vat.texMatIdx[0]);
 if(uint64_t(record)*r.count>r.bytes.size())return false;
 for(unsigned i=0;i<r.count;++i){size_t off=size_t(i)*record+matrix;
  for(unsigned a=0;a<attrs.size();++a){const auto& attr=attrs[a];
   if(attr.mask==1){off+=attr.bytes;continue;}
   const unsigned width=attr.mask==2?1:2;if(off+width>size_t(i+1)*record)return false;
   const unsigned index=width==1?r.bytes[off]:((unsigned(r.bytes[off])<<8)|r.bytes[off+1]);off+=width;
   const size_t address=size_t(index)*attr.stride;if(address+attr.bytes>attr.available)return false;
   scratch.insert(a,index);
  }
 }
 spans.clear();
 for(unsigned a=0;a<attrs.size();++a){const auto& attr=attrs[a];bool have=false;size_t begin=0,end=0;
  auto save=[&](){NativeGeometrySpan span;span.source=attr.base+begin;span.bytes.assign(span.source,span.source+end-begin);spans.push_back(std::move(span));};
  for(unsigned group=0;group<16;++group){uint64_t words=scratch.active[a][group];
   while(words){const unsigned word=group*64+unsigned(__builtin_ctzll(words));words&=words-1;
    uint64_t indices=scratch.bits[a][word];while(indices){const unsigned index=word*64+unsigned(__builtin_ctzll(indices));indices&=indices-1;
     const size_t next=size_t(index)*attr.stride;
     if(!have){begin=next;end=next+attr.bytes;have=true;}
     else if(next<=end+8)end=std::max(end,next+attr.bytes);
     else{save();begin=next;end=next+attr.bytes;}
    }
   }
  }if(have)save();
 }
 return !spans.empty();
}
static void NativeGeometryStore(std::vector<uint8_t>&& key,uint64_t hash,const NativePackedBuffer& packed,const std::vector<uint32_t>& indices,std::vector<NativeGeometrySpan>&& spans){
 NativeRetainedDraw entry;entry.key=std::move(key);entry.packed=packed;entry.indices=indices;entry.hash=hash;
 entry.spans=std::move(spans);
 // A miss snapshots dependencies before decoding. Concurrent source changes
// prevent admission; no post-decode RAM bytes can validate an older payload.
 if(!NativeGeometryUnchanged(entry))return;
 size_t cost=sizeof(entry)+entry.key.capacity()+packed.allocated_bytes()+indices.capacity()*4+entry.spans.capacity()*sizeof(NativeGeometrySpan);for(const auto& span:entry.spans)cost+=span.bytes.capacity();
 constexpr size_t limit=32u<<20;if(cost>limit)return;entry.cost=cost;
 std::lock_guard<std::mutex> lock(s_geometry_mutex);
 auto found=s_geometry_cache.find(hash);
 if(found!=s_geometry_cache.end()){
  if(found->second->key==entry.key && NativeGeometryUnchanged(*found->second))return;
  s_geometry_bytes-=found->second->cost;s_geometry_cache.erase(found);
 }
 while(!s_geometry_cache.empty() && (s_geometry_bytes+cost>limit || s_geometry_cache.size()>=512)){
  auto it=std::min_element(s_geometry_cache.begin(),s_geometry_cache.end(),[](const auto& a,const auto& b){return a.second->stamp<b.second->stamp;});
  s_geometry_bytes-=it->second->cost;s_geometry_cache.erase(it);
 }
 entry.stamp=++s_geometry_stamp;s_geometry_cache.emplace(hash,std::make_shared<NativeRetainedDraw>(std::move(entry)));s_geometry_bytes+=cost;
}
