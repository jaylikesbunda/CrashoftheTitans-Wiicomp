"""Stage format loaders and exact-content CPU/GPU retention after packed decoding."""
from pathlib import Path
from specialized_decode_codegen import generate_specialized
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
for name in ('native_geometry_stats.h',):
 (rt/'include/runtime'/name).write_text((p/'runtime-overlays'/name).read_text())
h=rt/'include/runtime/gx_command.h';s=h.read_text()
if 'bool native_geometry_retained=' not in s:
 s=s.replace('    NativePackedBuffer native_packed;','    bool native_geometry_retained=false;\n    NativePackedBuffer native_packed;',1);h.write_text(s)
f=rt/'src/hle/gx/fifo_parser.cpp';s=f.read_text()
if '#include "runtime/native_geometry_stats.h"' not in s:s='#include <mutex>\n#include <unordered_map>\n#include "runtime/native_geometry_stats.h"\n'+s
# Packed stage removes everything between its markers on each build.
anchor='static bool NativeTryPackedDecode(GXCommand& c){'
if '// Exact-content reuse:' in s:
 a=s.index('// Exact-content reuse:');b=s.index('static void NativeGeometryStore(',a);b=s.index('\n}',b)+2
 s=s[:a]+(p/'runtime-overlays/retained_decode.inc').read_text()+s[b:]
if '// NATIVE_FORMAT_LOADERS_V1' not in s:
 s=s.replace(anchor,generate_specialized(s)+'\n'+(p/'runtime-overlays/observed_vertex_formats.inc').read_text()+'\n'+(p/'runtime-overlays/retained_decode.inc').read_text()+'\n'+anchor,1)
 s=s.replace(' bool ok=false;\n if(fast){',''' std::vector<uint8_t> geometry_key;uint64_t geometry_hash=0;
 unsigned observed=NativeObservedFormat(r);
 bool retain=false,hit=false;
 if(fast || observed){native_trace::Scope validate("gx.geometry_validate");retain=NativeGeometryKey(r,inputs,geometry_key);if(!retain && native_hud::on.load(std::memory_order_relaxed))native_geometry::retained_rejects.fetch_add(1,std::memory_order_relaxed);}
 if(retain){geometry_hash=NativeGeometryHash(geometry_key);hit=NativeGeometryFind(geometry_key,geometry_hash,output,indices);}
 std::vector<NativeGeometrySpan> geometry_spans;
 if(retain && !hit)retain=NativeGeometryCapture(r,geometry_spans);
 bool ok=hit;
 if(!hit && fast){''',1)
 # Restrict replacements to the production dispatch, retaining the reference.
 a=s.index(anchor);b=s.index('// Tuple-reused draws',a)
 dispatch=s[a:b].replace('ok=NativePackedIndexed<','ok=NativeFormatDispatch<').replace(' else {\n bool direct=', ' else if(!hit && observed){ok=NativeObservedDispatch(observed,c,output,inputs,words,defaults); }\n else if(!hit){\n bool direct=')
 dispatch=dispatch.replace(' if(!ok)return false;',''' if(!ok)return false;
 if(retain && !hit)NativeGeometryStore(std::move(geometry_key),geometry_hash,output,indices,std::move(geometry_spans));
 c.native_geometry_retained=hit; // GPU admission requires demonstrated reuse.''',1)
 s=s[:a]+dispatch+s[b:]
s=s.replace(' std::vector<uint8_t> geometry_key;uint64_t geometry_hash=0;',' static thread_local std::vector<uint8_t> geometry_key;uint64_t geometry_hash=0;')
f.write_text(s)
f=rt/'src/hle/gx/renderer_gl.cpp';s=f.read_text()
if '#include "runtime/native_geometry_stats.h"' not in s:s='#include "runtime/native_geometry_stats.h"\n'+s
start='// NATIVE_RETAINED_GPU_V1';end='// NATIVE_RETAINED_GPU_END'
if start in s:
 a=s.index(start);b=s.index(end,a)+len(end);s=s[:a]+s[b:]
anchor='static void NativeUploadBatch() {'
s=s.replace(anchor,start+'\n'+(p/'runtime-overlays/retained_gpu.inc').read_text()+'\n'+end+'\n'+anchor,1)
anchor='  static size_t previous_stride=0;'
hook='  if(NativeRetainedGpuBind(stride,generators))return; // NATIVE_RETAINED_GPU_BIND_V1\n'
if hook not in s:s=s.replace(anchor,hook+anchor,1)
f.write_text(s)
print('Format-specialized loaders and bounded exact-content geometry retention staged.')
