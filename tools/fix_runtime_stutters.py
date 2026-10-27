"""Remove routine synchronous guest diagnostics and reuse the HUD mesh."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
f=rt/'src/hle/ios.cpp';s=f.read_text()
anchor='static bool native_process_callbacks_slow(CPUContext &ctx, bool poll_clock_due, bool wpad_polled) {'
decl='\n  static const bool routine_diag=std::getenv("NWII_RUNTIME_DIAG") && !std::getenv("NWII_NO_DIAG"); // NATIVE_ROUTINE_DIAG_FLAG_V1'
if decl not in s:
 assert s.count(anchor)==1;s=s.replace(anchor,anchor+decl,1)
old='    static const bool diag_on = std::getenv("NWII_NO_DIAG") == nullptr;'
new='    static const bool diag_on = routine_diag; // NATIVE_QUIET_CALLBACK_V1'
if old in s:s=s.replace(old,new,1)
old='  if ((ctx.inst_count % 20000000) == 0) {'
new='  if (routine_diag && (ctx.inst_count % 20000000) == 0) { // NATIVE_QUIET_HEARTBEAT_V1'
if old in s:s=s.replace(old,new,1)
old='      if (ctx.pc == 0 || (restore_count % 64) == 1) {'
new='      if (ctx.pc == 0 || (routine_diag && (restore_count % 64) == 1)) { // NATIVE_QUIET_RESTORE_V1'
if old in s:s=s.replace(old,new,1)
s=s.replace('static const bool diag_on = std::getenv("NWII_RUNTIME_DIAG") && !std::getenv("NWII_NO_DIAG");','static const bool diag_on = routine_diag;')
s=s.replace('if (std::getenv("NWII_RUNTIME_DIAG") && (ctx.inst_count % 20000000) == 0)', 'if (routine_diag && (ctx.inst_count % 20000000) == 0)')
s=s.replace('(std::getenv("NWII_RUNTIME_DIAG") && (restore_count % 64) == 1)', '(routine_diag && (restore_count % 64) == 1)')
f.write_text(s)
f=rt/'src/hle/gx.cpp';s=f.read_text()
anchor='    {\n        namespace fp = nwii::runtime::gx;\n        extern uint64_t g_prof_decode_us_accum;'
new='''    // NATIVE_QUIET_GXPROF_V1: F3/F4 provide profiling without stdout I/O.
    static const bool routine_gx_diag=std::getenv("NWII_RUNTIME_DIAG") && !std::getenv("NWII_NO_DIAG");
    if(routine_gx_diag){
        namespace fp = nwii::runtime::gx;
        extern uint64_t g_prof_decode_us_accum;'''
if 'NATIVE_QUIET_GXPROF_V1' not in s:
 assert s.count(anchor)==1;s=s.replace(anchor,new,1);f.write_text(s)
f=rt/'src/core/main.cpp';s=f.read_text()
anchor='      if (!headless) {\n        static auto t0 = std::chrono::steady_clock::now();'
new='''      // NATIVE_QUIET_STAT_V1: no periodic full-frame GPU readback in gameplay.
      static const bool routine_host_diag=std::getenv("NWII_RUNTIME_DIAG") && !std::getenv("NWII_NO_DIAG");
      if (!headless && routine_host_diag) {
        static auto t0 = std::chrono::steady_clock::now();'''
if 'NATIVE_QUIET_STAT_V1' not in s:
 assert s.count(anchor)==1;s=s.replace(anchor,new,1);f.write_text(s)
print('Routine callback diagnostics opt-in with NWII_RUNTIME_DIAG; error and explicit probe logs retained.')
