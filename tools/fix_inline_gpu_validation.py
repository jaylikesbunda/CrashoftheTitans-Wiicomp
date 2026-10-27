"""Accumulate GPU validation while decoding; retain the old scan for A/B and partial draws."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
h=rt/'include/runtime/gx_command.h';s=h.read_text()
if 'NATIVE_INLINE_GPU_VALIDATION_V1' not in s:
 s=s.replace('    std::shared_ptr<DrawRaw> raw;','''    // NATIVE_INLINE_GPU_VALIDATION_V1: only completely decoded vertices count.
    size_t native_gpu_checked=0;bool native_gpu_valid=true;
    std::shared_ptr<DrawRaw> raw;''',1);h.write_text(s)
f=rt/'src/hle/gx/fifo_parser.cpp';s=f.read_text()
helper='''// NATIVE_INLINE_GPU_VALIDATION_V1: same predicate as the post-decode scan.
static bool NativeInlineGpuValidation(){static const bool on=!std::getenv("NWII_SLOW_GPU_VALIDATION");return on;}
static inline void NativeValidateDecodedGpuVertex(GXCommand& c,const VertexData& v){
 if(c.native_gpu_snapshot && NativeInlineGpuValidation()){
  ++c.native_gpu_checked;
  c.native_gpu_valid=c.native_gpu_valid && v.has_pos && !v.pre_xf && unsigned(v.posMtxIdx)*4+12<=256 && 3u*(v.posMtxIdx&31)+9<=96;
 }
}
'''
if helper not in s:
 anchor='template<bool Direct> static void NativeDecodeDraw(GXCommand& c) {'
 assert s.count(anchor)==1;s=s.replace(anchor,helper+anchor,1)
# Each part restores itself independently when earlier staging rewrites a region.
def part(old,new):
 global s
 if new in s:return
 assert s.count(old)==1,(old,s.count(old));s=s.replace(old,new,1)
# Both decoders count only completed vertices; partial reuse retains the scan.
for rollback in ('c.vertices.pop_back();','c.vertices.resize(i);'):
  old='''        if (off > buf_size)
            {'''+rollback+'''return;}

        // Transform by the per-draw matrix snapshot'''
  call='if(!reuse)NativeValidateDecodedGpuVertex(c,vtx);' if 'resize' in rollback else 'NativeValidateDecodedGpuVertex(c,vtx);'
  new=old.replace('\n\n        // Transform','\n        '+call+'\n\n        // Transform')
  part(old,new)
old=' NativeDecodeDispatch(c);\n if(c.native_gpu_snapshot){'
new=' c.native_gpu_checked=0;c.native_gpu_valid=true;\n NativeDecodeDispatch(c);\n if(c.native_gpu_snapshot){'
part(old,new)
old='  for(const auto& v:c.vertices)valid=valid && v.has_pos && !v.pre_xf && unsigned(v.posMtxIdx)*4+12<=256 && 3u*(v.posMtxIdx&31)+9<=96;'
new='''  if(NativeInlineGpuValidation() && c.native_gpu_checked==c.vertices.size())valid=c.native_gpu_valid;
  else {'''+old.strip()+'''} // A/B or incompletely decoded reuse draw'''
part(old,new)
if s!=f.read_text():f.write_text(s)
# High-reuse draws have few decoded vertices: the post-scan is cheap and the
# extra accumulation slowed sharing=8 in the whole-decoder probe. Keep the scan.
s=f.read_text();a=s.index('template<int P,int N,int T> static void NativeIndexedDecodeDraw');b=s.index('static bool NativeTryIndexed',a)
part=s[a:b].replace('        NativeValidateDecodedGpuVertex(c,vtx);','        if(!reuse)NativeValidateDecodedGpuVertex(c,vtx);')
s=s[:a]+part+s[b:]
if s!=f.read_text():f.write_text(s)
print('GPU validation fused for expanded decode; reused/partial draws retain original scan.')
