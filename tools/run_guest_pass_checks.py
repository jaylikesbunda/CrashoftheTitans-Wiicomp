"""Run the fixtures that cover this pass's changes and print one pass/fail line each (no game launch).

A check is (name, [script, args...]) for self-contained scripts, or (name, [script, args...], target, executable) for scripts that only
generate a fixture: the runner then builds the CMake target in the toolchain build directory and runs the executable (exit 0 = pass).
"""
from pathlib import Path
import subprocess,sys,json,os,time,re
from port_paths import PORT as p,default_storage,winlibs_bin
env=os.environ.copy()
if winlibs_bin():env['PATH']=str(winlibs_bin())+os.pathsep+env['PATH']
toolchain=default_storage()/'toolchain'
checks=[('fifo_transfer',['check_fifo_ownership_transfer.py']),('gather_span',['check_gather_redirect_span.py']),('bulk_helpers',['check_native_bulk_helpers.py']),
 ('watch_guards',['check_watch_span_guards.py']),('inline_gather',['check_inline_gather_mmio.py']),('fast_clock',['check_fast_steady_clock.py']),
 ('idle_wait',['check_guest_idle_wait.py']),('pe_interrupts',['check_pe_interrupts.py','--label','guest_pass']),('wakeups',['check_deterministic_wakeups.py','--name','guest_pass']),
 ('sdk_sqrt',['check_sdk_sqrt.py']),('default_watch_hot',['check_default_watch_hot.py']),('cache_range',['check_cache_range_loops.py']),
 ('gpu_decode',['check_gpu_decode.py']),('index_tuple',['check_index_tuple_decode.py']),('vertex_reuse',['check_vertex_reuse.py']),
 ('dl_ownership',['check_display_list_gpu_ownership.py']),('gpu_vertex',['check_gpu_vertex_pipeline.py']),('shader_reuse',['check_shader_source_reuse.py']),
 ('sampler_cache',['check_sampler_mode_cache.py']),('program_cache',['check_program_binary_cache.py']),('broad_stage',['check_broad_stage.py']),
 ('persistent_upload',['check_persistent_upload.py']),
 ('audio_worker',['check_audio_worker.py']),
 ('early_gpu_signal',['check_early_gpu_signal.py']),
 ('fifo_ok_flag',['check_fifo_ok_flag.py']),
 ('hud_stats',['check_hud_stats.py']),
 ('hud_render',['check_hud_render.py']),
 ('fused_batch',['check_fused_batch.py']),
 ('used_textures',['check_used_texture_binding.py']),
 ('packed_layout',['check_packed_layout.py']),
 ('packed_upload',['check_packed_upload.py']),
 ('format_loaders',['check_format_loaders.py']),
 ('retained_gpu',['check_retained_gpu.py']),
 ('stutter_watch',['check_stutter_watch.py']),
 ('shader_data',['check_shader_data_uniforms.py']),
 ('shader_io',['check_shader_cache_io.py']),
 ('gap_functions',['check_gap_functions.py'],'crash_gap_tests','crash_gap_tests.exe'),
 ('fallthrough_tail',['check_fallthrough_tail.py'],'crash_tail_tests','crash_tail_tests.exe'),
 ('sqrt_fast',['check_sqrt_fast.py'],'crash_sqrt_tests','crash_sqrt_tests.exe'),
 ('ram_write_watch',['check_ram_write_watch.py'],'crash_ramwatch_tests','crash_ramwatch_tests.exe'),
 ('mmu_page_table',['check_mmu_page_table.py'],'crash_mmu_tests','crash_mmu_tests.exe'),
 ('fastmem_base',['check_fastmem_static_base.py']),
 ('codegen_slim',['check_codegen_slim.py','1500'],'crash_slim_tests','crash_slim_tests.exe'),
 ]+([('preserve',['preserve.py','--check'])] if (p/'preservation/baseline.json').is_file() else [])
only=set(sys.argv[1:]);results=[]
def summary(text):
    lines=[l for l in text.strip().splitlines() if l.strip() and not re.match(r'^\[(MMIO|DIAG|WARN|Interp)\]',l)]
    return (lines[-1] if lines else '')[:140]
for item in checks:
    name,cmd=item[0],item[1]
    if only and name not in only:continue
    t=time.time();r=subprocess.run([sys.executable,str(p/'tools'/cmd[0])]+cmd[1:],capture_output=True,text=True,env=env,timeout=1500)
    code=r.returncode;last=summary(r.stdout+r.stderr)
    if code==0 and len(item)==4:
        b=subprocess.run(['cmake','--build',str(toolchain),'--target',item[2],'-j','4'],capture_output=True,text=True,env=env,timeout=3600)
        if b.returncode:code=b.returncode;last='build failed: '+summary(b.stdout+b.stderr)
        else:
            x=subprocess.run([str(toolchain/item[3])],capture_output=True,text=True,env=env,timeout=1800,cwd=str(toolchain));code=x.returncode
            matched=[l for l in x.stdout.splitlines() if re.match(r'^[A-Z_]{4,} ',l)]
            last=(matched[-1] if matched else summary(x.stdout+x.stderr))[:140]
    results.append(dict(name=name,exit_code=code,seconds=round(time.time()-t),last=last));print(f"{'PASS' if code==0 else 'FAIL'} {name} ({results[-1]['seconds']}s) {last[:110]}",flush=True)
(p/'reports/guest-pass-checks.json').write_text(json.dumps(results,indent=2)+'\n')
sys.exit(1 if any(r['exit_code'] for r in results) else 0)
