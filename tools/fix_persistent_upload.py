"""Persistent-mapped streaming uploads (GL_ARB_buffer_storage) for the vertex, index and transform-uniform rings.

Capture frame-cost-20261006-180351-850400, renderer thread (26.2 ms CPU/frame): NativeStreamUpload inclusive 374 of 3,371 samples
(11.1%, about 2.9 ms/frame), almost all inside the driver's glBufferSubData; budget.md also lists gpu.buffer_transfer at 12.2 ms/frame
(sampled 1/8, variability 6.3).

Each ring's buffer gets immutable persistent storage (vertex 32 MiB, index 16 MiB, uniform 4 MiB) mapped once for writing. Uploads are
a memcpy into the mapping. The ring is split into four segments; when the write position leaves a segment a GPU fence is inserted
(every draw that reads it is already submitted, because upload precedes its draw), and before entering a segment its previous fence
is waited on, so the CPU can never overwrite data the GPU has not consumed. No upload spans two segments (it moves to the next
segment start). An upload larger than a segment does glFinish() first and is flagged so the segments it spans are finished again
before reuse. Offsets handed to the draw calls, alignment and the existing bind points are unchanged.

Falls back to the previous orphan + glBufferSubData path when GL_ARB_buffer_storage or glBufferStorage is missing, when storage
creation or mapping reports an error, or when NWII_NO_PERSISTENT_UPLOAD is set. Idempotent.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
f=rt/'src/hle/gx/renderer_gl.cpp';s=f.read_text()
marker='NATIVE_PERSISTENT_UPLOAD_V1'
if marker not in s:
    old_struct='struct NativeUploadRing {size_t capacity=0,cursor=0;};'
    assert s.count(old_struct)==1
    new_struct='''// NATIVE_PERSISTENT_UPLOAD_V1: see tools/fix_persistent_upload.py.
#ifndef GL_MAP_PERSISTENT_BIT
#define GL_MAP_PERSISTENT_BIT 0x0040
#endif
#ifndef GL_MAP_COHERENT_BIT
#define GL_MAP_COHERENT_BIT 0x0080
#endif
#ifndef GL_DYNAMIC_STORAGE_BIT
#define GL_DYNAMIC_STORAGE_BIT 0x0100
#endif
struct NativeUploadRing {size_t capacity=0,cursor=0;
  bool tried=false,active=false;uint8_t* mapped=nullptr;size_t segment=0;unsigned current=0;GLsync fence[4]={};bool needs_finish[4]={};};'''
    s=s.replace(old_struct,new_struct)
    old_head='''static size_t NativeStreamUpload(GLenum target,NativeUploadRing& ring,const void* data,size_t bytes,size_t alignment){
 static const bool disabled=std::getenv("NWII_NO_STREAM_UPLOAD")!=nullptr;
 if(disabled){{ native_trace::GPUScope budget_operation("gpu.buffer_allocate_transfer_us",8); glBufferData(target,GLsizeiptr(bytes),data,GL_STREAM_DRAW); }return 0;}
'''
    assert s.count(old_head)==1
    new_head='''using NativeBufferStorageFn=void(APIENTRY*)(GLenum,GLsizeiptr,const void*,GLbitfield);
static NativeBufferStorageFn NativeBufferStorage(){
 static NativeBufferStorageFn function=[]()->NativeBufferStorageFn{
   if(std::getenv("NWII_NO_PERSISTENT_UPLOAD"))return nullptr;
   bool extension=false;GLint count=0;glGetIntegerv(GL_NUM_EXTENSIONS,&count);
   for(GLint i=0;i<count && !extension;++i){const char* name=reinterpret_cast<const char*>(glGetStringi(GL_EXTENSIONS,GLuint(i)));
     extension=name && !std::strcmp(name,"GL_ARB_buffer_storage");}
   if(!extension)return nullptr;
   return reinterpret_cast<NativeBufferStorageFn>(SDL_GL_GetProcAddress("glBufferStorage"));}();
 return function;
}
// Waits for the fence that guarded a segment (or finishes the GPU when an oversized upload spanned it).
static void NativePersistentWait(NativeUploadRing& ring,unsigned segment){
 if(ring.needs_finish[segment]){glFinish();ring.needs_finish[segment]=false;}
 if(GLsync fence=ring.fence[segment]){
   for(;;){const GLenum state=glClientWaitSync(fence,GL_SYNC_FLUSH_COMMANDS_BIT,1000000000ull);
     if(state==GL_ALREADY_SIGNALED||state==GL_CONDITION_SATISFIED)break;
     if(state==GL_WAIT_FAILED){glFinish();break;}}
   glDeleteSync(fence);ring.fence[segment]=nullptr;
 }
}
static bool NativePersistentInit(GLenum target,NativeUploadRing& ring){
 ring.tried=true;
 const NativeBufferStorageFn storage=NativeBufferStorage();if(!storage)return false;
 const size_t size=target==GL_ARRAY_BUFFER?(32u<<20):target==GL_ELEMENT_ARRAY_BUFFER?(16u<<20):(4u<<20);
 while(glGetError()!=GL_NO_ERROR){}
 const GLbitfield flags=GL_MAP_WRITE_BIT|GL_MAP_PERSISTENT_BIT|GL_MAP_COHERENT_BIT|GL_DYNAMIC_STORAGE_BIT;
 storage(target,GLsizeiptr(size),nullptr,flags);
 if(glGetError()!=GL_NO_ERROR)return false;
 void* mapped=glMapBufferRange(target,0,GLsizeiptr(size),GL_MAP_WRITE_BIT|GL_MAP_PERSISTENT_BIT|GL_MAP_COHERENT_BIT);
 if(!mapped||glGetError()!=GL_NO_ERROR)return false;
 ring.mapped=static_cast<uint8_t*>(mapped);ring.capacity=size;ring.segment=size/4;ring.cursor=0;ring.current=0;ring.active=true;
 native_trace::mark("upload.persistent_ring",size);
 return true;
}
static size_t NativePersistentUpload(GLenum target,NativeUploadRing& ring,const void* data,size_t bytes,size_t alignment){
 size_t start=(ring.cursor+alignment-1)/alignment*alignment;
 if(bytes+alignment>ring.segment){
   // Larger than one segment: wait for all prior use, write from the start, and finish again before any spanned segment is reused.
   glFinish();for(unsigned i=0;i<4;++i){if(ring.fence[i]){glDeleteSync(ring.fence[i]);ring.fence[i]=nullptr;}ring.needs_finish[i]=false;}
   start=0;if(bytes>ring.capacity){{native_trace::GPUScope budget_operation("gpu.buffer_transfer_us",8);glBufferSubData(target,0,GLsizeiptr(std::min(bytes,ring.capacity)),data);}ring.cursor=0;ring.current=0;return 0;}
   std::memcpy(ring.mapped,data,bytes);
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
 if(bytes)std::memcpy(ring.mapped+start,data,bytes);
 ring.cursor=start+bytes;return start;
}
static size_t NativeStreamUpload(GLenum target,NativeUploadRing& ring,const void* data,size_t bytes,size_t alignment){
 static const bool disabled=std::getenv("NWII_NO_STREAM_UPLOAD")!=nullptr;
 if(disabled){{ native_trace::GPUScope budget_operation("gpu.buffer_allocate_transfer_us",8); glBufferData(target,GLsizeiptr(bytes),data,GL_STREAM_DRAW); }return 0;}
 if(!ring.tried)NativePersistentInit(target,ring);
 if(ring.active)return NativePersistentUpload(target,ring,data,bytes,alignment);
'''
    s=s.replace(old_head,new_head)
    f.write_text(s)
print('persistent upload installed')
