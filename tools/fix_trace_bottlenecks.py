"""Keep event semantics while outlining empty polls and planning VAT readers."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
path=rt/'src/hle/ios.cpp';s=path.read_text()
marker='NATIVE_EMPTY_POLL_FASTPATH_V1'
if marker not in s:
    # Baseline is retained for production differential fixtures, never byte fallback.
    (p/'runtime-overlays/callback_fastpath_baseline.cpp').write_text(s)
    old='bool process_pending_callbacks(CPUContext &ctx) {\n  native_trace::CallbackSample trace_callback(ctx.pc,ctx.lr,ctx.inst_count);'
    assert s.count(old)==1
    s=s.replace(old,'static bool native_process_callbacks_slow(CPUContext &ctx, bool poll_clock_due, bool wpad_polled) {',1)
    old='  static uint32_t poll_clock_phase = 0; // NATIVE_POLL_CLOCK_THROTTLE_V1\n  static const bool poll_no_throttle = std::getenv("NWII_NO_POLL_THROTTLE") != nullptr;\n  const bool poll_clock_due = poll_no_throttle || ((++poll_clock_phase) & 15u) == 0;'
    assert s.count(old)==1;s=s.replace(old,'',1)
    anchor='  native_wpad_poll(ctx);\n  if (ctx.pending_cb_count.load(std::memory_order_relaxed) == 0)'
    assert s.count(anchor)==1;s=s.replace(anchor,anchor.replace('  native_wpad_poll(ctx);','  if (!wpad_polled) native_wpad_poll(ctx);'),1)
    anchor='static bool native_process_callbacks_slow('
    wrapper='''// NATIVE_EMPTY_POLL_FASTPATH_V1: no skipped clocks, delay ticks, or queued events.
static bool native_process_callbacks_slow(CPUContext&,bool,bool);
bool process_pending_callbacks(CPUContext &ctx) {
  native_trace::CallbackSample trace_callback(ctx.pc,ctx.lr,ctx.inst_count);
  native_gx_publish();
  static uint32_t poll_clock_phase=0; // NATIVE_POLL_CLOCK_THROTTLE_V1
  static const bool no_throttle=std::getenv("NWII_NO_POLL_THROTTLE")!=nullptr;
  const bool due=no_throttle || ((++poll_clock_phase)&15u)==0;
  static const bool diagnostics=NativeLaunchFlag_NWII_SAMPLE()!=nullptr ||
      std::getenv("NWII_STARTUP_PROFILE")!=nullptr || g_fp_context_probe_enabled;
  if (!diagnostics && !due && !g_trace_after_dec && ctx.pc!=0 && ctx.pc!=0xFFFFFFFCu &&
      ctx.inst_count%20000000!=0 && g_ipc_interrupt_delay==0 && hw::g_di_interrupt_delay==0 &&
      !ctx.vblank_pending && !(hw::pi_intsr.load() & hw::pi_intmr.load()) &&
      ctx.pending_cb_count.load(std::memory_order_relaxed)==0) {
    if (ctx.in_callback || !(ctx.msr&0x8000u) || ctx.dispatch_saved_ctx!=0)return false;
    native_wpad_poll(ctx);
    if(ctx.pending_cb_count.load(std::memory_order_relaxed)==0)return false;
    return native_process_callbacks_slow(ctx,due,true);
  }
  return native_process_callbacks_slow(ctx,due,false);
}
'''
    s=s.replace(anchor,wrapper+anchor,1);path.write_text(s)
# Coalesce identical idle drain notifications; the host consumes the atomic request.
if 'NATIVE_IDLE_NOTIFY_ONCE_V1' not in s:
    old='  // NATIVE_GX_IDLE_DRAIN: guest idle with GX bytes pending -> ask the host loop to drain now.\n  { static const bool idle_drain = std::getenv("NWII_NO_GX_IDLE_DRAIN") == nullptr;\n    if (idle_drain && ctx.pc == 0x803BBFA0u && g_gx_pending_bytes.load(std::memory_order_relaxed) > 0)\n      { g_gx_drain_request.store(true, std::memory_order_release); g_gx_drain_cv.notify_one(); } }'
    new='  // NATIVE_GX_IDLE_DRAIN / NATIVE_IDLE_NOTIFY_ONCE_V1: one wake until consumed.\n  { static const bool idle_drain=std::getenv("NWII_NO_GX_IDLE_DRAIN")==nullptr;\n    if(idle_drain && ctx.pc==0x803BBFA0u && g_gx_pending_bytes.load(std::memory_order_relaxed)>0 &&\n       !g_gx_drain_request.exchange(true,std::memory_order_acq_rel))g_gx_drain_cv.notify_one(); }\n'
    assert s.count(old)==1;s=s.replace(old,'',1)
    anchor='  native_gx_publish();\n  static uint32_t poll_clock_phase=0; // NATIVE_POLL_CLOCK_THROTTLE_V1'
    assert s.count(anchor)==1;s=s.replace(anchor,'  native_gx_publish();\n'+new+'  static uint32_t poll_clock_phase=0; // NATIVE_POLL_CLOCK_THROTTLE_V1',1)
    path.write_text(s)
s=s.replace('static uint32_t poll_clock_phase=0;\n','static uint32_t poll_clock_phase=0; // NATIVE_POLL_CLOCK_THROTTLE_V1\n').replace('// NATIVE_IDLE_NOTIFY_ONCE_V1: request only needs one wake until host consumes it.','// NATIVE_GX_IDLE_DRAIN / NATIVE_IDLE_NOTIFY_ONCE_V1: one wake until consumed.')
s=s.replace('std::getenv("NWII_STARTUP_PROFILE")!=nullptr || g_fp_context_probe_enabled;',
    'std::getenv("NWII_STARTUP_PROFILE")!=nullptr || g_fp_context_probe_enabled ||\n'
    '      std::getenv("NWII_LOOPTRACE")!=nullptr || std::getenv("NWII_PEEK")!=nullptr ||\n'
    '      std::getenv("NWII_RESCHED")!=nullptr;')
path.write_text(s)
# A sampled callback may longjmp into the dispatcher. Close its trace scope first.
if 'NATIVE_TRACE_CALLBACK_ESCAPE_V1' not in s:
    s=s.replace('longjmp(ctx.exception_jmp_buf, 1);','native_trace::close_callback_sample(); /* NATIVE_TRACE_CALLBACK_ESCAPE_V1 */ longjmp(ctx.exception_jmp_buf, 1);')
    path.write_text(s)
if 'NATIVE_CALLBACK_TRACE_BRANCH_V1' not in s:
    old='bool process_pending_callbacks(CPUContext &ctx) {\n  native_trace::CallbackSample trace_callback(ctx.pc,ctx.lr,ctx.inst_count);'
    assert s.count(old)==1
    new='''// NATIVE_CALLBACK_TRACE_BRANCH_V1: ordinary polls create no trace scope.
static inline bool native_process_callbacks_poll(CPUContext&);
static bool native_process_callbacks_traced(CPUContext &ctx) {
  native_trace::CallbackSample trace_callback(ctx.pc,ctx.lr,ctx.inst_count);
  return native_process_callbacks_poll(ctx);
}
bool process_pending_callbacks(CPUContext &ctx) {
  native_fp_context_probe(ctx,"poll");
  if (__builtin_expect(native_trace::active.load(std::memory_order_relaxed),0))
    return native_process_callbacks_traced(ctx);
  return native_process_callbacks_poll(ctx);
}
static inline bool native_process_callbacks_poll(CPUContext &ctx) {'''
    s=s.replace(old,new,1)
    s=s.replace('static inline bool native_process_callbacks_poll(CPUContext &ctx) {\n  native_fp_context_probe(ctx,"poll");',
                'static inline bool native_process_callbacks_poll(CPUContext &ctx) {',1)
    path.write_text(s)
if 'NATIVE_CALLBACK_TRACE_SELECT_V1' not in s:
    s=s.replace('static bool native_process_callbacks_traced(CPUContext &ctx) {\n  native_trace::CallbackSample trace_callback(ctx.pc,ctx.lr,ctx.inst_count);',
                'static bool native_process_callbacks_traced(CPUContext &ctx, const char* selected) {\n  native_trace::CallbackSample trace_callback(selected);',1)
    old='  if (__builtin_expect(native_trace::active.load(std::memory_order_relaxed),0))\n    return native_process_callbacks_traced(ctx);'
    new='''  // NATIVE_CALLBACK_TRACE_SELECT_V1: only sampled polls create a scope.
  if (__builtin_expect(native_trace::active.load(std::memory_order_relaxed),0)) {
    const char* selected=native_trace::CallbackSample::choose(ctx.pc,ctx.lr,ctx.inst_count);
    if(*selected)return native_process_callbacks_traced(ctx,selected);
  }'''
    assert s.count(old)==1;s=s.replace(old,new,1);path.write_text(s)
s=s.replace('static bool native_process_callbacks_traced(',
            'static __attribute__((noinline,cold)) bool native_process_callbacks_traced(')
path.write_text(s)
path=rt/'src/hle/gx/fifo_parser.cpp';s=path.read_text()
if 'NATIVE_VECTOR_DECODE_PLAN_V1' not in s:
    (p/'runtime-overlays/vector_plan_baseline.cpp').write_text(s)
    anchor='void FifoParser::DecodeDraw(GXCommand& c) {'
    assert s.count(anchor)==1;s=s.replace(anchor,(p/'runtime-overlays/vector_decode_plan.inc').read_text()+'\n'+anchor,1)
    anchor='    c.vertices.reserve(r.count);'
    plans='''
    const NativeVectorPlan pos(r.vat.posMask,r.vat.posType,r.vat.posShift,r.arrayBase[0],r.arrayStride[0],r.vat.posElements?3:2);
    const NativeVectorPlan nrm(r.vat.nrmMask,r.vat.nrmType,0,r.arrayBase[1],r.arrayStride[1],r.vat.nrmElements?9:3);
    std::vector<NativeVectorPlan> tex;tex.reserve(8);
    for(unsigned t=0;t<8;++t)tex.emplace_back(r.vat.texMask[t],r.vat.texType[t],r.vat.texShift[t],r.arrayBase[4+t],r.arrayStride[4+t],r.vat.texElements[t]?2:1);
'''
    assert s.count(anchor)==1;s=s.replace(anchor,anchor+plans,1)
    old='''ReadVectorAttr(buf, off, buf_size, r.vat.posMask, r.vat.posType,
                            r.vat.posShift, r.arrayBase[0], r.arrayStride[0],
                            r.vat.posElements ? 3 : 2, vtx.pos)'''
    assert s.count(old)==1;s=s.replace(old,'pos.read(buf,off,buf_size,vtx.pos)',1)
    old='''ReadVectorAttr(buf, off, buf_size, r.vat.nrmMask, r.vat.nrmType,
                                0, r.arrayBase[1], r.arrayStride[1],
                                r.vat.nrmElements ? 9 : 3, nbt)'''
    assert s.count(old)==1;s=s.replace(old,'nrm.read(buf,off,buf_size,nbt)',1)
    old='ReadVectorAttr(buf,off,buf_size,r.vat.texMask[t],r.vat.texType[t],r.vat.texShift[t],r.arrayBase[4+t],r.arrayStride[4+t],r.vat.texElements[t]?2:1,vtx.tex[t])'
    assert s.count(old)==1;s=s.replace(old,'tex[t].read(buf,off,buf_size,vtx.tex[t])',1);path.write_text(s)
a=s.index('// NATIVE_VECTOR_DECODE_PLAN_V1:')
b=s.index('template<bool Direct> static void NativeDecodeDraw(',a) if 'NATIVE_DIRECT_VERTEX_DECODE_V1' in s else s.index('void FifoParser::DecodeDraw(',a)
s=s[:a]+(p/'runtime-overlays/vector_decode_plan.inc').read_text()+'\n'+s[b:]
s=s.replace('    std::vector<NativeVectorPlan> tex;tex.reserve(8);','    std::array<NativeVectorPlan,8> tex;').replace('tex.emplace_back(r.vat.texMask[t],r.vat.texType[t],r.vat.texShift[t],r.arrayBase[4+t],r.arrayStride[4+t],r.vat.texElements[t]?2:1);','tex[t]=NativeVectorPlan(r.vat.texMask[t],r.vat.texType[t],r.vat.texShift[t],r.arrayBase[4+t],r.arrayStride[4+t],r.vat.texElements[t]?2:1);')
path.write_text(s)
if 'NATIVE_DECODE_IN_PLACE_V1' not in s:
    a=s.index('    for (uint32_t i = 0; i < r.count; i++) {');b=s.index('  // NATIVE_NORMAL_DRAW_PROBE_BEGIN',a)
    loop=s[a:b];assert loop.count('        VertexData vtx;')==1
    loop=loop.replace('        VertexData vtx;','        // NATIVE_DECODE_IN_PLACE_V1: roll back incomplete vertices on every failure.\n        c.vertices.emplace_back();\n        VertexData& vtx=c.vertices.back();',1)
    loop=loop.replace('return;','{c.vertices.pop_back();return;}')
    assert loop.count('        c.vertices.push_back(vtx);')==1;loop=loop.replace('        c.vertices.push_back(vtx);','')
    s=s[:a]+loop+s[b:];path.write_text(s)
path=rt/'src/hle/gx/renderer_gl.cpp';s=path.read_text()
if 'NATIVE_COMPACT_VERTEX_UPLOAD_V1' not in s:
    a=s.index('static void FlushBatch() {');b=s.index('\n}',a)+2
    baseline=p/'runtime-overlays/compact_flush_baseline.inc'
    # Restaging can remove the helper definition but retain its call. Never
    # replace the independent full-width baseline with an optimized function.
    if 'NativeUploadBatch();' not in s[a:b]:baseline.write_text(s[a:b])
    s=s.replace('static void FlushBatch() {',(p/'runtime-overlays/compact_vertex_upload.inc').read_text()+'\nstatic void FlushBatch() {',1)
    old='''  glBufferData(GL_ARRAY_BUFFER, s_batch.size() * sizeof(BatchVertex),
               s_batch.data(), GL_STREAM_DRAW);'''
    assert s.count(old)==1 or s.count('  NativeUploadBatch();')==1;s=s.replace(old,'  NativeUploadBatch();',1)
    # CP register effects are already materialized in DrawRaw/VertexData. The
    # renderer never executes CP commands; retain every XF/BP ordering boundary.
    old='      if (cmd.type != GXCommandType::DrawPrimitive && s_batch_open) {'
    new='      // NATIVE_CP_BATCH_BOUNDARY_V1: ignored CP commands need no GL flush.\n      if (cmd.type != GXCommandType::DrawPrimitive && cmd.type != GXCommandType::CPRegister && s_batch_open) {'
    assert s.count(old)==1 or 'NATIVE_CP_BATCH_BOUNDARY_V1' in s;s=s.replace(old,new,1);path.write_text(s)
a=s.index('// NATIVE_COMPACT_VERTEX_UPLOAD_V1:');b=s.index('static void FlushBatch() {',a)
s=s[:a]+(p/'runtime-overlays/compact_vertex_upload.inc').read_text()+'\n'+s[b:]
path.write_text(s)
if 'NATIVE_GPU_DRAW_SAMPLE_V1' not in s and 'NATIVE_BUDGET_DRAW_V1' not in s:
    old='  if (s_indices.empty()) {\n    glDrawArrays('
    new='  { // NATIVE_GPU_DRAW_SAMPLE_V1: separate draw execution from broad submission gaps.\n  native_trace::GPUScope trace_gpu_draw("gpu.draw_sample_us",32);\n  if (s_indices.empty()) {\n    glDrawArrays('
    assert s.count(old)==1;s=s.replace(old,new,1)
    old='  glDrawElements(GL_TRIANGLES, GLsizei(s_indices.size()), GL_UNSIGNED_INT, nullptr);\n  }\n  if (s_glerr) {'
    new=old.replace('  if (s_glerr) {','  }\n  if (s_glerr) {')
    assert s.count(old)==1;s=s.replace(old,new,1);path.write_text(s)
path=p/'tools/build_native.py';s=path.read_text()
if "run('native-trace-bottlenecks-stage'" not in s:
    anchor="run('native-game-build',";assert s.count(anchor)==1
    s=s.replace(anchor,"run('native-trace-bottlenecks-stage',[sys.executable,PORT/'tools/fix_trace_bottlenecks.py'])\n"+anchor,1);path.write_text(s)
print('Empty-poll fast path and per-draw vector reader plans installed.')
