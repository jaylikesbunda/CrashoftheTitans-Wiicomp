"""Shared identity for the patches required by the launcher build from disc."""
from pathlib import Path
import hashlib,json
REVISION='2026.10.23-10'
REQUIRED=(
 'tools/fix_guest_batch.py','tools/guest_batch_lowering.py','tools/guest_typed_leaves.py','tools/check_guest_batch.py','runtime-overlays/native_guest_batch.h',
 # NATIVE_MEM_PROOF_V1: region-hoisted address proofs over the promoted hot units.
 'tools/fix_guest_mem_proofs.py','tools/guest_mem_proofs.py','tools/check_guest_mem_proofs.py','runtime-overlays/native_mem_proof.h',
 # NATIVE_POLL_CONTINUATIONS_V1: poll-site and call-return continuation entries for the measured resume set.
 'tools/guest_poll_continuations.py','tools/check_guest_poll_continuations.py','reference/guest-regions/poll-continuation-targets.json',
 # NATIVE_SCHED_RECORDER_OPT_IN_V2: default scheduler watch only with diagnostics; refreshed hot-function list.
 'tools/fix_sched_recorder.py','tools/check_sched_recorder_opt_in.py','tools/hot_functions.txt',
 # NATIVE_CAMERA_GLIDE_V1: build-time camera glide stage; runtime inert unless NWII_CAMERA_GLIDE_MS is set.
 'tools/fix_camera_glide.py','tools/check_camera_glide.py','runtime-overlays/native_camera_glide.h',
 'tools/fix_draw_followups.py','tools/check_draw_followups.py','runtime-overlays/native_viewport_shadow.h','runtime-overlays/native_uniform_epoch.h',
 'tools/fix_audit_speedups.py','tools/check_audit_speedups.py','tools/check_packed_upload.py','tools/check_packed_layout.py','tools/check_retained_gpu.py','runtime-overlays/native_tlut_epoch.h',
 'tools/fix_heavy_scene.py','tools/check_heavy_scene.py','runtime-overlays/native_palette_decode.h','runtime-overlays/native_batch_boundaries.h',
 'tools/check_v13_codegen.py',
 'tools/v13_codegen.py','tools/v13_headers.py','tools/guest_write_sets.py','tools/ppc_effects.py','runtime-overlays/native_guest_fast.h','runtime-overlays/native_launch_flags.h',
 'tools/fix_v13_runtime.py','tools/check_v13_runtime.py','runtime-overlays/native_guest_abi.h','runtime-overlays/native_handoff.h',
 'tools/fix_protected_fastmem.py','tools/check_protected_fastmem.py','runtime-overlays/native_fastmem.h','runtime-overlays/native_fastmem_allocator.h',
 'tools/check_indirect_call_cache.py',
 'tools/check_savestate_stage.py',
 'tools/fix_live_prompts.py','runtime-overlays/native_prompt_device.h','runtime-overlays/native_prompt_textures.h',
 'tools/check_live_prompts.py','tools/live_prompt_tests.cpp',
 'runtime-overlays/native_prompt_slots.h','tools/check_prompt_slots.py','tools/prompt_slots_tests.cpp',
 'tools/check_input_devices.py','tools/input_devices_tests.cpp',
 'tools/fix_input_mapping.py','runtime-overlays/input_mapping.h',
 'tools/build_native.py','tools/clean_room_build.py','tools/builder_revision.py',
 'tools/build_resources.py',
 'tools/fix_parse_work_reuse.py','tools/fix_decode_work_reuse.py','tools/fix_draw_submission_reuse.py',
 'tools/fix_texture_binding_shadow.py','runtime-overlays/native_gl_texture_shadow.h',
 'tools/fix_gx_work_metrics.py','runtime-overlays/native_gx_work_metrics.h',
 'tools/fix_state_epochs.py','tools/fix_unified_dirty_tracking.py','runtime-overlays/native_geometry_watch.h',
 'tools/fix_generic_position_loader.py','tools/fix_flush_reason_census.py','runtime-overlays/native_flush_census.h',
 'tools/fix_gx_architecture_metrics.py',
 'tools/fix_direct_fifo.py','tools/direct_fifo/files/command_stream.h',
 'tools/fix_adaptive_gx_batching.py','runtime-overlays/native_gx_batching.h',
 'tools/fix_xf_stream_reuse.py','runtime-overlays/native_xf_stream_shadow.h',
 'tools/fix_used_texture_binding.py','runtime-overlays/used_texture_mask.inc',
 'tools/fix_inline_gpu_validation.py','tools/fix_hot_path_detail.py',
 'tools/fix_dev_hud.py','runtime-overlays/native_hud_gl.h',
 'tools/fix_user_options.py',
 'tools/fix_packed_vertices.py','tools/packed_decode_codegen.py',
 'runtime-overlays/native_packed_buffer.h',
 'tools/fix_retained_geometry.py','tools/specialized_decode_codegen.py',
 'runtime-overlays/retained_decode.inc','runtime-overlays/retained_gpu.inc',
 'runtime-overlays/observed_vertex_formats.inc',
 'runtime-overlays/native_geometry_stats.h','tools/fix_runtime_stutters.py',
 'tools/fix_stutter_watch.py','runtime-overlays/native_stutter.h',
 'runtime-overlays/native_hud.h','tools/fix_frame_trace.py',
 'tools/fix_fused_gpu_batch.py','tools/fix_normal_scale.py',
 'tools/block_codegen.py','tools/fix_bounded_blocks.py',
 'tools/fix_gap_functions.py','tools/fix_fallthrough.py','tools/setup_full_build.py','tools/fix_dispatch_switch.py',
 'reference/backend-reports/bounded-block-candidates.json.gz',
 'reference/backend-reports/bounded-block-qualification.json.gz',
 'tools/fix_shader_data_uniforms.py','runtime-overlays/native_shader_data.h',
 'tools/fix_fog.py','runtime-overlays/fog_codegen.inc',
 'tools/fix_shader_cache_io.py','runtime-overlays/native_shader_cache_io.h',
 'tools/fix_wait_details.py','tools/fix_guest_idle_wait.py','runtime-overlays/idle_wait.h',
 'compiler/guest_leaf_ir.py','tools/guest_region_sources.py','tools/fix_guest_regions.py',
 'reference/guest-regions/register-chains.json','runtime-overlays/native_guest_region_stats.h',
 'tools/restore_guest_regions.py',
 'tools/fix_drain_protocol.py','runtime-overlays/native_drain_signal.h',
 'tools/fix_render_pacing.py','runtime-overlays/native_render_wait.h',
 'tools/check_render_pacing.py','tools/render_pacing_tests.cpp','tools/analyze_render_pacing.py',
 'tools/fix_decode_scheduling.py','runtime-overlays/native_decode_tasks.h',
 'runtime-overlays/native_pipeline_stats.h','runtime-overlays/chunked_decode.inc',
 'tools/guest_callee_effects.py','tools/effect_statements.py','tools/prepare_callee_effects.py',
 'tools/promote_regs.py','tools/fix_hot_split.py','reference/guest-regions/effect-exclusions.json',
 'tools/fix_frame_limit.py','runtime-overlays/native_frame_limit.h','tools/check_frame_limit.py',
 'tools/fix_ps5_controller.py','tools/fix_gamepad_pointer.py',
 'tools/guest_entry_specialize.py','runtime-overlays/native_guest_entry.h',
 'tools/guest_linked_region.py','tools/guest_bounded_inline.py','runtime-overlays/native_linked_region.h','reference/guest-regions/linked-regions.json',
 'tools/guest_loop_guards.py','runtime-overlays/native_loop_span.h','reference/guest-regions/loop-fastpaths.json',
 'tools/fix_mmu_slow_scalar.py',
 'tools/fix_page_zero_reads.py','tools/fix_poll_empty_publish.py',
 'tools/fix_callback_logging.py',
 'tools/fix_fifo_wide_flag.py',
 'tools/fix_callback_diagnostics_outline.py','tools/fix_event_scheduler_fastpath.py',
 'tools/fix_perf_census.py','runtime-overlays/native_perf_census.h','runtime-overlays/native_perf_census_export.h',
 'tools/fix_quant_pair_stores.py','tools/fix_normalized_entry_switch.py',
 'tools/fix_multiple_word_spans.py','runtime-overlays/native_multiple_words.h',
 'tools/fix_guest_fill_blocks.py','runtime-overlays/native_guest_fill_block.h',
 'tools/fix_poll_guard_outline.py','tools/fix_render_generation_gates.py','tools/fix_dcbz_zero.py',
 'tools/fix_ios_bulk_transfer.py','runtime-overlays/native_guest_bytes.h',
 'tools/fix_virtual_disc_region_index.py','tools/fix_timebase_scaling.py',
 'tools/fix_dispatch_cache_packing.py','tools/fix_dispatch_hot_inline.py',
 'tools/fix_guest_psq_pairs.py','tools/fix_psq_instruction_inline.py','tools/fix_guest_dispatch_overhead.py',
 'tools/fix_native_call_indirect.py','tools/fix_wpad_emulation.py',
 'tools/lazy_fpu_header.py','tools/fix_lazy_fpu.py',
 'tools/expand_hot_closure.py','reference/guest-regions/hot-continuations.json',
 'tools/fix_command_reuse.py','runtime-overlays/command_reuse.inc',
 'tools/fix_cp_derived_skip.py',
 'tools/fix_gl_program_shadow.py','runtime-overlays/native_gl_bind_shadow.h',
 # NATIVE_CACHE_BOUNDS_V1: bounded shader/uniform/texture caches, top-N census rows, cache occupancy in F3/F4.
 # NATIVE_RENDER_LOOP_SPIKES_V1: joystick-thread hint and GX drain before event poll / present.
 'tools/fix_render_loop_spikes.py','tools/check_render_loop_spikes.py',
 'tools/fix_cache_bounds.py','tools/check_cache_bounds.py','tools/cache_bounds_tests.cpp','tools/pc_rows_tests.cpp','runtime-overlays/native_cache_bounds.h',
 # NATIVE_SHADER_PRELOAD_V1: program binaries read off the render thread, linked in a bounded pump (2 ms, 6 ms when idle).
 'tools/fix_shader_preload.py','tools/check_shader_preload.py','runtime-overlays/native_shader_preload.h',
 # NATIVE_TEXTURE_HASH_CACHE_V1: texture validation skips the byte compare when the write tracker proves the source unchanged.
 'tools/fix_texture_hash_cache.py','tools/check_texture_hash_cache.py','tools/texture_hash_cache_tests.cpp','runtime-overlays/native_texture_hash_cache.h',
 # NATIVE_WATCH_FOLD_V1: guest-store watch checks folded out of the shipped exe (NWII_BUILD_WATCH_FOLD=0 disables).
 'tools/fix_watch_fold.py','tools/check_watch_fold.py','runtime-overlays/native_watch_fold.h',
 'tools/fix_poll_periodic_check.py','tools/fix_mmu_watch_span.py','tools/fix_hot_diag_gates.py',
 'tools/fix_fifo_mutex_srwlock.py','runtime-overlays/native_fifo_lock.h',
 'tools/fix_dl_hash_interleave.py',
 'tools/fix_mmu_flat_ram.py',
 'tools/fix_psq_pair_f32.py','tools/fix_psq_store_pair.py',
 'tools/fix_timebase_cache.py','tools/fix_gather_guard_outline.py','tools/fix_dispatch_hot_trim.py','tools/fix_dispatch_hot_path.py','tools/fix_misc_hot_paths.py',
 'tools/fix_generic_retention.py','tools/fix_interp_histogram_gate.py','runtime-overlays/frame_trace.h',
 'tools/fix_shared_draw_bytes.py','runtime-overlays/shared_draw_bytes.inc',
 'tools/fix_hot_path_detail.py',
 'tools/fix_movbe_flags.py','tools/fix_div64_fast.py',
 'tools/fix_indirect_call_cache.py','tools/check_indirect_call_cache.py','tools/check_return_cache.py',
 # NATIVE_VERTEX_EMIT_V1: guarded register-local kernel for the hot descriptor-driven GX vertex emitter.
 'tools/vertex_emit_kernel.py','tools/check_vertex_emit_kernel.py',
 # NATIVE_LLVM_IR_V1: hybrid LLVM-IR backend (clang-compiled slice beside the C++ oracle).
 'tools/fix_llvm_ir_backend.py','compiler/emit_llvm_ir.py',
 'runtime-overlays/llvm_ir_support.cpp',
 'reference/backend-reports/llvm-ir-functions.json',
 # NATIVE_FASTMEM_STATIC_BASE_V1: fixed-address guest RAM (fastmem stage 1).
 'tools/fix_fastmem_static_base.py','tools/check_fastmem_static_base.py',
 'runtime-overlays/static_ram_base.cpp',
 'tools/bench_guest_ram_fast.py','tools/run_guest_pass_checks.py',

 # NATIVE_SOURCE_LOWERING_V1: source-backed native lowering of translated guest functions.
  # The stage keeps every translated body reachable, so a lowering is a pure addition.
 'tools/native_lowering.py','tools/fix_native_lowering.py','tools/fix_native_lowering_includes.py','tools/check_native_lowering.py',
 'tools/native_lowering_harness.h','tools/native_lower_math_tests.cpp','tools/native_lower_leaf_tests.cpp',
  'tools/native_lowering_effects.py','tools/check_native_lowering_fixtures.py',
 'tools/native_lower_gx_tests.cpp',
  'tools/native_lower_vertex_tests.cpp',
  'tools/native_lower_vector_tests.cpp',
  'tools/native_lower_inferred_tests.cpp',
 'runtime-overlays/native_lower_support.h','runtime-overlays/native_lower_math.h',
 'runtime-overlays/native_lower_leaf.h','runtime-overlays/native_lower_gx.h',
  'runtime-overlays/native_lower_vertex.h',
  'runtime-overlays/native_lower_vector.h',
  'runtime-overlays/native_lower_inferred.h',
 'data/native-lowering-candidates.json','data/native-lowering-inventory.json','data/native-lowering-priorities.json',

 # NATIVE_EVENT_MASK_V1: one cache-line producer mask for the poll fast path.
 'tools/fix_event_mask.py','runtime-overlays/native_event_mask.h','tools/check_event_mask.py',
 'tools/check_poll_periodic_check.py',
 # NATIVE_GX_PUBLISH_GATE_V1: inline the staging-buffer emptiness test on the poll path.
 'tools/fix_publish_path.py','runtime-overlays/native_gx_publish.h',
 # NATIVE_DISPATCH_ABI16_V1: 16-byte dispatch choice returned in registers.
 'tools/fix_dispatch_abi.py',
 # NATIVE_GATHER_REDIRECT_CACHE_V1: classify the write-gather destination once per config.
 'tools/fix_gather_redirect_cache.py',
 # NATIVE_CLOCK_CACHE_V1: guest-instruction guard for the AI microsecond clock.
 'tools/fix_clock_cache.py',

 # NATIVE_POLL_RELOAD_ELISION_V1: drop the dead promoted-register reload at ordinary poll sites.
 # The oracle re-audits the proof obligation (a false return from process_pending_callbacks leaves
 # every promoted register unchanged) and the two barrier-census tools price the population.
 'tools/fix_poll_reload_elision.py','tools/check_poll_reload_elision.py',
 'tools/barrier_census.py','tools/blockcall_effect_census.py',

 # NATIVE_WPAD_COOP_V1 / NATIVE_SAVESTATE_V1: co-op Wii Remotes, per-controller maps and save states.
 'tools/fix_wpad_coop.py','tools/fix_savestate.py','runtime-overlays/native_savestate.h',
 'runtime-overlays/native_savestate_ios.inc','runtime-overlays/gamepad_source_mapped.cpp',

 # Standalone layout: default paths, build-time game-code baselines and the evidence tables the stages read.
 'tools/port_paths.py','tools/guest_baselines.py','tools/guest_leaf_kernels.py','tools/fix_native_plain_leaf_family.py',
 'tools/fix_sdk_sqrt_loops.py','tools/fix_dispatcher_optimization.py','tools/fix_o2_translation.py','tools/translation_inventory.py',
 'data/function-index.tsv','data/main.dol.sha256','data/bounded-block-candidates.json','data/bounded-block-qualification.json',
 'data/dispatcher-o2-tests.json','data/guest-plain-edge-tests.json','data/guest-plain-family-tests.json','data/o2-translation-tests.json',
 'data/plain-leaf-family.json','data/plain-leaf-special-fallbacks.json','data/sdk-sqrt-tests.json',
 # Disc releases: the reference (PAL) release is data/; others rebase the stage tree onto their own addresses.
 'tools/region.py','tools/derive_release_map.py',
 'data/regions/RQJE7D/release.json','data/regions/RQJE7D/function-index.tsv','data/regions/RQJE7D/address-map.tsv',

 # NATIVE_GUEST_ARENA_V1: Dolphin-style guest memory arena. Opt-in only (NWII_BUILD_GUEST_ARENA=1); with it unset the stage removes its edits and stages nothing.
 'tools/fix_guest_arena.py','tools/check_guest_arena.py','runtime-overlays/native_guest_arena.h','runtime-overlays/native_guest_arena_fifo.h','runtime-overlays/native_guest_arena.cpp',
 'tools/guest_arena_tests/arena_alias.cpp','tools/guest_arena_tests/arena_decoder.cpp','tools/guest_arena_tests/arena_fault.cpp','tools/guest_arena_tests/arena_misc.cpp','tools/guest_arena_tests/diff_harness.cpp','tools/guest_arena_tests/gen_decoder_variants.py','tools/guest_arena_tests/gen_stubs.py','tools/guest_arena_tests/stubs.h',
 # NATIVE_POLL_SPIN_OUTLINE_V1: poll-site "Spinning at PC" print as one cold call in the promoted hot units.
 'tools/fix_poll_spin_outline.py',
 # NATIVE_HOT_PGO_V1: opt-in profile-guided hot units (NWII_BUILD_HOT_PGO=shipped); falls back silently to the normal flags unless the staged hot-unit text matches the committed profile.
 'tools/fix_hot_pgo.py','runtime-overlays/native_pgo_dump.cpp',
 'data/pgo/RQJP7D/hot-pgo-manifest.json','data/pgo/RQJP7D/hot_000.cpp.gcda','data/pgo/RQJP7D/hot_001.cpp.gcda','data/pgo/RQJP7D/hot_002.cpp.gcda','data/pgo/RQJP7D/hot_003.cpp.gcda','data/pgo/RQJP7D/hot_004.cpp.gcda','data/pgo/RQJP7D/hot_005.cpp.gcda','data/pgo/RQJP7D/hot_006.cpp.gcda','data/pgo/RQJP7D/hot_007.cpp.gcda','data/pgo/RQJP7D/hot_008.cpp.gcda','data/pgo/RQJP7D/hot_009.cpp.gcda','data/pgo/RQJP7D/hot_010.cpp.gcda','data/pgo/RQJP7D/hot_011.cpp.gcda','data/pgo/RQJP7D/hot_012.cpp.gcda','data/pgo/RQJP7D/hot_013.cpp.gcda','data/pgo/RQJP7D/hot_014.cpp.gcda','data/pgo/RQJP7D/hot_015.cpp.gcda','data/pgo/RQJP7D/hot_016.cpp.gcda','data/pgo/RQJP7D/hot_017.cpp.gcda','data/pgo/RQJP7D/hot_018.cpp.gcda','data/pgo/RQJP7D/hot_019.cpp.gcda','data/pgo/RQJP7D/hot_020.cpp.gcda','data/pgo/RQJP7D/hot_021.cpp.gcda','data/pgo/RQJP7D/hot_022.cpp.gcda','data/pgo/RQJP7D/hot_023.cpp.gcda','data/pgo/RQJP7D/hot_024.cpp.gcda','data/pgo/RQJP7D/hot_025.cpp.gcda','data/pgo/RQJP7D/hot_026.cpp.gcda',
 # NATIVE_GL_STATE_REDUNDANCY_V1: redundant array/element buffer binds skipped in the batch helpers; retained eviction resets the memos.
 'tools/fix_gl_state_redundancy.py','tools/check_gl_state_redundancy.py',
 # NATIVE_TEXTURE_VALIDATION_V1: stale hot entries are not re-compared; EFB copies skip the unused sampled hash.
 'tools/fix_texture_validation.py','tools/check_texture_validation.py',
 # NATIVE_TEXTURE_DECODE_FAST_V1: pointer decoders, CMPR passthrough and storage reuse in texture_cache_gl.cpp (differential test).
 'tools/fix_texture_decode_fast.py','tools/check_texture_decode_fast.py','tools/texture_decode_tests.cpp','runtime-overlays/native_texture_decode.h',
 # NATIVE_TEXTURE_VALIDATION_MEMO_V1: per-page write-epoch memo for unchanged() and the EFB snapshot (unit test; page epochs in native_guest_arena.cpp).
 'tools/fix_texture_validation_memo.py','tools/check_texture_validation_memo.py','tools/texture_memo_tests.cpp','runtime-overlays/native_texture_memo.h',
 # NATIVE_TEXTURE_ATTRIBUTION_V1: lookup scopes (track query, memcmp, eviction), pages armed, batched eviction deletes.
 'tools/fix_texture_attribution.py','tools/check_texture_attribution.py',
 # NATIVE_FALLBACK_REASONS_V1: gate bits of the generic fallback path (FallbackGates in native_gx_work_metrics.h).
 'tools/fix_fallback_gates.py','tools/check_fallback_gates.py','tools/fallback_gates_tests.cpp',
 # NATIVE_FALLBACK_CENSUS_V1: measurement call sites for the GX work census (overlay counters in native_gx_work_metrics.h).
 'tools/fix_fallback_census.py','tools/check_fallback_census.py',
 # NATIVE_DIRECT_TEXCOORD_LOADER_V1: direct F32 position/normal with one indexed F32 texcoord picks its readers at compile time.
 'tools/fix_direct_texcoord_loader.py','tools/check_direct_texcoord_loader.py',
 # NATIVE_TRACKER_HOST_WRITES_V1: host stores into guest RAM run under HostWriteScope (arena tracker and sticky pages see them).
 'tools/fix_tracker_host_writes.py','tools/check_tracker_host_writes.py',
# NATIVE_RENDERER_STATE_SPLIT_V1: renderer-owned GX state (RenderState) split; differential harness and check.
 'tools/fix_renderer_state_split.py','tools/check_renderer_state_split.py','tools/render_state_split_harness.cpp','runtime-overlays/render_state.h','runtime-overlays/render_state.cpp',
# NATIVE_TEXTURE_CPU_GL_SPLIT_V1: texture lookup/validation/decode in a CPU prepass before the early PE signal; GL upload/bind queued for Render.
 'tools/fix_texture_cpu_gl_split.py','tools/check_texture_cpu_gl_split.py','runtime-overlays/texture_cpu_gl_split_walk.inc',
# NATIVE_RENDER_QUEUE_V1: GL thread and batch queue (gated with the state split); harness: tools/check_render_queue.py.
 'tools/fix_render_queue.py','tools/check_render_queue.py','tools/render_queue_tests.cpp','runtime-overlays/native_render_queue.h','runtime-overlays/render_queue_glue.inc','runtime-overlays/native_render_queue_stats.h',
)
def identity(root):
 root=Path(root)
 return dict(schema_version=1,revision=REVISION,required_inputs={rel:hashlib.sha256((root/rel).read_bytes()).hexdigest() for rel in REQUIRED})
def digest(identity):
 """One hash of the whole patch set (revision and every input). A runtime records it, so changed inputs need a rebuild even when
 the revision string is unchanged. The launcher computes the same value from its compiled builder-revision.json."""
 return hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def validate(root,expected=None):
 root=Path(root);saved=json.loads((root/'builder-revision.json').read_text())
 if expected and saved.get('revision')!=expected:raise ValueError('Builder patch revision is outdated; refresh the builder pack.')
 if saved!=identity(root):raise ValueError('Builder patch files do not match the declared revision; refresh the builder pack.')
 return saved
if __name__=='__main__':
 import argparse
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--write',action='store_true');args=ap.parse_args()
 if args.write:(args.root/'builder-revision.json').write_bytes((json.dumps(identity(args.root),indent=2)+'\n').encode('utf-8'))
 else:validate(args.root)
 print('Builder patch revision '+REVISION+' verified.')
