"""Expose hidden waits and packing within the reported renderer costs, without changing scheduling."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
f=rt/'src/hle/gx/renderer_gl.cpp';s=f.read_text()
def replace(old,new):
 global s
 if new in s:return
 assert s.count(old)==1,(old,s.count(old));s=s.replace(old,new,1)
replace('static void NativePersistentWait(NativeUploadRing& ring,unsigned segment){',
 'static void NativePersistentWait(NativeUploadRing& ring,unsigned segment){\n native_trace::Scope upload_wait("gx.upload_wait"); // NATIVE_HOT_PATH_DETAIL_V1')
replace('   glFinish();for(unsigned i=0;i<4;++i)',
 '   native_trace::Scope oversized_wait("gx.upload_oversized_wait"); // NATIVE_HOT_PATH_DETAIL_V1\n   glFinish();for(unsigned i=0;i<4;++i)')
replace('  if(!s_gpu_spans.empty()) { // NATIVE_FUSED_BATCH_V1: decoded vertices -> ring, one pass',
 '  if(!s_gpu_spans.empty()) { // NATIVE_FUSED_BATCH_V1: decoded vertices -> ring, one pass\n    native_trace::Scope pack_scope("gx.vertex_pack"); // NATIVE_HOT_PATH_DETAIL_V1')
replace('  auto pack_batch=[&](uint8_t* target){',
 '  auto pack_batch=[&](uint8_t* target){\n    native_trace::Scope pack_scope("gx.vertex_pack"); // NATIVE_HOT_PATH_DETAIL_V1')
replace('static void NativeUploadGpuXf(){',
 'static void NativeUploadGpuXf(){\n native_trace::Scope state_xf("gx.state_xf"); // NATIVE_HOT_PATH_DETAIL_V1')
if s!=f.read_text():f.write_text(s)
f=rt/'src/hle/gx.cpp';s=f.read_text()
replace('        finished.wait(lock, [&] { return outstanding == 0; });',
 '        {native_trace::Scope decode_wait("gx.decode_wait"); // NATIVE_HOT_PATH_DETAIL_V1\n        finished.wait(lock, [&] { return outstanding == 0; });}')
if 'native_trace::Scope release("gx.command_release")' not in s:
 replace('~NativeCommandPoolRelease(){if(active){s_command_pool.clear();s_command_pool_busy=false;}}',
  '~NativeCommandPoolRelease(){if(active){native_trace::Scope release("gx.command_release");s_command_pool.clear();s_command_pool_busy=false;}}')
if s!=f.read_text():f.write_text(s)
print('Packing, mapped-ring waits, decode join, XF state upload and command release have separate timing scopes.')
