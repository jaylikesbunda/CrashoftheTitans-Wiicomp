"""Repeated late-stage source transforms must preserve each other's helpers."""
from pathlib import Path
import subprocess,sys,hashlib,json
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
scripts=['fix_vertex_texgens.py','fix_measured_gameplay_perf.py','fix_normal_draw_probe.py','fix_frame_trace.py','fix_trace_bottlenecks.py','fix_broad_memory_decode.py','fix_vertex_inplace.py']
scripts+=['fix_inline_psq_ram_writes.py','fix_index_tuple_reuse.py','fix_native_plain_leaf_family.py','fix_gpu_vertex_pipeline.py','fix_renderer_streaming.py','fix_exact_texture_cache.py']
scripts+=['fix_renderer_submission.py','fix_budget_trace.py','fix_display_list_gpu_ownership.py']
scripts+=['fix_gather_redirect_span.py','fix_mmio_lookup_cache.py','fix_dispatch_exception_scope.py','fix_gpu_uniform_stream.py','fix_inline_gather_mmio.py']
scripts+=['fix_shared_draw_bytes.py','fix_gpu_input_packing.py']
scripts+=['fix_uniform_lookup_cache.py','fix_mem2_inline.py','fix_rlwnm_native.py']
scripts+=['fix_native_bulk_helpers.py','fix_fifo_ownership_transfer.py']
scripts+=['fix_dense_dispatch.py','fix_bounded_blocks.py','fix_gl_raster_shadow.py']
scripts+=['fix_shader_source_reuse.py','fix_sampler_mode_cache.py','fix_watch_span_guards.py']
scripts+=['fix_wait_cause_trace.py']
scripts+=['fix_ctx_restrict_variant.py']
paths=[rt/'include/runtime/cpu_context.h',rt/'include/runtime/native_frame_trace.h',rt/'src/hle/gx/fifo_parser.cpp',rt/'src/hle/gx/renderer_gl.cpp']
paths+=[rt/'include/runtime/gx_command.h',rt/'include/runtime/texture_cache_gl.h',rt/'src/hle/texture_cache_gl.cpp',rt/'src/hle/gx/tev_shader_gen.cpp']
paths+=[rt/'src/hle/gx.cpp',rt/'src/hw/mmio_dispatcher.cpp',rt/'include/runtime/hw/mmio_dispatcher.h',p/'build/crash-generated/main_output.cpp']
paths+=[rt/'include/runtime/gx/fifo_parser.h']
paths+=[rt/'src/hle/interpreter.cpp']
paths+=[rt/'src/core/main.cpp']+list((p/'build/crash-generated').glob('output_*.cpp'))
# Running the stage scripts in place can rewrite staged source that later stages (ring, fused batch, decode tuning, ...) had changed, and those
# stages stay skipped behind their own markers. The check must not leave the staged tree different from how it found it.
_snapshot={f:f.read_bytes() for f in paths if f.exists()}
import atexit
def _restore_staged_tree():
    for f,b in _snapshot.items():
        if not f.exists() or f.read_bytes()!=b:f.write_bytes(b)
atexit.register(_restore_staged_tree)
states=[]
for iteration in range(2):
    subprocess.run([sys.executable,str(p/'tools/reset_ctx_signatures.py')],check=True,capture_output=True)
    for name in scripts:
        r=subprocess.run([sys.executable,str(p/'tools'/name)],capture_output=True,text=True)
        if r.returncode:raise RuntimeError(name+' failed:\n'+r.stdout+r.stderr)
    states.append({str(f.relative_to(p/'build')):hashlib.sha256(f.read_bytes()).hexdigest() for f in paths})
assert states[0]==states[1],'Repeated staging changes source'
r=(rt/'src/hle/gx/renderer_gl.cpp').read_text();assert r.count('static void NativeWriteConvertedVertex(')==1 and r.count('static BatchVertex ConvertVertex(')==1
assert 's_upload_first=GLint(NativeStreamUpload' in r and 'NATIVE_GPU_INPUT_PACKING_V1' in r
f=(rt/'src/hle/gx/fifo_parser.cpp').read_text();a=f.index('static void NativeDecodeDispatch(');b=f.index('\nvoid FifoParser::Parse(',a)
assert f[a:b].count('native_trace::Scope trace_decode')==1
assert f.count('g_native_normal_probe_count=0')==1 and f.count('auto& normal_count=g_native_normal_probe_count;')==2
(p/'reports/broad-stage-idempotence.json').write_text(json.dumps(dict(pass_=True,source_hashes=states[-1],iterations=2),indent=2)+'\n')
print('Broad stage idempotence/helper/scope/shared-probe checks pass.')
