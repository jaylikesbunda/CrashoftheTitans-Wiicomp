"""Fused GPU-mode vertex batches: pack decoded vertices straight into the upload ring.

Capture frame-cost-20261006-180351-850400: 99.99% of vertices (22,049,817 of 22,052,697) already use the GPU vertex pipeline, where
NativeWriteConvertedVertex is a pure field copy of VertexData into a 160-byte BatchVertex, which FlushBatch then repacks to 64+12*N bytes
and copies into the ring. EmitVertex is 578 of 3,371 renderer samples (4.5 ms/frame) and NativeUploadBatch's repack is most of the
ucrtbase time under FlushBatch.

In GPU vertex mode EmitPrimitive now records (pointer, count) spans over the draw's decoded vertices instead of converting them. At
flush, NativeUploadBatch packs each vertex straight from VertexData into the ring (NativePackGpuVertex writes exactly the first
64+12*generators bytes that the old BatchVertex repack produced; tools/check_fused_batch.py proves the bytes are identical). The spans
point into the GXCommand objects, which outlive the batch (Render always flushes the open batch before it returns). Index generation,
batch boundaries, draw counts and uploaded offsets are unchanged.

Not fused (the previous code runs): CPU-mode draws, lines/points, NWII_NO_FUSED_BATCH=1, NWII_DRAW_ARCHIVE, NWII_EFB_PROBE (these read
s_batch). A batch that already holds converted vertices keeps converting; a conversion request while spans exist materialises them first.

Requires fix_renderer_batch_ring.py first (NativeBatchBuffer / NativeStreamReserve). Idempotent.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
f=rt/'src/hle/gx/renderer_gl.cpp';s=f.read_text()
NL=chr(10)

def part(old, new, label, whole=None):
    """Apply one part of the patch unless its result is already there (each part restores itself if another stage reverted it)."""
    global s
    probe = whole if whole is not None else new
    if probe in s:
        return
    assert s.count(old) == 1, (label, s.count(old))
    s = s.replace(old, new, 1)


# 1. declarations next to the batch buffer
old = 'static NativeBatchBuffer s_batch;'
decl = old + """
// NATIVE_FUSED_BATCH_V1: see tools/fix_fused_gpu_batch.py.
struct NativeGpuSpan {const VertexData* vertices; size_t count;};
static std::vector<NativeGpuSpan> s_gpu_spans;
static size_t s_gpu_span_vertices=0;
static inline size_t NativeBatchSize(){return s_gpu_span_vertices?s_gpu_span_vertices:s_batch.size();}
static inline bool NativeFusedBatchEnabled(){
  static const bool enabled=!std::getenv("NWII_NO_FUSED_BATCH") && !std::getenv("NWII_DRAW_ARCHIVE") && !std::getenv("NWII_EFB_PROBE");
  return enabled;
}
// Writes the first 16+3*generators floats of the old BatchVertex (GPU-vertex-mode field copy) straight from a decoded vertex.
static inline void NativePackGpuVertex(const VertexData& vtx,float* out,unsigned generators){
  out[0]=vtx.pos[0];out[1]=vtx.pos[1];out[2]=vtx.pos[2];
  if(vtx.has_color){out[3]=vtx.color[0];out[4]=vtx.color[1];out[5]=vtx.color[2];out[6]=vtx.color[3];}
  else{out[3]=out[4]=out[5]=out[6]=1.f;}
  out[7]=float(vtx.posMtxIdx);out[8]=vtx.has_norm?1.f:0.f;
  if(vtx.has_norm){out[9]=vtx.norm[0];out[10]=vtx.norm[1];out[11]=vtx.norm[2];}
  else{out[9]=out[10]=out[11]=0;}
  out[12]=vtx.color1[0];out[13]=vtx.color1[1];out[14]=vtx.color1[2];out[15]=vtx.color1[3];
  for(unsigned t=0;t<generators;++t){out[16+t*3]=vtx.tex[t][0];out[17+t*3]=vtx.tex[t][1];out[18+t*3]=float(vtx.texMtxIdx[t]);}
}"""
part(old, decl, 'declarations',whole='static inline void NativePackGpuVertex(')

# 2. materialise spans when a conversion arrives
old = '  if(s_gpu_vertex_mode)NativeWriteConvertedVertex(vtx,s_batch.emplace_back_uninitialized()); // writes every field'
new = """  if(!s_gpu_spans.empty()){ // NATIVE_FUSED_BATCH_V1: a conversion after fused spans keeps their order
    for(const auto& span:s_gpu_spans)for(size_t i=0;i<span.count;++i)NativeWriteConvertedVertex(span.vertices[i],s_batch.emplace_back_uninitialized());
    s_gpu_spans.clear();s_gpu_span_vertices=0;
  }
""" + old
part(old, new, 'materialise',whole='// NATIVE_FUSED_BATCH_V1: a conversion after fused spans keeps their order')

# 3. EmitPrimitive: base offset and span recording
old = '  const uint32_t base=uint32_t(s_batch.size());\n  if (cmd.prim_type>=0xA8 && cmd.prim_type<=0xB8) {'
new = '  const uint32_t base=uint32_t(NativeBatchSize());\n  if (cmd.prim_type>=0xA8 && cmd.prim_type<=0xB8) {'
part(old, new, 'emit base')
old = """  if(reused)for(const auto& vertex:v)EmitVertex(vertex);
  else for(size_t i=0;i<count;++i)EmitVertex(v[i]);"""
new = """  if(s_gpu_vertex_mode && NativeFusedBatchEnabled() && s_batch.empty()) { // pack straight from the decoded vertices at flush
    const size_t emitted=reused?v.size():count;
    s_gpu_spans.push_back({v.data(),emitted});s_gpu_span_vertices+=emitted;
  } else {
    if(reused)for(const auto& vertex:v)EmitVertex(vertex);
    else for(size_t i=0;i<count;++i)EmitVertex(v[i]);
  }"""
part(old, new, 'emit spans',whole='// pack straight from the decoded vertices at flush')

# 4. NativeUploadBatch
old = """  uint8_t* destination=nullptr;
  const size_t reserved=NativeStreamReserve(GL_ARRAY_BUFFER,s_vertex_ring,stride*s_batch.size(),stride,&destination);
  if(destination) { // pack once, straight into the mapped ring"""
new = """  uint8_t* destination=nullptr;
  const size_t reserved=NativeStreamReserve(GL_ARRAY_BUFFER,s_vertex_ring,stride*NativeBatchSize(),stride,&destination);
  if(!s_gpu_spans.empty()) { // NATIVE_FUSED_BATCH_V1: decoded vertices -> ring, one pass
    static std::vector<uint8_t> fused_temp;
    uint8_t* out=destination;
    if(!out){if(fused_temp.size()<stride*s_gpu_span_vertices)fused_temp.resize(stride*s_gpu_span_vertices);out=fused_temp.data();}
    uint8_t* cursor=out;
    for(const auto& span:s_gpu_spans)for(size_t i=0;i<span.count;++i,cursor+=stride)NativePackGpuVertex(span.vertices[i],reinterpret_cast<float*>(cursor),generators);
    s_upload_first=destination?GLint(reserved/stride):GLint(NativeStreamUpload(GL_ARRAY_BUFFER,s_vertex_ring,fused_temp.data(),stride*s_gpu_span_vertices,stride)/stride);
  } else if(destination) { // pack once, straight into the mapped ring"""
part(old, new, 'upload',whole='// NATIVE_FUSED_BATCH_V1: decoded vertices -> ring, one pass')
old = '  native_trace::mark("upload.bytes",stride*s_batch.size(),sizeof(BatchVertex)*s_batch.size());'
new = '  native_trace::mark("upload.bytes",stride*NativeBatchSize(),sizeof(BatchVertex)*NativeBatchSize());'
part(old, new, 'upload mark')

# 5. FlushBatch (idempotent rewrite of the body; a frame-trace mark left over from before the rewrite is collapsed to one)
a = s.index('static void FlushBatch() {')
tail = '  s_batch.clear();\n  s_indices.clear();\n}'
tail2 = '  s_batch.clear();s_gpu_spans.clear();s_gpu_span_vertices=0;\n  s_indices.clear();\n}'
b = s.find(tail2, a)
if b >= 0:
    b += len(tail2)
else:
    b = s.index(tail, a) + len(tail)
body = s[a:b]
body = body.replace('s_batch.empty()', '(NativeBatchSize()==0)').replace('s_batch.size()', 'NativeBatchSize()')
body = body.replace(tail, tail2) if tail in body else body
mark = '  native_trace::mark("batch.vertices",NativeBatchSize(),s_indices.size());\n'
while body.count(mark) > 1:       # fix_frame_trace and this stage both used to insert it
    i = body.rindex(mark)
    body = body[:i] + body[i + len(mark):]
s = s[:a] + body + s[b:]
f_path = f
f_path.write_text(s)
print('fused GPU-mode batches installed')
