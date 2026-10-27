"""GL 3.3 orphan-on-wrap upload rings; no overwrite of in-flight storage."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp';s=f.read_text()
baseline=p/'runtime-overlays/streaming-before-renderer.cpp'
if not baseline.exists():baseline.write_text(s)
if 'NATIVE_STREAM_UPLOAD_V1' not in s and 'struct NativeUploadRing' in s:
    # Earlier compact-upload staging removes the comment above the retained ring.
    s=s.replace('struct NativeUploadRing', '// NATIVE_STREAM_UPLOAD_V1: retained ring after compact-upload restaging.\nstruct NativeUploadRing',1)
    f.write_text(s)
if 'NATIVE_STREAM_UPLOAD_V1' not in s:
    a=s.index('// NATIVE_COMPACT_VERTEX_UPLOAD_V1:')
    helper='''// NATIVE_STREAM_UPLOAD_V1: append until full, orphan before reusing any bytes.
struct NativeUploadRing {size_t capacity=0,cursor=0;};
static NativeUploadRing s_vertex_ring,s_index_ring;
static GLint s_upload_first=0;
static size_t NativeStreamUpload(GLenum target,NativeUploadRing& ring,const void* data,size_t bytes,size_t alignment){
 static const bool disabled=std::getenv("NWII_NO_STREAM_UPLOAD")!=nullptr;
 if(disabled){glBufferData(target,GLsizeiptr(bytes),data,GL_STREAM_DRAW);return 0;}
 size_t start=(ring.cursor+alignment-1)/alignment*alignment;
 if(!ring.capacity || start+bytes>ring.capacity){
   size_t capacity=8u<<20;while(capacity<bytes)capacity*=2;
   glBufferData(target,GLsizeiptr(capacity),nullptr,GL_STREAM_DRAW);ring.capacity=capacity;ring.cursor=0;start=0;
   native_trace::mark("upload.orphan",capacity);
 }
 if(bytes)glBufferSubData(target,GLintptr(start),GLsizeiptr(bytes),data);
 ring.cursor=start+bytes;return start;
}
'''
    s=s[:a]+helper+s[a:]
    s=s.replace('glDrawArrays(GL_TRIANGLES, 0, s_batch.size());','glDrawArrays(GL_TRIANGLES, s_upload_first, s_batch.size());',1)
    old='''  glBufferData(GL_ELEMENT_ARRAY_BUFFER, s_indices.size()*sizeof(uint32_t),
               s_indices.data(), GL_STREAM_DRAW);
  glDrawElements(GL_TRIANGLES, GLsizei(s_indices.size()), GL_UNSIGNED_INT, nullptr);'''
    new='''  const size_t index_offset=NativeStreamUpload(GL_ELEMENT_ARRAY_BUFFER,s_index_ring,s_indices.data(),s_indices.size()*sizeof(uint32_t),4);
  glDrawElementsBaseVertex(GL_TRIANGLES,GLsizei(s_indices.size()),GL_UNSIGNED_INT,reinterpret_cast<void*>(index_offset),s_upload_first);'''
    if old in s:s=s.replace(old,new,1)
    else:assert 'glDrawElementsBaseVertex(' in s and 'NativeStreamUpload(GL_ELEMENT_ARRAY_BUFFER' in s
    # Program state is renderer-owned; copies/presentation do not bind another program.
    # Do not cache this until presentation's GL program ownership is qualified.
    f.write_text(s)
s=f.read_text()
s=s.replace('  glBufferData(GL_ARRAY_BUFFER,GLsizeiptr(stride*s_batch.size()),data,GL_STREAM_DRAW);','  s_upload_first=GLint(NativeStreamUpload(GL_ARRAY_BUFFER,s_vertex_ring,data,stride*s_batch.size(),stride)/stride);',1)
if f.read_text()!=s:f.write_text(s)
print('Streaming rings staged; GL 3.3 base-vertex drawing, orphan-before-wrap, legacy switch.')
