"""Stream XF blocks into aligned ranges instead of reallocating every update."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp';s=f.read_text()
marker='NATIVE_GPU_XF_RING_V1'
if marker not in s:
    (p/'runtime-overlays/gpu-uniform-before.cpp').write_text(s)
    s=s.replace('static void NativeUploadGpuXf(){','static NativeUploadRing s_gpu_xf_ring; // NATIVE_GPU_XF_RING_V1\nstatic void NativeUploadGpuXf(){',1)
    old=''' if(!buffer){glGenBuffers(1,&buffer);glBindBufferBase(GL_UNIFORM_BUFFER,1,buffer);}
 if(!have || std::memcmp(&previous,&block,sizeof(block))){glBindBuffer(GL_UNIFORM_BUFFER,buffer);{ native_trace::GPUScope budget_operation("gpu.buffer_allocate_transfer_us",8); glBufferData(GL_UNIFORM_BUFFER,sizeof(block),&block,GL_STREAM_DRAW); }previous=block;have=true;native_trace::mark("upload.xf_bytes",sizeof(block));}'''
    new=''' static GLint alignment=0;
 if(!buffer){glGenBuffers(1,&buffer);glGetIntegerv(GL_UNIFORM_BUFFER_OFFSET_ALIGNMENT,&alignment);}
 if(!have || std::memcmp(&previous,&block,sizeof(block))){
   glBindBuffer(GL_UNIFORM_BUFFER,buffer);
   const size_t offset=NativeStreamUpload(GL_UNIFORM_BUFFER,s_gpu_xf_ring,&block,sizeof(block),size_t(std::max(1,alignment)));
   glBindBufferRange(GL_UNIFORM_BUFFER,1,buffer,GLintptr(offset),sizeof(block));
   previous=block;have=true;native_trace::mark("upload.xf_bytes",sizeof(block));
 }'''
    assert s.count(old)==1;s=s.replace(old,new,1);f.write_text(s)
print('GPU XF uniform updates use aligned append ranges; orphan before byte reuse.')
