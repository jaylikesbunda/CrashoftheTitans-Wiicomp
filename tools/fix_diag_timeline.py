"""Always-on, low-rate diagnostic timeline in the runtime log.

One [DIAG] line every ~5 s of wall time records what can stall a game timer:
wall clock vs guest timebase, VI interrupts, GX tokens/finishes, decrementer
fires, the pad state the runtime sees, interrupt enable and queued callbacks.
A session that misbehaves on hardware the developer does not have can then
be diagnosed from its log alone. NWII_NO_DIAG=1 disables it.
"""
from pathlib import Path
port=Path(__file__).resolve().parents[1]
path=port/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp'
text=path.read_text()
marker='NATIVE_DIAG_TIMELINE_V1'
if marker not in text:
    anchor='bool process_pending_callbacks(CPUContext &ctx) {'
    assert text.count(anchor)==1
    text=text.replace(anchor,'static uint64_t g_diag_dec_fires = 0; // NATIVE_DIAG_TIMELINE_V1\n'+anchor,1)
    fire='ctx.dec_irq_pending = false;'
    assert text.count(fire)==1
    text=text.replace(fire,fire+' ++g_diag_dec_fires;',1)
    due='const bool poll_clock_due = poll_no_throttle || ((++poll_clock_phase) & 15u) == 0;'
    assert text.count(due)==1
    block='''
  { // NATIVE_DIAG_TIMELINE_V1
    static const bool diag_on = std::getenv("NWII_NO_DIAG") == nullptr;
    static const auto diag_t0 = std::chrono::steady_clock::now();
    static auto diag_last = diag_t0;
    if (diag_on && poll_clock_due) {
      const auto diag_now = std::chrono::steady_clock::now();
      if (diag_now - diag_last >= std::chrono::seconds(5)) {
        diag_last = diag_now;
        auto &w = native_wpad_state();
        std::cout << std::dec << "[DIAG] wall=" << std::chrono::duration<double>(diag_now - diag_t0).count()
                  << " guest_tb_s=" << double(ctx.read_timebase()) / double(ctx.tb_freq)
                  << " vi=" << hw::native_vi_events.load() << " tokens=" << hw::native_pe_tokens.load()
                  << " finishes=" << hw::native_pe_finishes.load() << " dec_fires=" << g_diag_dec_fires
                  << " pad(conn=" << w.connected << " phys=" << w.physical << " btn=0x" << std::hex << unsigned(w.buttons)
                  << std::dec << " sx=" << int(w.sx) << " sy=" << int(w.sy) << ") msr=0x" << std::hex << ctx.msr
                  << std::dec << " in_cb=" << ctx.in_callback << " cbq=" << ctx.pending_cb_count.load() << std::endl;
      }
    }
  }'''
    text=text.replace(due,due+block,1)
    path.write_text(text)
print('[DIAG] timeline line added to the runtime log (NWII_NO_DIAG=1 disables).')
