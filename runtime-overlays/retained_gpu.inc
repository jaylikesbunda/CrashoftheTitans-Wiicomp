// Render-context-owned immutable VBOs, each with its own VAO. Keeping the source
// alive prevents pointer identity reuse. The stream VAO remains untouched.
struct NativeRetainedGpuSource {NativePackedBuffer packed;size_t count=0;};
struct NativeRetainedGpu {
 NativePackedBuffer owner;std::vector<NativeRetainedGpuSource> more;uint64_t signature=0;size_t stride=0,count=0,cost=0,owner_cost=0;GLuint vao=0,vbo=0;uint64_t stamp=0;
};
static std::vector<NativeRetainedGpu> s_geometry_gpu;
static size_t s_geometry_gpu_bytes=0;
static size_t s_geometry_gpu_owner_bytes=0;
static uint64_t s_geometry_gpu_stamp=0;
static bool NativeRetainedGpuBind(size_t stride,unsigned generators){
 static const bool enabled=!std::getenv("NWII_NO_RETAINED_GEOMETRY");
 if(!enabled || !s_gpu_vertex_mode || s_gpu_spans.empty())return false;
 size_t vertices=0,owner_cost=0;uint64_t signature=14695981039346656037ull^stride;
 for(const auto& span:s_gpu_spans){
  if(!span.packed)return false;const auto& command=*span.packed;
  // GPU admission requires demonstrated decoder reuse. Dynamic meshes retain
  // the stream path without allocating one GL object every frame.
  if(!command.native_geometry_retained || command.native_packed.empty() || span.count!=command.NativeStoredVertexCount())return false;
  vertices+=span.count;owner_cost+=command.native_packed.allocated_bytes();
  signature=(signature^reinterpret_cast<uintptr_t>(command.native_packed.identity()))*1099511628211ull;
  signature=(signature^span.count)*1099511628211ull;
 }
 const size_t cost=stride*vertices;if(!cost || cost>(32u<<20) || owner_cost>(32u<<20))return false;
 const auto& first=*s_gpu_spans.front().packed;
 auto same_sources=[&](const NativeRetainedGpu& entry){
  if(entry.more.size()+1!=s_gpu_spans.size() || entry.owner.identity()!=first.native_packed.identity())return false;
  for(size_t i=1;i<s_gpu_spans.size();++i)if(entry.more[i-1].packed.identity()!=s_gpu_spans[i].packed->native_packed.identity() || entry.more[i-1].count!=s_gpu_spans[i].count)return false;
  return true;
 };
 native_trace::Scope scope("gx.geometry_gpu");
 for(auto& entry:s_geometry_gpu)if(entry.signature==signature && entry.stride==stride && entry.count==vertices && same_sources(entry)){
  entry.stamp=++s_geometry_gpu_stamp;glBindVertexArray(entry.vao);glBindBuffer(GL_ARRAY_BUFFER,entry.vbo);s_upload_first=0;
  if(native_hud::on.load(std::memory_order_relaxed)){native_geometry::gpu_hits.fetch_add(1,std::memory_order_relaxed);native_geometry::saved_upload_bytes.fetch_add(cost,std::memory_order_relaxed);}
  return true;
 }
 if(native_hud::on.load(std::memory_order_relaxed))native_geometry::gpu_misses.fetch_add(1,std::memory_order_relaxed);
 while(!s_geometry_gpu.empty() && (s_geometry_gpu_bytes+cost>(32u<<20) || s_geometry_gpu_owner_bytes+owner_cost>(32u<<20) || s_geometry_gpu.size()>=512)){
  auto it=std::min_element(s_geometry_gpu.begin(),s_geometry_gpu.end(),[](const auto& a,const auto& b){return a.stamp<b.stamp;});
  glDeleteVertexArrays(1,&it->vao);glDeleteBuffers(1,&it->vbo);s_geometry_gpu_bytes-=it->cost;s_geometry_gpu_owner_bytes-=it->owner_cost;s_geometry_gpu.erase(it);
 }
 NativeRetainedGpu entry;entry.owner=first.native_packed;entry.signature=signature;entry.stride=stride;entry.count=vertices;entry.cost=cost;entry.owner_cost=owner_cost;entry.stamp=++s_geometry_gpu_stamp;
 for(size_t i=1;i<s_gpu_spans.size();++i)entry.more.push_back({s_gpu_spans[i].packed->native_packed,s_gpu_spans[i].count});
 glGenVertexArrays(1,&entry.vao);glBindVertexArray(entry.vao);glGenBuffers(1,&entry.vbo);glBindBuffer(GL_ARRAY_BUFFER,entry.vbo);
 const void* data=first.native_packed.data();std::vector<uint8_t> combined;
 if(s_gpu_spans.size()!=1 || size_t(first.native_packed_stride)*4!=stride){
  combined.resize(cost);uint8_t* destination=combined.data();
  for(const auto& span:s_gpu_spans){const auto& command=*span.packed;
   if(size_t(command.native_packed_stride)*4==stride){std::memcpy(destination,command.native_packed.data(),span.count*stride);destination+=span.count*stride;}
   else for(size_t i=0;i<span.count;++i,destination+=stride)std::memcpy(destination,command.native_packed.data()+i*command.native_packed_stride,stride);
  }data=combined.data();
 }
 {native_trace::Scope upload("gx.geometry_upload");glBufferData(GL_ARRAY_BUFFER,GLsizeiptr(cost),data,GL_STATIC_DRAW);}
 const unsigned sizes[5]={3,4,2,3,4};const size_t offsets[5]={0,12,28,36,48};
 for(unsigned i=0;i<5;++i){glEnableVertexAttribArray(i);glVertexAttribPointer(i,sizes[i],GL_FLOAT,GL_FALSE,GLsizei(stride),reinterpret_cast<void*>(offsets[i]));}
 for(unsigned t=0;t<8;++t){if(t<generators){glEnableVertexAttribArray(5+t);glVertexAttribPointer(5+t,3,GL_FLOAT,GL_FALSE,GLsizei(stride),reinterpret_cast<void*>(64+12*t));}
  else{glDisableVertexAttribArray(5+t);glVertexAttrib3f(5+t,0,0,0);}}
 native_hud::work_uploaded(cost);s_geometry_gpu_bytes+=cost;s_geometry_gpu_owner_bytes+=owner_cost;s_geometry_gpu.push_back(std::move(entry));s_upload_first=0;return true;
}
