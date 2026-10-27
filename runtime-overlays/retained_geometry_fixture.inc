 // Exercise production retention against a freshly decoded oracle. Matrices
 // change independently, while array bytes and FIFO records invalidate reuse.
 native_hud::on=true;
 GXCommand concurrent_seed{};
 unsigned retained_checks=0;uint64_t stable_hits=0;
 for(unsigned trial=0;trial<1200;++trial){
  GXCommand seed{};seed.type=GXCommandType::DrawPrimitive;seed.prim_type=0x90;seed.native_packed_inputs=1;
  seed.raw=std::make_shared<DrawRaw>();auto& r=*seed.raw;auto& v=r.vat;r.count=48;r.have_snap_mtx=true;
  v.posMask=VtxAttrMask(2+(trial&1));v.nrmMask=VtxAttrMask(2+((trial>>1)&1));v.texMask[0]=VtxAttrMask(2+((trial>>2)&1));
  v.posType=v.nrmType=v.texType[0]=VtxAttrType::F32;v.posElements=v.texElements[0]=1;
  v.posMatIdx=trial&8;v.texMatIdx[0]=trial&16;
  for(unsigned j=0;j<2;++j){v.clrMask[j]=((trial>>(5+j))&1)?VtxAttrMask::Direct:VtxAttrMask::None;v.clrType[j]=VtxAttrType((trial+j)%6);}
  for(unsigned j=0;j<16;++j){r.arrayBase[j]=0x80010000+j*4096;r.arrayStride[j]=32;}
  for(unsigned i=0;i<r.count;++i){
   auto index=[&](unsigned m){if(m==3)r.bytes.push_back(0);r.bytes.push_back(uint8_t(i/2));};
   if(v.posMatIdx)r.bytes.push_back(0);if(v.texMatIdx[0])r.bytes.push_back(60);
   index(unsigned(v.posMask));index(unsigned(v.nrmMask));
   for(unsigned j=0;j<2;++j)if(v.clrMask[j]!=VtxAttrMask::None)for(int n=0;n<ColorBytes(v.clrType[j]);++n)r.bytes.push_back(uint8_t(rng()));
   index(unsigned(v.texMask[0]));
  }
  if(trial==0)concurrent_seed=seed;
  const void* previous=nullptr;
  for(unsigned round=0;round<5;++round){
   if(round==2){ctx->mmu.write32(r.arrayBase[0],rng());ctx->mmu.write32(r.arrayBase[1]+32,rng());ctx->mmu.write32(r.arrayBase[4]+64,rng());}
   if(round==3)r.defTexMtxIdx[0]=uint8_t(trial);
   r.snap_pos_mtx[3]=float(round); // GPU snapshot must remain live on cache hits.
   GXCommand actual=seed;NativePackedBuffer expected;std::vector<uint32_t> expected_indices;
   float defaults[40];NativePackedDefaults(r,defaults,1);
   unsigned mask=(v.posMask==VtxAttrMask::Index16?4:0)|(v.nrmMask==VtxAttrMask::Index16?2:0)|(v.texMask[0]==VtxAttrMask::Index16?1:0);
   bool oracle=false;
   switch(mask){
    case 0:oracle=NativePackedIndexed<2,2,2>(seed,expected,expected_indices,1,19,defaults);break;
    case 1:oracle=NativePackedIndexed<2,2,3>(seed,expected,expected_indices,1,19,defaults);break;
    case 2:oracle=NativePackedIndexed<2,3,2>(seed,expected,expected_indices,1,19,defaults);break;
    case 3:oracle=NativePackedIndexed<2,3,3>(seed,expected,expected_indices,1,19,defaults);break;
    case 4:oracle=NativePackedIndexed<3,2,2>(seed,expected,expected_indices,1,19,defaults);break;
    case 5:oracle=NativePackedIndexed<3,2,3>(seed,expected,expected_indices,1,19,defaults);break;
    case 6:oracle=NativePackedIndexed<3,3,2>(seed,expected,expected_indices,1,19,defaults);break;
    case 7:oracle=NativePackedIndexed<3,3,3>(seed,expected,expected_indices,1,19,defaults);break;
   }
   if(!oracle || !NativeTryPackedDecode(actual))return 8;
   const auto& read=actual;
   if(read.native_vertex_indices!=expected_indices || read.native_packed.size()!=expected.size() || std::memcmp(read.native_packed.data(),expected.data(),expected.size()*4) || actual.native_gpu_snapshot!=seed.raw)return 9;
   if(round==1 && previous==actual.native_packed.identity())++stable_hits;
   previous=actual.native_packed.identity();++retained_checks;
  }
 }
 if(stable_hits<1000 || native_geometry::decoded_hits.load()<1000 || s_geometry_bytes>(32u<<20) || s_geometry_cache.size()>512)return 10;
 GXCommand oracle_command=concurrent_seed;if(!NativeTryPackedDecode(oracle_command))return 13;
 const GXCommand& oracle_read=oracle_command;std::atomic<unsigned> parallel_failed{0};std::vector<std::thread> workers;
 for(unsigned lane=0;lane<4;++lane)workers.emplace_back([&](){for(unsigned i=0;i<200;++i){GXCommand actual=concurrent_seed;
  if(!NativeTryPackedDecode(actual)){++parallel_failed;continue;}const GXCommand& read=actual;
  if(read.native_packed.size()!=oracle_read.native_packed.size() || read.native_vertex_indices!=oracle_read.native_vertex_indices || std::memcmp(read.native_packed.data(),oracle_read.native_packed.data(),read.native_packed.size()*4))++parallel_failed;
 }});
 for(auto& worker:workers)worker.join();if(parallel_failed)return 14;
 NativePackedBuffer collision;collision.resize(4);std::fill(collision.data(),collision.data()+4,1.f);
 std::vector<NativeGeometrySpan> collision_spans;if(!NativeGeometryCapture(*concurrent_seed.raw,collision_spans))return 16;NativeGeometryStore(std::vector<uint8_t>{1},7,collision,{},std::move(collision_spans));
 if(!NativeGeometryCapture(*concurrent_seed.raw,collision_spans))return 17;NativeGeometryStore(std::vector<uint8_t>{2},7,collision,{},std::move(collision_spans));
 NativePackedBuffer found;std::vector<uint32_t> index;if(NativeGeometryFind({1},7,found,index) || !NativeGeometryFind({2},7,found,index))return 15;
 std::cout<<"RETAINED_PARALLEL checks=800 collisions=2 failed="<<parallel_failed.load()<<'\n';
 // Copy-on-write storage must survive cache eviction and command mutation.
 NativePackedBuffer one;one.resize(4);one.data()[0]=12.f;NativePackedBuffer two=one;
 if(one.identity()!=two.identity())return 11;two.data()[0]=7.f;if(one.data()[0]!=12.f)return 12;
 std::cout<<"RETAINED_BYTES checks="<<retained_checks<<" stable_hits="<<stable_hits<<" entries="<<s_geometry_cache.size()<<" bytes="<<s_geometry_bytes<<'\n';
 native_hud::on=false;
