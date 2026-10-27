"""Build full NWii runtime, import Crash's function map, and link native game.

All modifications and outputs are isolated from strict decomp sources/manifests.
Build storage defaults to C: to avoid filling the disc-image drive.
"""
from pathlib import Path
import argparse, json, os, subprocess, sys, time
from port_paths import PORT,INDEX,default_dol,default_storage
ap=argparse.ArgumentParser()
ap.add_argument('--storage',type=Path,default=default_storage())
ap.add_argument('--jobs',type=int,default=10)
ap.add_argument('--skip-generation',action='store_true')
ap.add_argument('--generate-only',action='store_true',help='create generated game sources before first-time post-generation staging')
ap.add_argument('--stage-only',action='store_true',help='qualify source transforms without compiling the final game')
ap.add_argument('--dol',type=Path,default=default_dol())
ap.add_argument('--index',type=Path,default=INDEX)
ap.add_argument('--backend',choices=('cpp','llvm','hybrid'),default=None,
                help='guest code backend: cpp (default, C++-only) or llvm/hybrid (emit the '
                     'reference/backend-reports/llvm-ir-functions.json slice as LLVM IR). '
                     'Overrides NWII_BUILD_IR_BACKEND.')
args=ap.parse_args()
import region
release=region.for_dol(args.dol)
if args.index==INDEX and not release.reference:args.index=release.index
if args.storage==default_storage() and not os.environ.get('NWII_BUILD_STORAGE') and not release.reference:
    args.storage=args.storage.with_name('CrashTitans-'+release.game_id)
storage=args.storage.resolve();storage.mkdir(parents=True,exist_ok=True)
backend=args.backend or os.environ.get('NWII_BUILD_IR_BACKEND') or 'cpp'
backend={'0':'cpp','1':'hybrid','off':'cpp','on':'hybrid'}.get(backend.strip().lower(),backend.strip().lower())
if backend not in ('cpp','llvm','hybrid'):
    raise SystemExit('backend must be cpp|llvm|hybrid (got %r)'%backend)
os.environ['NWII_BUILD_IR_BACKEND']=backend
os.environ['NWII_BUILD_STORAGE']=str(storage)
os.environ['NWII_INPUT_DOL']=str(args.dol.resolve())
os.environ['NWII_INPUT_INDEX']=str(args.index.resolve())
reports=PORT/'reports';reports.mkdir(exist_ok=True);stages=[]
def run(name,cmd):
    skip=release.skips(Path(str(cmd[1])).name) if len(cmd)>1 else None
    if skip:
        print(name+' skipped for '+release.game_id+': '+skip,flush=True)
        stages.append({'stage':name,'skipped':skip});return
    print(name,flush=True);start=time.monotonic();log=reports/(name+'.log')
    with log.open('wb') as f:p=subprocess.run([str(c) for c in cmd],cwd=PORT,stdout=f,stderr=subprocess.STDOUT)
    stages.append({'stage':name,'command':[str(c) for c in cmd],'seconds':time.monotonic()-start,'exit_code':p.returncode,'log':str(log)})
    (reports/'native-build-stages.json').write_text(json.dumps(stages,indent=2))
    if p.returncode:
        print(log.read_text(errors='replace')[-12000:]);raise SystemExit(p.returncode)
# A checkout is reference material; never patch it in place.
expected='595f176f1d24cc54ff2e8389feed12d7fb553cc2'
pin_file=PORT/'vendor/NWiiRecomp/PINNED_COMMIT'  # shipped builder packs carry no .git
actual=pin_file.read_text().strip() if pin_file.is_file() else subprocess.check_output(['git','rev-parse','HEAD'],cwd=PORT/'vendor/NWiiRecomp',text=True).strip()
if actual!=expected:raise SystemExit('NWiiRecomp checkout differs from tested pin')
for name in ('nwii-full-source','crash-generated'):
    link=PORT/'build'/name;target=storage/name
    if not link.exists():
        target.mkdir(parents=True,exist_ok=True)
        # Populate only the newly created staging directory.
        if name=='nwii-full-source':
            import shutil
            shutil.copytree(PORT/'vendor/NWiiRecomp',target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('.git'))
        subprocess.run(['cmd','/c','mklink','/J',str(link),str(target)],check=True,capture_output=True)
build=storage/'toolchain';src=PORT/'build/nwii-full-source';generated=PORT/'build/crash-generated'
run('native-mtime-snapshot',[sys.executable,PORT/'tools/preserve_mtimes.py','snapshot'])
# NATIVE_TRACKER_HOST_WRITES_V1 (no-op with NWII_BUILD_GUEST_ARENA=0) and NATIVE_FALLBACK_CENSUS_V1 (measurement only) are
# applied after the texture stages. Restores run in reverse apply order: render split, census, tracker.
run('native-render-queue-restore',[sys.executable,PORT/'tools/fix_render_queue.py','--restore'])  # NATIVE_RENDER_QUEUE_V1: applied last, so it restores first
run('native-texture-cpu-gl-split-restore',[sys.executable,PORT/'tools/fix_texture_cpu_gl_split.py','--restore'])  # NATIVE_TEXTURE_CPU_GL_SPLIT_V1: applied after the state split, so it restores first
run('native-renderer-state-split-restore',[sys.executable,PORT/'tools/fix_renderer_state_split.py','--restore'])  # NATIVE_RENDERER_STATE_SPLIT_V1: applied last, so it restores first
# NATIVE_TEXTURE_VALIDATION_MEMO_V1 and NATIVE_TEXTURE_DECODE_FAST_V1 are applied after the census (memo) and after the texture
# stages; restores in reverse order.
# NATIVE_TEXTURE_ATTRIBUTION_V1 anchors on the memo's query line, so it restores before the memo.
run('native-texture-attribution-restore',[sys.executable,PORT/'tools/fix_texture_attribution.py','--restore'])
run('native-texture-validation-memo-restore',[sys.executable,PORT/'tools/fix_texture_validation_memo.py','--restore'])
run('native-texture-decode-fast-restore',[sys.executable,PORT/'tools/fix_texture_decode_fast.py','--restore'])
# NATIVE_DIRECT_TEXCOORD_LOADER_V1 anchors on the generic position loader's text, so it restores before that stage.
run('native-direct-texcoord-restore',[sys.executable,PORT/'tools/fix_direct_texcoord_loader.py','--restore'])
# NATIVE_FALLBACK_REASONS_V1 adds its call after the census counter, so it restores before the census.
run('native-fallback-gates-restore',[sys.executable,PORT/'tools/fix_fallback_gates.py','--restore'])
run('native-fallback-census-restore',[sys.executable,PORT/'tools/fix_fallback_census.py','--restore'])
run('native-tracker-host-writes-restore',[sys.executable,PORT/'tools/fix_tracker_host_writes.py','--restore'])
# NATIVE_MEM_PROOF_V1 wraps the guest-batch output, so it restores first.
run('native-poll-spin-outline-restore',[sys.executable,PORT/'tools/fix_poll_spin_outline.py','--restore'])  # NATIVE_POLL_SPIN_OUTLINE_V1; staged after the arena, so it restores first
run('native-texture-validation-restore',[sys.executable,PORT/'tools/fix_texture_validation.py','--restore'])  # NATIVE_TEXTURE_VALIDATION_V1; applied after the hash cache
run('native-texture-hash-cache-restore',[sys.executable,PORT/'tools/fix_texture_hash_cache.py','--restore'])  # NATIVE_TEXTURE_HASH_CACHE_V1; applied last among the texture-cache stages
run('native-shader-preload-restore',[sys.executable,PORT/'tools/fix_shader_preload.py','--restore'])  # NATIVE_SHADER_PRELOAD_V1; renderer_gl.cpp, restores before the other renderer stages
run('native-guest-arena-restore',[sys.executable,PORT/'tools/fix_guest_arena.py','--restore'])  # NATIVE_GUEST_ARENA_V1; the stage runs last, so it restores first
run('native-hot-pgo-restore',[sys.executable,PORT/'tools/fix_hot_pgo.py','--restore'])  # NATIVE_HOT_PGO_V1; the stage runs after the arena, so it restores first
run('native-mem-proofs-restore',[sys.executable,PORT/'tools/fix_guest_mem_proofs.py','--restore'])
run('native-guest-batch-restore',[sys.executable,PORT/'tools/fix_guest_batch.py','--restore'])
run('native-gl-state-redundancy-restore',[sys.executable,PORT/'tools/fix_gl_state_redundancy.py','--restore'])  # NATIVE_GL_STATE_REDUNDANCY_V1; anchors on draw-followups and gl-program-shadow text
run('native-draw-followups-restore',[sys.executable,PORT/'tools/fix_draw_followups.py','--restore'])
run('native-audit-speedups-restore',[sys.executable,PORT/'tools/fix_audit_speedups.py','--restore'])
run('native-heavy-scene-restore',[sys.executable,PORT/'tools/fix_heavy_scene.py','--restore'])
run('native-watch-fold-restore',[sys.executable,PORT/'tools/fix_watch_fold.py','--restore'])  # NATIVE_WATCH_FOLD_V1; runs after v13, so it restores before it
run('native-v13-runtime-restore',[sys.executable,PORT/'tools/fix_v13_runtime.py','--restore'])
# NATIVE_RENDER_LOOP_SPIKES_V1 rewrites main.cpp around the event poll and present, so it restores before render pacing.
run('native-render-loop-spikes-restore',[sys.executable,PORT/'tools/fix_render_loop_spikes.py','--restore'])
run('native-render-pacing-restore',[sys.executable,PORT/'tools/fix_render_pacing.py','--restore'])
run('native-cp-derived-restore',[sys.executable,PORT/'tools/fix_cp_derived_skip.py','--restore'])
run('native-xf-stream-reuse-restore',[sys.executable,PORT/'tools/fix_xf_stream_reuse.py','--restore'])
run('native-direct-fifo-restore',[sys.executable,PORT/'tools/fix_direct_fifo.py','--restore'])
run('native-adaptive-gx-batching-restore',[sys.executable,PORT/'tools/fix_adaptive_gx_batching.py','--restore'])
run('native-gx-architecture-metrics-restore',[sys.executable,PORT/'tools/fix_gx_architecture_metrics.py','--restore'])
run('native-flush-census-restore',[sys.executable,PORT/'tools/fix_flush_reason_census.py','--restore'])
run('native-state-epochs-restore',[sys.executable,PORT/'tools/fix_state_epochs.py','--restore'])
run('native-geometry-watch-restore',[sys.executable,PORT/'tools/fix_unified_dirty_tracking.py','--restore'])
run('native-generic-position-restore',[sys.executable,PORT/'tools/fix_generic_position_loader.py','--restore'])
run('native-perf-census-restore',[sys.executable,PORT/'tools/fix_perf_census.py','--restore'])
run('native-indirect-cache-restore',[sys.executable,PORT/'tools/fix_indirect_call_cache.py','--restore'])
# NATIVE_VERTEX_EMIT_V1 rewrites the same chunk regions that fix_bounded_blocks.py owns and
# runs last, so an incremental (non-regenerated) re-stage needs its restore first.
# install() re-derives the kernel from the pristine body and uninstalls its own block first, so the
# apply is a fixpoint on an already-staged tree (the dispatch follows the TRACE_CALL line when the
# body still has it, else the entry-specialized hot body's opening brace).
run('native-lowering-includes-restore',[sys.executable,PORT/'tools/fix_native_lowering_includes.py','--restore'])
run('native-vertex-emit-restore',[sys.executable,PORT/'tools/vertex_emit_kernel.py','--restore'])
run('native-normalized-entry-switch-restore',[sys.executable,PORT/'tools/fix_normalized_entry_switch.py','--restore'])
run('native-multiple-word-spans-restore',[sys.executable,PORT/'tools/fix_multiple_word_spans.py','--restore'])
run('native-fill-blocks-restore',[sys.executable,PORT/'tools/fix_guest_fill_blocks.py','--restore'])
run('native-poll-guards-restore',[sys.executable,PORT/'tools/fix_poll_guard_outline.py','--restore'])
run('native-xf-generation-restore',[sys.executable,PORT/'tools/fix_render_generation_gates.py','--restore'])
run('native-dcbz-zero-restore',[sys.executable,PORT/'tools/fix_dcbz_zero.py','--restore'])
run('native-gx-work-metrics-restore',[sys.executable,PORT/'tools/fix_gx_work_metrics.py','--restore'])
run('native-parse-work-reuse-restore',[sys.executable,PORT/'tools/fix_parse_work_reuse.py','--restore'])
run('native-decode-work-reuse-restore',[sys.executable,PORT/'tools/fix_decode_work_reuse.py','--restore'])
run('native-texture-shadow-restore',[sys.executable,PORT/'tools/fix_texture_binding_shadow.py','--restore'])
run('native-draw-submission-reuse-restore',[sys.executable,PORT/'tools/fix_draw_submission_reuse.py','--restore'])
run('native-camera-glide-restore',[sys.executable,PORT/'tools/fix_camera_glide.py','--restore'])
run('native-stage',[sys.executable,PORT/'tools/setup_full_build.py'])
# The lowering stage runs after the guest-regions stage, so its restore comes first.
run('native-lowering-restore',[sys.executable,PORT/'tools/fix_native_lowering.py','--restore'])
if not args.generate_only:
    run('native-guest-regions-restore',[sys.executable,PORT/'tools/restore_guest_regions.py'])
    run('native-ctx-signature-reset-stage',[sys.executable,PORT/'tools/reset_ctx_signatures.py'])
    run('native-fallthrough-stage',[sys.executable,PORT/'tools/fix_fallthrough.py'])
    run('native-ipc-stage',[sys.executable,PORT/'tools/fix_deferred_ipc.py'])
    run('native-indexed-psq-stage',[sys.executable,PORT/'tools/fix_indexed_psq.py'])
    run('native-trace-stage',[sys.executable,PORT/'tools/fix_trace_text.py'])
    run('native-png-probe-stage',[sys.executable,PORT/'tools/fix_png_probe.py'])
    run('native-ipc-probe-stage',[sys.executable,PORT/'tools/fix_ipc_probe.py'])
    run('native-ipc-irq-stage',[sys.executable,PORT/'tools/fix_ipc_irq_ack.py'])
    run('native-ipc-reply-stage',[sys.executable,PORT/'tools/fix_ipc_reply_queue.py'])
    run('native-ai-stage',[sys.executable,PORT/'tools/fix_ai_registers.py'])
    run('native-irq-probe-stage',[sys.executable,PORT/'tools/fix_irq_probe.py'])
    run('native-startup-profile-stage',[sys.executable,PORT/'tools/fix_startup_profile.py'])
    run('native-callback-flags-stage',[sys.executable,PORT/'tools/fix_callback_flags.py'])
    run('native-callback-context-stage',[sys.executable,PORT/'tools/fix_callback_context_guard.py'])
    run('native-vi-stage',[sys.executable,PORT/'tools/fix_vi_masks.py'])
    run('native-wait-probe-stage',[sys.executable,PORT/'tools/fix_wait_probe.py'])
    run('native-fp-context-probe-stage',[sys.executable,PORT/'tools/fix_fp_context_probe.py'])
    run('native-interpreter-stage',[sys.executable,PORT/'tools/fix_interpreter_stwux.py'])
    run('native-addze-stage',[sys.executable,PORT/'tools/fix_interpreter_addze.py'])
    run('native-subcarry-stage',[sys.executable,PORT/'tools/fix_interpreter_subcarry.py'])
    run('native-field-stage',[sys.executable,PORT/'tools/fix_field_pacing.py'])
    run('native-efb-probe-stage',[sys.executable,PORT/'tools/fix_efb_probe.py'])
    run('native-projection-stage',[sys.executable,PORT/'tools/fix_projection.py'])
    run('native-depth-masks-stage',[sys.executable,PORT/'tools/fix_gx_depth_masks.py'])
    run('native-tev-banks-stage',[sys.executable,PORT/'tools/fix_tev_register_banks.py'])
    run('native-bp-mask-stage',[sys.executable,PORT/'tools/fix_bp_write_mask.py'])
    run('native-tev-scope-stage',[sys.executable,PORT/'tools/fix_tev_stage_scope.py'])
    run('native-texture-binding-stage',[sys.executable,PORT/'tools/fix_texture_binding.py'])
    run('native-addic-stage',[sys.executable,PORT/'tools/fix_addic_carry.py'])
    run('native-subfic-sraw-stage',[sys.executable,PORT/'tools/fix_subfic_sraw_carry.py'])
    run('native-divw-edge-stage',[sys.executable,PORT/'tools/fix_divw_edge.py'])
    run('native-div64-fast-stage',[sys.executable,PORT/'tools/fix_div64_fast.py'])
    run('native-andis-stage',[sys.executable,PORT/'tools/fix_andis_record.py'])
    run('native-sc-pc-stage',[sys.executable,PORT/'tools/fix_sc_resume_pc.py'])
    run('native-wpar-stage',[sys.executable,PORT/'tools/fix_wpar_gather_redirect.py'])
    run('native-pi-base0-stage',[sys.executable,PORT/'tools/fix_pi_redirect_base0.py'])
    run('native-gx-idle-drain-stage',[sys.executable,PORT/'tools/fix_gx_idle_drain.py'])
    run('native-gx-drain-cv-stage',[sys.executable,PORT/'tools/fix_gx_drain_cv.py'])
    run('native-turbo-stage',[sys.executable,PORT/'tools/fix_turbo_fields.py'])
    run('native-fp-probe-fast-stage',[sys.executable,PORT/'tools/fix_fp_probe_fastpath.py'])
    run('native-gx-mtxdiag-stage',[sys.executable,PORT/'tools/fix_gx_mtx_diag.py'])
    run('native-gl-errtrace-stage',[sys.executable,PORT/'tools/fix_gl_error_trace.py'])
    run('native-ps-rsqrte-stage',[sys.executable,PORT/'tools/fix_ps_rsqrte.py'])
    run('native-gx-desync-stage',[sys.executable,PORT/'tools/fix_gx_desync_probe.py'])
    run('native-gx-redirect-stage',[sys.executable,PORT/'tools/fix_gx_redirect_probe.py'])
    run('native-gx-thread-stage',[sys.executable,PORT/'tools/fix_gx_thread_probe.py'])
    run('native-gx-writer-stage',[sys.executable,PORT/'tools/fix_gx_writer_ring.py'])
    run('native-pi-fifo-stage',[sys.executable,PORT/'tools/fix_pi_fifo_probe.py'])
    run('native-shot-ondemand-stage',[sys.executable,PORT/'tools/fix_screenshot_ondemand.py'])
    run('native-fp-fusion-stage',[sys.executable,PORT/'tools/fix_fp_fusion.py'])
    run('native-scalar-fp-lane-stage',[sys.executable,PORT/'tools/fix_scalar_fp_lanes.py'])
    run('native-lazy-fpu-stage',[sys.executable,PORT/'tools/fix_lazy_fpu.py'])
    run('native-function-lookup-stage',[sys.executable,PORT/'tools/fix_function_lookup.py'])
    run('native-dispatch-switch-stage',[sys.executable,PORT/'tools/fix_dispatch_switch.py'])
    run('native-wpad-emulation-stage',[sys.executable,PORT/'tools/fix_wpad_emulation.py'])
    run('native-controller-stage',[sys.executable,PORT/'tools/fix_ps5_controller.py'])
    run('native-alpha-lighting-stage',[sys.executable,PORT/'tools/fix_alpha_lighting.py'])
configure=['cmake','-S',src,'-B',build,'-G','Ninja','-DCMAKE_BUILD_TYPE=RelWithDebInfo','-DCMAKE_EXE_LINKER_FLAGS=-static','-DSDL_SHARED=OFF','-DSDL_STATIC=ON','-DCMAKE_POLICY_VERSION_MINIMUM=3.5']
for dep in ('sdl2','tomlplusplus','glad'):
    cached=storage/'nwii-full/_deps'/f'{dep}-src'
    if cached.exists():configure.append(f'-DFETCHCONTENT_SOURCE_DIR_{dep.upper()}={cached.as_posix()}')
run('native-configure',configure)
if not args.skip_generation:
    run('native-generator-build',['cmake','--build',build,'--target','crash_game_emit','-j',args.jobs])
    run('native-translation',[build/'nWiiRecomp/crash_game_emit.exe',args.dol.resolve(),args.index.resolve(),src/'nWiiRuntime',generated])
    run('native-translation-inventory',[sys.executable,PORT/'tools/translation_inventory.py'])
    if args.generate_only:
        print('Generated game sources ready for clean staging.')
        raise SystemExit(0)
    run('native-restage',[sys.executable,PORT/'tools/setup_full_build.py'])
    run('native-fallthrough-restage',[sys.executable,PORT/'tools/fix_fallthrough.py'])
    run('native-ipc-restage',[sys.executable,PORT/'tools/fix_deferred_ipc.py'])
    run('native-indexed-psq-restage',[sys.executable,PORT/'tools/fix_indexed_psq.py'])
    run('native-trace-restage',[sys.executable,PORT/'tools/fix_trace_text.py'])
    run('native-png-probe-restage',[sys.executable,PORT/'tools/fix_png_probe.py'])
    run('native-ipc-probe-restage',[sys.executable,PORT/'tools/fix_ipc_probe.py'])
    run('native-ipc-irq-restage',[sys.executable,PORT/'tools/fix_ipc_irq_ack.py'])
    run('native-ipc-reply-restage',[sys.executable,PORT/'tools/fix_ipc_reply_queue.py'])
    run('native-ai-restage',[sys.executable,PORT/'tools/fix_ai_registers.py'])
    run('native-irq-probe-restage',[sys.executable,PORT/'tools/fix_irq_probe.py'])
    run('native-startup-profile-restage',[sys.executable,PORT/'tools/fix_startup_profile.py'])
    run('native-callback-flags-restage',[sys.executable,PORT/'tools/fix_callback_flags.py'])
    run('native-callback-context-restage',[sys.executable,PORT/'tools/fix_callback_context_guard.py'])
    run('native-vi-restage',[sys.executable,PORT/'tools/fix_vi_masks.py'])
    run('native-wait-probe-restage',[sys.executable,PORT/'tools/fix_wait_probe.py'])
    run('native-fp-context-probe-restage',[sys.executable,PORT/'tools/fix_fp_context_probe.py'])
    run('native-interpreter-restage',[sys.executable,PORT/'tools/fix_interpreter_stwux.py'])
    run('native-addze-restage',[sys.executable,PORT/'tools/fix_interpreter_addze.py'])
    run('native-subcarry-restage',[sys.executable,PORT/'tools/fix_interpreter_subcarry.py'])
    run('native-field-restage',[sys.executable,PORT/'tools/fix_field_pacing.py'])
    run('native-efb-probe-restage',[sys.executable,PORT/'tools/fix_efb_probe.py'])
    run('native-projection-restage',[sys.executable,PORT/'tools/fix_projection.py'])
    run('native-depth-masks-restage',[sys.executable,PORT/'tools/fix_gx_depth_masks.py'])
    run('native-tev-banks-restage',[sys.executable,PORT/'tools/fix_tev_register_banks.py'])
    run('native-bp-mask-restage',[sys.executable,PORT/'tools/fix_bp_write_mask.py'])
    run('native-tev-scope-restage',[sys.executable,PORT/'tools/fix_tev_stage_scope.py'])
    run('native-texture-binding-restage',[sys.executable,PORT/'tools/fix_texture_binding.py'])
    run('native-addic-restage',[sys.executable,PORT/'tools/fix_addic_carry.py'])
    run('native-subfic-sraw-restage',[sys.executable,PORT/'tools/fix_subfic_sraw_carry.py'])
    run('native-divw-edge-restage',[sys.executable,PORT/'tools/fix_divw_edge.py'])
    run('native-andis-restage',[sys.executable,PORT/'tools/fix_andis_record.py'])
    run('native-sc-pc-restage',[sys.executable,PORT/'tools/fix_sc_resume_pc.py'])
    run('native-wpar-restage',[sys.executable,PORT/'tools/fix_wpar_gather_redirect.py'])
    run('native-pi-base0-restage',[sys.executable,PORT/'tools/fix_pi_redirect_base0.py'])
    run('native-gx-idle-drain-restage',[sys.executable,PORT/'tools/fix_gx_idle_drain.py'])
    run('native-gx-drain-cv-restage',[sys.executable,PORT/'tools/fix_gx_drain_cv.py'])
    run('native-turbo-restage',[sys.executable,PORT/'tools/fix_turbo_fields.py'])
    run('native-fp-probe-fast-restage',[sys.executable,PORT/'tools/fix_fp_probe_fastpath.py'])
    run('native-gx-mtxdiag-restage',[sys.executable,PORT/'tools/fix_gx_mtx_diag.py'])
    run('native-gl-errtrace-restage',[sys.executable,PORT/'tools/fix_gl_error_trace.py'])
    run('native-ps-rsqrte-restage',[sys.executable,PORT/'tools/fix_ps_rsqrte.py'])
    run('native-gx-desync-restage',[sys.executable,PORT/'tools/fix_gx_desync_probe.py'])
    run('native-gx-redirect-restage',[sys.executable,PORT/'tools/fix_gx_redirect_probe.py'])
    run('native-gx-thread-restage',[sys.executable,PORT/'tools/fix_gx_thread_probe.py'])
    run('native-gx-writer-restage',[sys.executable,PORT/'tools/fix_gx_writer_ring.py'])
    run('native-pi-fifo-restage',[sys.executable,PORT/'tools/fix_pi_fifo_probe.py'])
    run('native-shot-ondemand-restage',[sys.executable,PORT/'tools/fix_screenshot_ondemand.py'])
    run('native-fp-fusion-restage',[sys.executable,PORT/'tools/fix_fp_fusion.py'])
    run('native-scalar-fp-lane-restage',[sys.executable,PORT/'tools/fix_scalar_fp_lanes.py'])
    run('native-lazy-fpu-restage',[sys.executable,PORT/'tools/fix_lazy_fpu.py'])
    run('native-function-lookup-restage',[sys.executable,PORT/'tools/fix_function_lookup.py'])
    run('native-dispatch-switch-restage',[sys.executable,PORT/'tools/fix_dispatch_switch.py'])
    run('native-wpad-emulation-restage',[sys.executable,PORT/'tools/fix_wpad_emulation.py'])
    run('native-reconfigure',configure)
run('native-declared-normals-stage',[sys.executable,PORT/'tools/fix_gx_declared_normals.py'])
run('native-efb-copy-geometry-stage',[sys.executable,PORT/'tools/fix_efb_copy_geometry.py'])
run('native-xfb-presentation-stage',[sys.executable,PORT/'tools/fix_xfb_presentation.py'])
run('native-gx-execution-order-stage',[sys.executable,PORT/'tools/fix_gx_execution_order.py'])
run('native-indexed-zero-base-stage',[sys.executable,PORT/'tools/fix_indexed_zero_base.py'])
run('native-texture-source-probe-stage',[sys.executable,PORT/'tools/fix_texture_source_probe.py'])
run('native-normal-draw-probe-stage',[sys.executable,PORT/'tools/fix_normal_draw_probe.py'])
run('native-draw-archive-limit-stage',[sys.executable,PORT/'tools/fix_draw_archive_limit.py'])
run('native-display-list-banks-stage',[sys.executable,PORT/'tools/fix_display_list_banks.py'])
run('native-vertex-texgens-stage',[sys.executable,PORT/'tools/fix_vertex_texgens.py'])
run('native-tev-swaps-stage',[sys.executable,PORT/'tools/fix_tev_swaps.py'])
run('native-fog-stage',[sys.executable,PORT/'tools/fix_fog.py'])
run('native-indirect-stage',[sys.executable,PORT/'tools/fix_indirect_texturing.py'])
run('native-pixel-center-stage',[sys.executable,PORT/'tools/fix_pixel_center.py'])
run('native-shader-draw-budget-stage',[sys.executable,PORT/'tools/fix_shader_draw_budget.py'])
run('native-diagnostic-cache-stage',[sys.executable,PORT/'tools/fix_runtime_diagnostic_cache.py'])
run('native-texture-samplers-stage',[sys.executable,PORT/'tools/fix_texture_samplers.py'])
run('native-pe-transaction-stage',[sys.executable,PORT/'tools/fix_pe_interrupt_serialization.py'])
run('native-nand-filesystem-stage',[sys.executable,PORT/'tools/fix_nand_filesystem.py'])
run('native-poll-clock-throttle-stage',[sys.executable,PORT/'tools/fix_poll_clock_throttle.py'])
run('native-ipc-reply-header-stage',[sys.executable,PORT/'tools/fix_ipc_reply_header.py'])
run('native-swap-interval-stage',[sys.executable,PORT/'tools/fix_swap_interval.py'])
run('native-fake-pad-stage',[sys.executable,PORT/'tools/fix_fake_pad.py'])
run('native-diag-timeline-stage',[sys.executable,PORT/'tools/fix_diag_timeline.py'])
run('native-watch-chain-stage',[sys.executable,PORT/'tools/fix_watch_chain.py'])
run('native-gpu-noblock-stage',[sys.executable,PORT/'tools/fix_gpu_completion_noblock.py'])
run('native-dec-context-stage',[sys.executable,PORT/'tools/fix_decrementer_context_guard.py'])
run('native-interpreter-callback-return-stage',[sys.executable,PORT/'tools/fix_interpreter_callback_return.py'])
run('native-gx-viewport-stage',[sys.executable,PORT/'tools/fix_gx_viewport.py'])
run('native-tev-output-stage',[sys.executable,PORT/'tools/fix_tev_output_register.py'])
run('native-stfiwx-bits-stage',[sys.executable,PORT/'tools/fix_stfiwx_bits.py'])
run('native-resolution-scale-stage',[sys.executable,PORT/'tools/fix_resolution_scale.py'])
run('native-dsp-ax-stage',[sys.executable,PORT/'tools/fix_dsp_ax.py'])
run('native-audio-jitter-stage',[sys.executable,PORT/'tools/fix_audio_jitter_buffer.py'])
run('native-tev-compare-stage',[sys.executable,PORT/'tools/fix_tev_compare_mode.py'])
run('native-gx-fidelity-stage',[sys.executable,PORT/'tools/fix_gx_fidelity.py'])
run('native-gx-perf-stage',[sys.executable,PORT/'tools/fix_gx_perf.py'])
run('native-gx-lines-points-stage',[sys.executable,PORT/'tools/fix_gx_lines_points.py'])
run('native-gx-fifo-batch-stage',[sys.executable,PORT/'tools/fix_gx_fifo_batch.py'])
run('native-texture-cache-stage',[sys.executable,PORT/'tools/fix_texture_cache.py'])
run('native-blend-diag-stage',[sys.executable,PORT/'tools/fix_blend_diag_and_followups.py'])
run('native-alpha-isolation-stage',[sys.executable,PORT/'tools/fix_alpha_isolation.py'])
run('native-audio-policy-stage',[sys.executable,PORT/'tools/fix_audio_policy_v3.py'])
run('native-blend-diag-v2-stage',[sys.executable,PORT/'tools/fix_blend_diag_v2.py'])
run('native-gx-early-drain-stage',[sys.executable,PORT/'tools/fix_gx_early_drain.py'])
run('native-sched-recorder-stage',[sys.executable,PORT/'tools/fix_sched_recorder.py'])
run('native-callback-return-owner-stage',[sys.executable,PORT/'tools/fix_callback_return_owner.py'])
run('native-dec-repeat-drop-stage',[sys.executable,PORT/'tools/fix_dec_repeat_drop.py'])
run('native-measured-gameplay-perf-stage',[sys.executable,PORT/'tools/fix_measured_gameplay_perf.py'])
run('native-vertex-reuse-stage',[sys.executable,PORT/'tools/fix_vertex_reuse.py'])
run('native-indexed-batches-stage',[sys.executable,PORT/'tools/fix_indexed_batches.py'])
run('native-o2-translation-stage',[sys.executable,PORT/'tools/fix_o2_translation.py'])
run('native-frame-trace-stage',[sys.executable,PORT/'tools/fix_frame_trace.py'])
run('native-trace-bottlenecks-stage',[sys.executable,PORT/'tools/fix_trace_bottlenecks.py'])
run('native-dispatcher-o2-stage',[sys.executable,PORT/'tools/fix_dispatcher_optimization.py'])
run('native-sdk-sqrt-loops-stage',[sys.executable,PORT/'tools/fix_sdk_sqrt_loops.py'])
run('native-perf-quant-gather-stage',[sys.executable,PORT/'tools/fix_perf_quant_gather.py'])
run('native-direct-vertex-stage',[sys.executable,PORT/'tools/fix_direct_vertex_decode.py'])
run('native-guest-idle-wait-stage',[sys.executable,PORT/'tools/fix_guest_idle_wait.py'])
run('native-broad-memory-decode-stage',[sys.executable,PORT/'tools/fix_broad_memory_decode.py'])
run('native-vertex-inplace-stage',[sys.executable,PORT/'tools/fix_vertex_inplace.py'])
run('native-inline-psq-writes-stage',[sys.executable,PORT/'tools/fix_inline_psq_ram_writes.py'])
run('native-index-tuple-reuse-stage',[sys.executable,PORT/'tools/fix_index_tuple_reuse.py'])
run('native-plain-leaf-stage',[sys.executable,PORT/'tools/fix_native_plain_leaf_family.py'])
run('native-gpu-vertex-stage',[sys.executable,PORT/'tools/fix_gpu_vertex_pipeline.py'])
run('native-stream-upload-stage',[sys.executable,PORT/'tools/fix_renderer_streaming.py'])
run('native-exact-texture-stage',[sys.executable,PORT/'tools/fix_exact_texture_cache.py'])
run('native-render-submission-stage',[sys.executable,PORT/'tools/fix_renderer_submission.py'])
run('native-fast-steady-clock-stage',[sys.executable,PORT/'tools/fix_fast_steady_clock.py'])
run('native-budget-trace-stage',[sys.executable,PORT/'tools/fix_budget_trace.py'])
run('native-display-list-ownership-stage',[sys.executable,PORT/'tools/fix_display_list_gpu_ownership.py'])
run('native-gather-redirect-span-stage',[sys.executable,PORT/'tools/fix_gather_redirect_span.py'])
run('native-mmio-lookup-cache-stage',[sys.executable,PORT/'tools/fix_mmio_lookup_cache.py'])
run('native-dispatch-exception-scope-stage',[sys.executable,PORT/'tools/fix_dispatch_exception_scope.py'])
run('native-gpu-uniform-stream-stage',[sys.executable,PORT/'tools/fix_gpu_uniform_stream.py'])
run('native-inline-gather-mmio-stage',[sys.executable,PORT/'tools/fix_inline_gather_mmio.py'])
run('native-shared-draw-bytes-stage',[sys.executable,PORT/'tools/fix_shared_draw_bytes.py'])
run('native-gpu-input-packing-stage',[sys.executable,PORT/'tools/fix_gpu_input_packing.py'])
run('native-uniform-lookup-stage',[sys.executable,PORT/'tools/fix_uniform_lookup_cache.py'])
run('native-mem2-inline-stage',[sys.executable,PORT/'tools/fix_mem2_inline.py'])
run('native-rlwnm-stage',[sys.executable,PORT/'tools/fix_rlwnm_native.py'])
run('native-bulk-helpers-stage',[sys.executable,PORT/'tools/fix_native_bulk_helpers.py'])
run('native-fifo-ownership-stage',[sys.executable,PORT/'tools/fix_fifo_ownership_transfer.py'])
run('native-dense-dispatch-stage',[sys.executable,PORT/'tools/fix_dense_dispatch.py'])
run('native-dcbz-zero-stage',[sys.executable,PORT/'tools/fix_dcbz_zero.py'])
run('native-bounded-blocks-stage',[sys.executable,PORT/'tools/fix_bounded_blocks.py'])
run('native-raster-shadow-stage',[sys.executable,PORT/'tools/fix_gl_raster_shadow.py'])
run('native-shader-source-stage',[sys.executable,PORT/'tools/fix_shader_source_reuse.py'])
run('native-sampler-mode-stage',[sys.executable,PORT/'tools/fix_sampler_mode_cache.py'])
run('native-watch-span-stage',[sys.executable,PORT/'tools/fix_watch_span_guards.py'])
run('native-wait-cause-stage',[sys.executable,PORT/'tools/fix_wait_cause_trace.py'])
run('native-early-drain-default-stage',[sys.executable,PORT/'tools/fix_gx_early_drain_default.py'])
run('native-program-cache-stage',[sys.executable,PORT/'tools/fix_program_binary_cache.py'])
run('native-gap-functions-stage',[sys.executable,PORT/'tools/fix_gap_functions.py'])
run('native-poll-credit-stage',[sys.executable,PORT/'tools/fix_poll_credit.py'])
run('native-cache-range-stage',[sys.executable,PORT/'tools/fix_cache_range_loops.py'])
run('native-ctx-restrict-stage',[sys.executable,PORT/'tools/fix_ctx_restrict_variant.py'])
run('native-interp-histogram-stage',[sys.executable,PORT/'tools/fix_interp_histogram.py'])
run('native-gather-ordinary-stage',[sys.executable,PORT/'tools/fix_gather_ordinary_fast.py'])
run('native-mmu-limits-stage',[sys.executable,PORT/'tools/fix_mmu_constant_limits.py'])
run('native-single-vi-stage',[sys.executable,PORT/'tools/fix_single_vi_source.py'])
run('native-large-block-stage',[sys.executable,PORT/'tools/fix_large_block_cache.py'])
run('native-decode-pack-stage',[sys.executable,PORT/'tools/fix_decode_pack_tuning.py'])
run('native-parallel-decode-stage',[sys.executable,PORT/'tools/fix_parallel_decode.py'])
run('native-fallthrough-tail-stage',[sys.executable,PORT/'tools/fix_fallthrough_tail.py'])
run('native-cheap-hotspots-stage',[sys.executable,PORT/'tools/fix_cheap_guest_hotspots.py'])
run('native-sqrt-fast-stage',[sys.executable,PORT/'tools/fix_sqrt_fast_path.py'])
run('native-persistent-upload-stage',[sys.executable,PORT/'tools/fix_persistent_upload.py'])
run('native-static-ram-stage',[sys.executable,PORT/'tools/fix_static_ram_base.py'])
run('native-ram-write-watch-stage',[sys.executable,PORT/'tools/fix_ram_write_watch.py'])
# V13 fixed RAM placement is applied by the final native-v13-runtime stage so all
# generated and runtime objects use one layout. NWII_BUILD_V13_STATIC_RAM=0 rolls it back.
run('native-mmu-page-table-stage',[sys.executable,PORT/'tools/fix_mmu_page_table.py'])
run('native-mmu-slow-scalar-stage',[sys.executable,PORT/'tools/fix_mmu_slow_scalar.py'])
run('native-page-zero-reads-stage',[sys.executable,PORT/'tools/fix_page_zero_reads.py'])
run('native-psq-value-helpers-stage',[sys.executable,PORT/'tools/fix_psq_value_helpers.py'])
run('native-renderer-batch-ring-stage',[sys.executable,PORT/'tools/fix_renderer_batch_ring.py'])
run('native-fused-gpu-batch-stage',[sys.executable,PORT/'tools/fix_fused_gpu_batch.py'])
run('native-prune-entry-cases-stage',[sys.executable,PORT/'tools/fix_prune_entry_cases.py'])
run('native-codegen-slim-stage',[sys.executable,PORT/'tools/fix_codegen_slim.py'])
run('native-call-indirect-stage',[sys.executable,PORT/'tools/fix_native_call_indirect.py'])
run('native-guest-runtime-trim-stage',[sys.executable,PORT/'tools/fix_guest_runtime_trim.py'])
run('native-audio-worker-stage',[sys.executable,PORT/'tools/fix_audio_worker.py'])
run('native-early-gpu-signal-stage',[sys.executable,PORT/'tools/fix_early_gpu_signal.py'])
run('native-fifo-ok-flag-stage',[sys.executable,PORT/'tools/fix_fifo_ok_flag.py'])
run('native-present-metrics-stage',[sys.executable,PORT/'tools/fix_present_metrics.py'])
run('native-efb-copy-guard-stage',[sys.executable,PORT/'tools/fix_efb_copy_guard.py'])
run('native-gamepad-pointer-stage',[sys.executable,PORT/'tools/fix_gamepad_pointer.py'])
run('native-state-census-stage',[sys.executable,PORT/'tools/fix_state_census.py'])
run('native-dev-hud-stage',[sys.executable,PORT/'tools/fix_dev_hud.py'])
run('native-gx-work-headers-stage',[sys.executable,PORT/'tools/fix_gx_work_metrics.py','--headers-only'])
run('native-ind-debug-stage',[sys.executable,PORT/'tools/fix_ind_debug.py'])
run('native-user-options-stage',[sys.executable,PORT/'tools/fix_user_options.py'])
run('native-input-map-stage',[sys.executable,PORT/'tools/fix_input_mapping.py'])
run('native-normal-scale-stage',[sys.executable,PORT/'tools/fix_normal_scale.py'])
run('native-texture-mipmaps-stage',[sys.executable,PORT/'tools/fix_texture_mipmaps.py'])
run('native-used-texture-binding-stage',[sys.executable,PORT/'tools/fix_used_texture_binding.py'])
run('native-inline-gpu-validation-stage',[sys.executable,PORT/'tools/fix_inline_gpu_validation.py'])
run('native-hot-path-detail-stage',[sys.executable,PORT/'tools/fix_hot_path_detail.py'])
run('native-packed-vertices-stage',[sys.executable,PORT/'tools/fix_packed_vertices.py'])
run('native-retained-geometry-stage',[sys.executable,PORT/'tools/fix_retained_geometry.py'])
run('native-runtime-stutters-stage',[sys.executable,PORT/'tools/fix_runtime_stutters.py'])
run('native-stutter-watch-stage',[sys.executable,PORT/'tools/fix_stutter_watch.py'])
run('native-shader-data-stage',[sys.executable,PORT/'tools/fix_shader_data_uniforms.py'])
run('native-shader-cache-io-stage',[sys.executable,PORT/'tools/fix_shader_cache_io.py'])
run('native-wait-details-stage',[sys.executable,PORT/'tools/fix_wait_details.py'])
run('native-guest-regions-stage',[sys.executable,PORT/'tools/fix_guest_regions.py'])
run('native-lowering-stage',[sys.executable,PORT/'tools/fix_native_lowering.py'])
run('native-frame-limit-stage',[sys.executable,PORT/'tools/fix_frame_limit.py'])
run('native-drain-protocol-stage',[sys.executable,PORT/'tools/fix_drain_protocol.py'])
run('native-decode-scheduling-stage',[sys.executable,PORT/'tools/fix_decode_scheduling.py'])
run('native-guest-psq-pairs-stage',[sys.executable,PORT/'tools/fix_guest_psq_pairs.py'])
run('native-quant-pair-stores-stage',[sys.executable,PORT/'tools/fix_quant_pair_stores.py'])
run('native-psq-instruction-inline-stage',[sys.executable,PORT/'tools/fix_psq_instruction_inline.py'])
run('native-guest-dispatch-overhead-stage',[sys.executable,PORT/'tools/fix_guest_dispatch_overhead.py'])
run('native-command-reuse-stage',[sys.executable,PORT/'tools/fix_command_reuse.py'])
run('native-empty-publish-stage',[sys.executable,PORT/'tools/fix_poll_empty_publish.py'])
run('native-callback-log-stage',[sys.executable,PORT/'tools/fix_callback_logging.py'])
run('native-fifo-wide-stage',[sys.executable,PORT/'tools/fix_fifo_wide_flag.py'])
run('native-fifo-srwlock-stage',[sys.executable,PORT/'tools/fix_fifo_mutex_srwlock.py'])
run('native-callback-diagnostics-outline-stage',[sys.executable,PORT/'tools/fix_callback_diagnostics_outline.py'])
run('native-event-advance-fast-stage',[sys.executable,PORT/'tools/fix_event_scheduler_fastpath.py'])
run('native-ios-bulk-transfer-stage',[sys.executable,PORT/'tools/fix_ios_bulk_transfer.py'])
run('native-disc-region-index-stage',[sys.executable,PORT/'tools/fix_virtual_disc_region_index.py'])
run('native-timebase-scaling-stage',[sys.executable,PORT/'tools/fix_timebase_scaling.py'])
run('native-dispatch-cache-packing-stage',[sys.executable,PORT/'tools/fix_dispatch_cache_packing.py'])
run('native-dispatch-hot-inline-stage',[sys.executable,PORT/'tools/fix_dispatch_hot_inline.py'])
run('native-fill-blocks-stage',[sys.executable,PORT/'tools/fix_guest_fill_blocks.py'])
run('native-poll-guards-stage',[sys.executable,PORT/'tools/fix_poll_guard_outline.py'])
run('native-xf-generation-stage',[sys.executable,PORT/'tools/fix_render_generation_gates.py'])
run('native-parse-work-reuse-stage',[sys.executable,PORT/'tools/fix_parse_work_reuse.py'])
run('native-dl-hash-interleave-stage',[sys.executable,PORT/'tools/fix_dl_hash_interleave.py'])
run('native-decode-work-reuse-stage',[sys.executable,PORT/'tools/fix_decode_work_reuse.py'])
run('native-draw-submission-reuse-stage',[sys.executable,PORT/'tools/fix_draw_submission_reuse.py'])
run('native-texture-shadow-stage',[sys.executable,PORT/'tools/fix_texture_binding_shadow.py'])
run('native-gx-work-metrics-stage',[sys.executable,PORT/'tools/fix_gx_work_metrics.py'])
run('native-generic-position-stage',[sys.executable,PORT/'tools/fix_generic_position_loader.py'])
run('native-generic-retention-stage',[sys.executable,PORT/'tools/fix_generic_retention.py'])
run('native-interp-histogram-gate-stage',[sys.executable,PORT/'tools/fix_interp_histogram_gate.py'])
run('native-geometry-watch-stage',[sys.executable,PORT/'tools/fix_unified_dirty_tracking.py'])
run('native-state-epochs-stage',[sys.executable,PORT/'tools/fix_state_epochs.py'])
run('native-flush-census-stage',[sys.executable,PORT/'tools/fix_flush_reason_census.py'])
run('native-gx-architecture-metrics-stage',[sys.executable,PORT/'tools/fix_gx_architecture_metrics.py'])
run('native-hot-split-stage',[sys.executable,PORT/'tools/fix_hot_split.py'])
run('native-multiple-word-spans-stage',[sys.executable,PORT/'tools/fix_multiple_word_spans.py'])
run('native-normalized-entry-switch-stage',[sys.executable,PORT/'tools/fix_normalized_entry_switch.py'])
run('native-perf-census-stage',[sys.executable,PORT/'tools/fix_perf_census.py'])
run('native-chunk-size-flags-stage',[sys.executable,PORT/'tools/fix_chunk_size_flags.py'])
run('native-movbe-flags-stage',[sys.executable,PORT/'tools/fix_movbe_flags.py'])
run('native-adaptive-gx-batching-stage',[sys.executable,PORT/'tools/fix_adaptive_gx_batching.py'])
run('native-direct-fifo-stage',[sys.executable,PORT/'tools/fix_direct_fifo.py'])
run('native-xf-stream-reuse-stage',[sys.executable,PORT/'tools/fix_xf_stream_reuse.py'])
run('native-cp-derived-stage',[sys.executable,PORT/'tools/fix_cp_derived_skip.py'])
run('native-gl-program-shadow-stage',[sys.executable,PORT/'tools/fix_gl_program_shadow.py'])
run('native-cache-bounds-stage',[sys.executable,PORT/'tools/fix_cache_bounds.py'])
# NATIVE_SHADER_PRELOAD_V1: render-side program-binary loads. Idempotent (marker-restorable); NWII_NO_SHADER_PRELOAD=1 switches it off.
run('native-shader-preload-stage',[sys.executable,PORT/'tools/fix_shader_preload.py'])
run('native-poll-periodic-check-stage',[sys.executable,PORT/'tools/fix_poll_periodic_check.py'])
run('native-mmu-watch-span-stage',[sys.executable,PORT/'tools/fix_mmu_watch_span.py'])
run('native-hot-diag-gates-stage',[sys.executable,PORT/'tools/fix_hot_diag_gates.py'])
run('native-dispatch-hot-trim-stage',[sys.executable,PORT/'tools/fix_dispatch_hot_trim.py'])
run('native-dispatch-hot-path-stage',[sys.executable,PORT/'tools/fix_dispatch_hot_path.py'])
run('native-gather-guard-outline-stage',[sys.executable,PORT/'tools/fix_gather_guard_outline.py'])
run('native-psq-f32-pair-stage',[sys.executable,PORT/'tools/fix_psq_pair_f32.py'])
run('native-psq-f32-store-pair-stage',[sys.executable,PORT/'tools/fix_psq_store_pair.py'])
run('native-mmu-flat-ram-stage',[sys.executable,PORT/'tools/fix_mmu_flat_ram.py'])
run('native-timebase-cache-stage',[sys.executable,PORT/'tools/fix_timebase_cache.py'])
run('native-misc-hot-paths-stage',[sys.executable,PORT/'tools/fix_misc_hot_paths.py'])
run('native-render-pacing-stage',[sys.executable,PORT/'tools/fix_render_pacing.py'])
# NATIVE_RENDER_LOOP_SPIKES_V1: joystick-thread hint, GX drain served before event poll and present. Runtime opt-outs:
# NWII_NO_JOYSTICK_THREAD=1, NWII_NO_DRAIN_BEFORE_HOST=1 (texture eviction budget: NWII_NO_TEXTURE_EVICT_BUDGET=1).
run('native-render-loop-spikes-stage',[sys.executable,PORT/'tools/fix_render_loop_spikes.py'])
# NATIVE_LLVM_IR_V1: hybrid backend stage (no-op unless --backend=llvm|hybrid). Runs last so it
# sees the final chunk text and CMakeLists; skips cleanly when clang is unavailable.
run('native-llvm-ir-backend-stage',[sys.executable,PORT/'tools/fix_llvm_ir_backend.py'])
run('native-indirect-cache-stage',[sys.executable,PORT/'tools/fix_indirect_call_cache.py'])
# NATIVE_VERTEX_EMIT_V1: guarded register-local kernel for the hot descriptor-driven GX vertex emitter
# (func_803143EC). Runs last so it sees the final hot-unit text; NWII_BUILD_VERTEX_EMIT=0 rolls it back.
run('native-vertex-emit-stage',[sys.executable,PORT/'tools/vertex_emit_kernel.py'])
# The hot split moves whole functions into hot units with their own prelude, so a
# rewritten lowering call site can land in a unit that never saw the overlay
# includes; this stage adds them wherever a native lowering is called.
# Idempotent, with its own --restore.
# NATIVE_EVENT_MASK_V1 / NATIVE_GX_PUBLISH_GATE_V1 / NATIVE_DISPATCH_ABI16_V1 /
# NATIVE_GATHER_REDIRECT_CACHE_V1 / NATIVE_CLOCK_CACHE_V1: guest-thread native-runtime
# trims. Each runs last so it sees the final staged text of the stage it builds on.
# NATIVE_EVENT_MASK_V1: the stage still RUNS (it installs runtime/native_event_mask.h, which the
# idle_wait overlay includes unconditionally) but its poll edits are off by default -- see the
# default in fix_event_mask.py.  NWII_BUILD_EVENT_MASK=1 opts the poll rewrite back in.
run('native-lowering-includes-stage',[sys.executable,PORT/'tools/fix_native_lowering_includes.py'])
run('native-event-mask-stage',[sys.executable,PORT/'tools/fix_event_mask.py'])
# NATIVE_PUBLISH_GATE_V1 stays disabled: it is SUPERSEDED, not merely non-re-entrant. The
# `static std::vector<uint8_t> g_cpu_fifo` it moves into runtime/native_gx_publish.h was replaced by
# NATIVE_CPU_FIFO_FIXED_V1's fixed NativeCpuFifo, and NATIVE_EMPTY_PUBLISH_CALL_V1 already rewrote the
# poll call to `if (*g_native_gx_staged_size) native_gx_publish();` - the poll path pays the emptiness
# test and the publish call is only made when the buffer is non-empty, which is all this stage wanted.
# apply() now recognises that form and reports "superseded" instead of asserting, so the stage cannot
# break a build if it is re-enabled; re-pointing it at the current text would mean moving NativeCpuFifo
# into the header for no measurable gain.
run('native-dispatch-abi-stage',[sys.executable,PORT/'tools/fix_dispatch_abi.py'])
run('native-gather-cache-stage',[sys.executable,PORT/'tools/fix_gather_redirect_cache.py'])
# NATIVE_CLOCK_CACHE_V1 disabled 2026-10-16: a frozen time source can make a wait never complete
# (the AI clock freeze is demonstrated).  NWII_BUILD_CLOCK_CACHE=1 opts in.
# run('native-clock-cache-stage',[sys.executable,PORT/'tools/fix_clock_cache.py'])
# NATIVE_POLL_RELOAD_ELISION_V1: the promoted-register reload at an ordinary poll site is dead -
# a false return from process_pending_callbacks provably leaves every promoted register unchanged
# (tools/check_poll_reload_elision.py re-audits that obligation). Runs after every stage that emits
# promoted text, so it sees the hot units and the vertex-emit kernel;
# NWII_BUILD_POLL_RELOAD_ELISION=0 rolls it back.
run('native-poll-reload-elision-stage',[sys.executable,PORT/'tools/fix_poll_reload_elision.py'])
# NATIVE_WPAD_COOP_V1 / NATIVE_SAVESTATE_V1: four emulated Wii Remotes (co-op) and save states (F1, NWII_LOAD_STATE).
# Both run last: they edit the final text of ios.cpp, gx.cpp, main.cpp, the device modules and the generated run_game loop.
run('native-wpad-coop-stage',[sys.executable,PORT/'tools/fix_wpad_coop.py'])
run('native-savestate-stage',[sys.executable,PORT/'tools/fix_savestate.py'])
run('native-live-prompts-stage',[sys.executable,PORT/'tools/fix_live_prompts.py'])
run('native-heavy-scene-stage',[sys.executable,PORT/'tools/fix_heavy_scene.py'])
run('native-v13-runtime-stage',[sys.executable,PORT/'tools/fix_v13_runtime.py'])
run('native-watch-fold-stage',[sys.executable,PORT/'tools/fix_watch_fold.py'])  # NATIVE_WATCH_FOLD_V1 (NWII_BUILD_WATCH_FOLD=0 disables)
run('native-audit-speedups-stage',[sys.executable,PORT/'tools/fix_audit_speedups.py'])
run('native-draw-followups-stage',[sys.executable,PORT/'tools/fix_draw_followups.py'])
# NATIVE_GL_STATE_REDUNDANCY_V1: skips redundant GL_ARRAY_BUFFER / GL_ELEMENT_ARRAY_BUFFER binds in the batch helpers and
# resets the memos on retained-geometry eviction. Anchors on draw-followups text, so it runs after that stage. NWII_NO_GL_STATE_REDUNDANCY=1 opts out.
run('native-gl-state-redundancy-stage',[sys.executable,PORT/'tools/fix_gl_state_redundancy.py'])
# Runs after the audit / draw-followup stages: it anchors on the TLUT epoch include they add to texture_cache_gl.cpp.
run('native-texture-hash-cache-stage',[sys.executable,PORT/'tools/fix_texture_hash_cache.py'])
# NATIVE_TEXTURE_VALIDATION_V1: must run after the hash cache (it anchors on that text). NWII_NO_TEXTURE_VALIDATION_FIX=1 opts out.
run('native-texture-validation-stage',[sys.executable,PORT/'tools/fix_texture_validation.py'])
run('native-guest-batch-stage',[sys.executable,PORT/'tools/fix_guest_batch.py'])
# NATIVE_MEM_PROOF_V1: region-hoisted address proofs over the final hot text (NWII_BUILD_MEM_PROOFS=0 disables).
run('native-mem-proofs-stage',[sys.executable,PORT/'tools/fix_guest_mem_proofs.py'])
run('native-camera-glide-stage',[sys.executable,PORT/'tools/fix_camera_glide.py'])
run('native-guest-arena-stage',[sys.executable,PORT/'tools/fix_guest_arena.py'])  # NATIVE_GUEST_ARENA_V1: on by default (dynamic layout); NWII_BUILD_GUEST_ARENA=0 opts out, NWII_BUILD_GUEST_ARENA_MODE=dynamic|fixed|mask
# NATIVE_POLL_SPIN_OUTLINE_V1: poll-site "Spinning at PC" print -> one cold call (hot units; NWII_BUILD_POLL_SPIN_OUTLINE=0 disables).
run('native-poll-spin-outline-stage',[sys.executable,PORT/'tools/fix_poll_spin_outline.py'])
# NATIVE_HOT_PGO_V1: on by default (NWII_BUILD_HOT_PGO=shipped): hot units whose text matches the committed data/pgo profile are compiled with it; anything else, or any problem, falls back silently to the normal flags. NWII_BUILD_HOT_PGO=off opts out.
run('native-hot-pgo-stage',[sys.executable,PORT/'tools/fix_hot_pgo.py'])
# NATIVE_TRACKER_HOST_WRITES_V1: host stores into guest RAM (AR DMA, write-gather, bulk helpers, disc reads) run under
# native_arena::HostWriteScope so the write tracker and sticky pages see them. Runs last; no-op with NWII_BUILD_GUEST_ARENA=0.
run('native-tracker-host-writes-stage',[sys.executable,PORT/'tools/fix_tracker_host_writes.py'])
# NATIVE_FALLBACK_CENSUS_V1: F3/F4 counters for generic fallback draws (posType, posMatIdx), scalar AoS draws, texture tracker answers and memcmp bytes.
run('native-fallback-census-stage',[sys.executable,PORT/'tools/fix_fallback_census.py'])
# NATIVE_TEXTURE_DECODE_FAST_V1: pointer decoders, CMPR->DXT1 upload, GL name reuse on content change. NWII_NO_FAST_TEXTURE_DECODE=1,
# NWII_NO_CMPR_PASSTHROUGH=1 opt out.
run('native-texture-decode-fast-stage',[sys.executable,PORT/'tools/fix_texture_decode_fast.py'])
# NATIVE_TEXTURE_VALIDATION_MEMO_V1: skip unchanged() for entries verified clean by the tracker. NWII_NO_TEXTURE_VALIDATION_MEMO=1 opts out.
run('native-texture-validation-memo-stage',[sys.executable,PORT/'tools/fix_texture_validation_memo.py'])
# NATIVE_TEXTURE_VALIDATION_MEMO_V1 is per-page now; the EFB snapshot shares it. Runs after the memo (anchors on its query line).
run('native-texture-attribution-stage',[sys.executable,PORT/'tools/fix_texture_attribution.py'])  # NATIVE_TEXTURE_ATTRIBUTION_V1: lookup scopes, pages armed, batched eviction deletes
# NATIVE_FALLBACK_REASONS_V1: generic fallback draws count the packed-decode gates they fail (F3 fallback_gate_* rows).
run('native-fallback-gates-stage',[sys.executable,PORT/'tools/fix_fallback_gates.py'])
# NATIVE_DIRECT_TEXCOORD_LOADER_V1: direct F32 position/normal with one indexed F32 texcoord set picks its readers at compile time.
# Runs last among the fifo_parser stages; harness: tools/check_direct_texcoord_loader.py, byte oracle: check_format_loaders.py.
run('native-direct-texcoord-stage',[sys.executable,PORT/'tools/fix_direct_texcoord_loader.py'])
if os.environ.get('NWII_BUILD_RENDER_STATE_SPLIT')=='1':  # opt-in (default off)
    run('native-renderer-state-split-stage',[sys.executable,PORT/'tools/fix_renderer_state_split.py'])  # NATIVE_RENDERER_STATE_SPLIT_V1: renderer-owned GX state (output-neutral; harness: tools/check_renderer_state_split.py)
    # NATIVE_TEXTURE_CPU_GL_SPLIT_V1: texture lookup/validation/decode before the early PE signal; GL upload/bind in Render.
    # Needs the state split (it uses RenderState()). Reversible; harness: tools/check_texture_cpu_gl_split.py.
    run('native-texture-cpu-gl-split-stage',[sys.executable,PORT/'tools/fix_texture_cpu_gl_split.py'])
    # NATIVE_RENDER_QUEUE_V1: GL thread and batch queue (NWII_NO_RENDER_QUEUE=1 at runtime = the synchronous path). Needs the texture split.
    run('native-render-queue-stage',[sys.executable,PORT/'tools/fix_render_queue.py'])
run('native-mtime-restore',[sys.executable,PORT/'tools/preserve_mtimes.py','restore'])
if not args.stage_only:
    from build_resources import compile_jobs
    jobs=compile_jobs(args.jobs)
    print(f'Compiler workers: {jobs} (requested maximum {args.jobs}; available-memory budget).',flush=True)
    run('native-game-build',['cmake','--build',build,'--target','CrashNative','-j',jobs])
    print('Executable:',build/'CrashNative.exe')
else:
    print('Staged sources:',src)
