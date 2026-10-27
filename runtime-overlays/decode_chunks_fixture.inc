 unsigned chunk_cases=0;
 for(unsigned trial=0;trial<180;++trial){
  GXCommand seed{};seed.type=GXCommandType::DrawPrimitive;seed.prim_type=0x90;seed.native_packed_inputs=1;seed.raw=std::make_shared<DrawRaw>();
  auto& r=*seed.raw;auto& v=r.vat;unsigned mask=1+trial%3,uv=2+(trial/3)%3;bool normal=(trial/9)&1;
  r.count=uint16_t(8192+trial*127);r.have_snap_mtx=true;v.posMask=v.texMask[0]=VtxAttrMask(mask);v.posType=VtxAttrType::F32;v.posElements=v.texElements[0]=1;v.texType[0]=VtxAttrType(uv);v.texShift[0]=trial%16;
  v.nrmMask=normal?VtxAttrMask(mask):VtxAttrMask::None;v.nrmType=VtxAttrType::S8;v.clrMask[0]=VtxAttrMask(mask);v.clrType[0]=VtxAttrType(trial%6);
  for(unsigned j=0;j<16;++j){r.arrayBase[j]=0x80010000+j*4096;r.arrayStride[j]=32;}
  auto attr=[&](unsigned bytes,unsigned index){if(mask==1){for(unsigned n=0;n<bytes;++n)r.bytes.push_back(uint8_t(rng()));}else{if(mask==3)r.bytes.push_back(index>>8);r.bytes.push_back(index);}};
  for(unsigned i=0;i<r.count;++i){unsigned index=i%256;attr(12,index);if(normal)attr(3,index);attr(ColorBytes(v.clrType[0]),index);attr(uv==4?8:4,index);}
  GXCommand reference=seed;if(!NativeTryPackedDecode(reference))return 23;
  {std::lock_guard<std::mutex> lock(s_geometry_mutex);s_geometry_cache.clear();s_geometry_bytes=0;}
  GXCommand actual=seed;auto plan=NativePrepareDecodeChunks(actual);if(!plan)return 24;
  if(NativeChunksNeedWork(plan)){std::atomic<size_t> next{0};std::vector<std::thread> threads;
   for(unsigned lane=0;lane<4;++lane)threads.emplace_back([&]{for(;;){auto begin=next.fetch_add(4096);if(begin>=r.count)break;NativeRunDecodeChunk(plan,begin,std::min(size_t(4096),size_t(r.count)-begin));}});
   for(auto& t:threads)t.join();NativeFinishDecodeChunks(plan);
  }
  const auto& a=actual;const auto& b=reference;
  if(a.native_packed.size()!=b.native_packed.size() || a.native_vertex_indices!=b.native_vertex_indices || std::memcmp(a.native_packed.data(),b.native_packed.data(),a.native_packed.size()*4) || a.native_gpu_snapshot!=seed.raw)return 25;
  ++chunk_cases;
  // Force a failed task after another task writes privately: commit must fall
  // back from the original raw bytes, never expose partial output.
  if(mask==1){GXCommand bad=seed;auto private_plan=NativePrepareDecodeChunks(bad);NativeRunDecodeChunk(private_plan,0,4096);NativeRunDecodeChunk(private_plan,r.count+1,1);NativeFinishDecodeChunks(private_plan);
   const auto& read=bad;if(read.native_packed.size()!=b.native_packed.size() || std::memcmp(read.native_packed.data(),b.native_packed.data(),b.native_packed.size()*4))return 26;++chunk_cases;}
 }
 std::cout<<"DECODE_CHUNKS "<<chunk_cases<<"/"<<chunk_cases<<'\n';
