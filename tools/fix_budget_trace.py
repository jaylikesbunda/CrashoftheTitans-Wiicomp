"""Final-stage tracing: isolate draw calls from upload and time EFB blits locally."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]
rt=p/'build/nwii-full-source/nWiiRuntime'
for src,dst in [('frame_trace.h','native_frame_trace.h'),('frame_trace_gl.h','native_frame_trace_gl.h')]:
    target=rt/'include/runtime'/dst
    value=(p/'runtime-overlays'/src).read_text()
    if target.read_text()!=value:target.write_text(value)
f=rt/'src/hle/gx/renderer_gl.cpp';s=f.read_text()
if 'NATIVE_BUDGET_DRAW_V1' not in s:
    a=s.index('  { // NATIVE_GPU_DRAW_SAMPLE_V1:')
    b=s.index('\n  if (s_glerr)',a)
    s=s[:a]+'''  { // NATIVE_BUDGET_DRAW_V1: queries surround only the draw, never index upload.
  size_t index_offset=0;
  if(!s_indices.empty()) {
    native_trace::Scope upload_scope("gx.index_upload");
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,s_ebo);
    index_offset=NativeStreamUpload(GL_ELEMENT_ARRAY_BUFFER,s_index_ring,s_indices.data(),s_indices.size()*sizeof(uint32_t),4);
  }
  native_trace::mark("draw.calls",1,s_indices.empty()?s_batch.size():s_indices.size());
  native_trace::Scope draw_submit("gx.driver_draw");
  native_trace::GPUScope trace_gpu_draw("gpu.draw_sample_us",8);
  if(s_indices.empty())glDrawArrays(GL_TRIANGLES,s_upload_first,s_batch.size());
  else glDrawElementsBaseVertex(GL_TRIANGLES,GLsizei(s_indices.size()),GL_UNSIGNED_INT,reinterpret_cast<void*>(index_offset),s_upload_first);
  }
''' +s[b:]
for function,name in [('NativeCopyEfbTexture','gpu.efb_blit_us'),('NativeLatchXfb','gpu.xfb_blit_us')]:
    a=s.index('static void '+function+'(');b=s.index('\n}',a)
    region=s[a:b]
    marker='native_trace::GPUScope budget_blit("'+name+'")'
    if marker not in region:
        start=region.index('  glBlitFramebuffer(');end=region.index(';',start)+1
        region=region[:start]+'  { '+marker+';\n'+region[start:end]+'\n  }'+region[end:]
        s=s[:a]+region+s[b:]
if f.read_text()!=s:f.write_text(s)
def wrap_calls(path,call,name,stride=1):
    """Wrap a GL call statement, preserving surrounding if/else control flow."""
    s=path.read_text();marker='NATIVE_BUDGET_'+call.upper()
    token=call+'(';at=0
    prefix='{ native_trace::GPUScope budget_operation("'+name+'",'+str(stride)+'); '
    while True:
        a=s.find(token,at)
        if a<0:break
        if s[max(0,a-len(prefix)):a]==prefix:
            at=a+len(token);continue
        b=a+len(token);depth=1
        while depth:
            if s[b]=='(':depth+=1
            elif s[b]==')':depth-=1
            b+=1
        assert s[b]==';',(path,call,s[a:b+10])
        b+=1
        wrapper=prefix+s[a:b]+' }'
        s=s[:a]+wrapper+s[b:];at=a+len(wrapper)
    if marker not in s:s+='\n// '+marker+'\n'
    if '#include "runtime/native_frame_trace_gl.h"' not in s:s='#include "runtime/native_frame_trace_gl.h"\n'+s
    if path.read_text()!=s:path.write_text(s)
for path in (f,rt/'src/core/main.cpp'):
    wrap_calls(path,'glClear','gpu.clear_us')
for path in (f,rt/'src/hle/texture_cache_gl.cpp',rt/'src/core/main.cpp'):
    wrap_calls(path,'glTexImage2D','gpu.texture_transfer_us')
    wrap_calls(path,'glTexSubImage2D','gpu.texture_subtransfer_us')
wrap_calls(rt/'src/core/main.cpp','glBlitFramebuffer','gpu.present_blit_us')
# Buffer uploads have many calls; sample to limit diagnostic driver overhead.
wrap_calls(f,'glBufferSubData','gpu.buffer_transfer_us',8)
wrap_calls(f,'glBufferData','gpu.buffer_allocate_transfer_us',8)
print('Budget tracing: draw/upload census, 1/8 queries, isolated clear/texture/copy/presentation queries, driver scopes.')
