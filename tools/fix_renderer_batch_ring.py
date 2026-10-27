"""Renderer-thread copy and allocation churn (capture frame-cost-20261006-180351-850400, renderer thread 36256).

1. Persistent command vector (gx.cpp ProcessGXFifo): operator new/delete under ParseStream and ~vector<GXCommand> were ~70 samples
   (2%): the per-drain std::vector<GXCommand> was created, grown by doubling and freed every drain. It now lives in a static pool that
   keeps its capacity (cleared at every exit). A re-entrant call falls back to a local vector.
2. Pack straight into the mapped ring (renderer_gl.cpp NativeUploadBatch): GPU-mode batches were copied twice per vertex
   (BatchVertex 160 B -> packed temp 76 B -> ring). With the persistent ring each vertex is packed once, directly into the mapping
   (NativeStreamReserve). The legacy packed-temp + glBufferSubData path remains the fallback (ring inactive or NWII_NO_STREAM_UPLOAD).
3. s_batch is a NativeBatchBuffer: same interface as the vector it replaces, but GPU-mode vertices (every field is written by
   NativeWriteConvertedVertex) are appended without the 160-byte zero-fill of emplace_back(). Every other path still gets a zeroed vertex.

Requires fix_persistent_upload.py first. Idempotent.
"""
from pathlib import Path
import re
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
NL=chr(10)

f=rt/'src/hle/gx.cpp';s=f.read_text()
if 'NATIVE_COMMAND_POOL_V1' not in s:
    old='    std::vector<nwii::runtime::gx::GXCommand> commands;'+NL
    assert s.count(old)==1
    new='''    // NATIVE_COMMAND_POOL_V1: keep the command vector's capacity across drains (re-entrant calls use a local vector).
    static std::vector<nwii::runtime::gx::GXCommand> s_command_pool;
    static bool s_command_pool_busy=false;
    std::vector<nwii::runtime::gx::GXCommand> local_commands;
    const bool use_command_pool=!s_command_pool_busy;
    if(use_command_pool){s_command_pool_busy=true;s_command_pool.clear();if(s_command_pool.capacity()<2048)s_command_pool.reserve(2048);}
    struct NativeCommandPoolRelease{bool active;~NativeCommandPoolRelease(){if(active){s_command_pool.clear();s_command_pool_busy=false;}}} native_command_pool_release{use_command_pool};
    std::vector<nwii::runtime::gx::GXCommand>& commands=use_command_pool?s_command_pool:local_commands;
'''
    s=s.replace(old,new)
    old='    std::vector<nwii::runtime::gx::GXCommand> frame=std::move(commands);'+NL
    assert s.count(old)==1
    s=s.replace(old,'    std::vector<nwii::runtime::gx::GXCommand>& frame=commands;'+NL)
    f.write_text(s)

f=rt/'src/hle/gx/renderer_gl.cpp';s=f.read_text()
if 'NATIVE_BATCH_RING_V1' not in s:
    # --- NativeBatchBuffer ---
    old='static std::vector<BatchVertex> s_batch;'
    assert s.count(old)==1
    new='''// NATIVE_BATCH_RING_V1: see tools/fix_renderer_batch_ring.py.
struct NativeBatchBuffer {
  BatchVertex* storage=nullptr;size_t count=0,capacity=0;
  NativeBatchBuffer()=default;NativeBatchBuffer(const NativeBatchBuffer&)=delete;NativeBatchBuffer& operator=(const NativeBatchBuffer&)=delete;
  ~NativeBatchBuffer(){::operator delete(storage);}
  size_t size() const {return count;}
  bool empty() const {return count==0;}
  void clear(){count=0;}
  BatchVertex* data(){return storage;}
  const BatchVertex* data() const {return storage;}
  BatchVertex& operator[](size_t i){return storage[i];}
  const BatchVertex& operator[](size_t i) const {return storage[i];}
  BatchVertex& front(){return storage[0];}
  const BatchVertex& front() const {return storage[0];}
  BatchVertex& back(){return storage[count-1];}
  const BatchVertex& back() const {return storage[count-1];}
  BatchVertex* begin(){return storage;}
  BatchVertex* end(){return storage+count;}
  const BatchVertex* begin() const {return storage;}
  const BatchVertex* end() const {return storage+count;}
  void reserve(size_t wanted){
    if(wanted<=capacity)return;
    size_t grown=capacity?capacity*2:4096;while(grown<wanted)grown*=2;
    BatchVertex* next=static_cast<BatchVertex*>(::operator new(grown*sizeof(BatchVertex)));
    if(count)std::memcpy(next,storage,count*sizeof(BatchVertex));
    ::operator delete(storage);storage=next;capacity=grown;
  }
  void push_back(const BatchVertex& value){const BatchVertex copy=value;reserve(count+1);storage[count++]=copy;}
  BatchVertex& emplace_back(){reserve(count+1);std::memset(&storage[count],0,sizeof(BatchVertex));return storage[count++];}
  BatchVertex& emplace_back_uninitialized(){reserve(count+1);return storage[count++];}
  void resize(size_t wanted){if(wanted>count){reserve(wanted);std::memset(&storage[count],0,(wanted-count)*sizeof(BatchVertex));}count=wanted;}
};
static NativeBatchBuffer s_batch;'''
    s=s.replace(old,new)
    old='  s_batch.emplace_back();NativeWriteConvertedVertex(vtx,s_batch.back());'
    assert s.count(old)==1
    s=s.replace(old,'  if(s_gpu_vertex_mode)NativeWriteConvertedVertex(vtx,s_batch.emplace_back_uninitialized()); // writes every field\n  else NativeWriteConvertedVertex(vtx,s_batch.emplace_back());')
    f.write_text(s)
s=f.read_text()
if 'static size_t NativeStreamReserve(' not in s:   # second half is self-healing: another stage that rewrites NativeUploadBatch or the upload region no longer strands it behind the first marker
    # --- persistent ring: placement refactor + reserve ---
    a=s.index('static size_t NativePersistentUpload(GLenum target,NativeUploadRing& ring,const void* data,size_t bytes,size_t alignment){')
    b=s.index('static size_t NativeStreamUpload(GLenum target')
    place='''// Chooses where the next `bytes` go in the mapped ring (waiting on fences as needed) and returns the offset; the caller writes there.
static size_t NativePersistentPlace(GLenum target,NativeUploadRing& ring,size_t bytes,size_t alignment){
 (void)target;
 size_t start=(ring.cursor+alignment-1)/alignment*alignment;
 if(bytes+alignment>ring.segment){
   // Larger than one segment: wait for all prior use, write from the start, and finish again before any spanned segment is reused.
   glFinish();for(unsigned i=0;i<4;++i){if(ring.fence[i]){glDeleteSync(ring.fence[i]);ring.fence[i]=nullptr;}ring.needs_finish[i]=false;}
   const unsigned last=unsigned((bytes-1)/ring.segment);
   for(unsigned i=0;i<last;++i)ring.needs_finish[i]=true;
   ring.current=last;ring.cursor=bytes;return 0;
 }
 size_t segment=start/ring.segment;
 if(segment>=4||start+bytes>(segment+1)*ring.segment){segment=segment>=3?0:segment+1;start=(segment*ring.segment+alignment-1)/alignment*alignment;}
 while(ring.current!=segment){
   if(!ring.fence[ring.current])ring.fence[ring.current]=glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE,0);
   ring.current=(ring.current+1)%4;NativePersistentWait(ring,ring.current);
 }
 ring.cursor=start+bytes;return start;
}
static size_t NativePersistentUpload(GLenum target,NativeUploadRing& ring,const void* data,size_t bytes,size_t alignment){
 if(bytes>ring.capacity){{native_trace::GPUScope budget_operation("gpu.buffer_transfer_us",8);glBufferSubData(target,0,GLsizeiptr(ring.capacity),data);}ring.cursor=0;ring.current=0;return 0;}
 const size_t start=NativePersistentPlace(target,ring,bytes,alignment);
 if(bytes)std::memcpy(ring.mapped+start,data,bytes);
 return start;
}
// NATIVE_BATCH_RING_V1: reserve space in the persistent ring and let the caller pack into it; destination stays null when the legacy path applies.
static size_t NativeStreamReserve(GLenum target,NativeUploadRing& ring,size_t bytes,size_t alignment,uint8_t** destination){
 *destination=nullptr;
 static const bool disabled=std::getenv("NWII_NO_STREAM_UPLOAD")!=nullptr;
 if(disabled)return 0;
 if(!ring.tried)NativePersistentInit(target,ring);
 if(!ring.active||bytes==0||bytes>ring.capacity)return 0;
 const size_t start=NativePersistentPlace(target,ring,bytes,alignment);
 *destination=ring.mapped+start;return start;
}
'''
    s=s[:a]+place+s[b:]
    # --- NativeUploadBatch: pack straight into the ring ---
    # NATIVE_DECODE_PACK_TUNING_V1 already replaced the repack loop with constant-size copies; keep that code, but let it target any buffer.
    start_marker='  static std::vector<uint8_t> packed;'+NL+'  const void* data=s_batch.data();'+NL+'  if(stride!=sizeof(BatchVertex)) {'+NL
    end_marker='    data=packed.data();'+NL+'  }'+NL
    i0=s.index(start_marker);i1=s.index(end_marker,i0)+len(end_marker)
    block=s[i0:i1]
    inner_start=block.index('    // NATIVE_DECODE_PACK_TUNING_V1')
    inner=block[inner_start:block.index('    data=packed.data();')]
    assert 'uint8_t* dst=packed.data();' in inner
    inner=inner.replace('uint8_t* dst=packed.data();','uint8_t* dst=target;')
    lam=('  // NATIVE_BATCH_RING_V1: one packing routine that can target the mapped ring or a temporary buffer.'+NL+
         '  auto pack_batch=[&](uint8_t* target){'+NL+
         '    if(stride==sizeof(BatchVertex)){std::memcpy(target,s_batch.data(),stride*s_batch.size());return;}'+NL+inner+'  };'+NL)
    s=s[:i0]+lam+s[i1:]
    old_tail='  s_upload_first=GLint(NativeStreamUpload(GL_ARRAY_BUFFER,s_vertex_ring,data,stride*s_batch.size(),stride)/stride);'
    assert s.count(old_tail)==1
    new_tail='''  uint8_t* destination=nullptr;
  const size_t reserved=NativeStreamReserve(GL_ARRAY_BUFFER,s_vertex_ring,stride*s_batch.size(),stride,&destination);
  if(destination) { // pack once, straight into the mapped ring
    pack_batch(destination);
    s_upload_first=GLint(reserved/stride);
  } else {
    static std::vector<uint8_t> packed;
    const void* data=s_batch.data();
    if(stride!=sizeof(BatchVertex)) {
      if(packed.size()<stride*s_batch.size())packed.resize(stride*s_batch.size());
      pack_batch(packed.data());
      data=packed.data();
    }
    s_upload_first=GLint(NativeStreamUpload(GL_ARRAY_BUFFER,s_vertex_ring,data,stride*s_batch.size(),stride)/stride);
  }'''
    s=s.replace(old_tail,new_tail)
    f.write_text(s)
print('renderer batch ring, command pool and uninitialised batch vertices installed')
