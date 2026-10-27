"""Drain the GX FIFO on demand when the guest CPU is idle, instead of only once per video field.

A 3D frame issues many GXDrawDone waits (render-to-texture passes). Each wait is only released when the host loop drains
and parses the FIFO, which happened once per 20 ms field, so a frame with ~35 waits took ~0.7 s (about 1.4 fps in level
geometry while the guest CPU was ~70% idle). Now, when the guest is in the OS idle loop (803BBFA0) and GX bytes are
pending, it requests an immediate drain; the host loop services it while waiting for the field deadline. VI cadence
and interrupt timing are unchanged. Disable with NWII_NO_GX_IDLE_DRAIN=1."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
SRC = PORT / 'build/nwii-full-source/nWiiRuntime/src'


def load(p):
    s = p.read_bytes().decode('utf-8')
    nl = '\r\n' if '\r\n' in s else '\n'
    return s.replace('\r\n', '\n'), nl


def save(p, t, nl):
    p.write_bytes(t.replace('\n', nl).encode('utf-8'))


# ---- gx.cpp: globals + counters
gx = SRC / 'hle/gx.cpp'
t, nl = load(gx)
if 'g_gx_pending_bytes' in t:
    print('gx.cpp: already staged')
else:
    old_decl = 'void ProcessGXFifo() {'
    new_decl = ('namespace nwii::runtime {\n'
                'std::atomic<size_t> g_gx_pending_bytes{0};\n'
                'std::atomic<bool> g_gx_drain_request{false};\n'
                '}\n'
                'void ProcessGXFifo() {')
    if old_decl not in t:
        raise SystemExit('gx.cpp: ProcessGXFifo anchor missing')
    t = t.replace(old_decl, new_decl, 1)
    # count pushed bytes
    old_push = 'static inline void wgp_push(uint8_t b) {'
    if old_push not in t:
        raise SystemExit('gx.cpp: wgp_push anchor missing')
    t = t.replace(old_push, old_push + '\n    g_gx_pending_bytes.fetch_add(1, std::memory_order_relaxed);', 1)
    # reset when the drain takes the bytes
    old_take = '            g_hw_fifo.clear();\n        }\n    }\n    if (s_parse_buf.empty()) return;'
    new_take = '            g_hw_fifo.clear();\n        }\n        g_gx_pending_bytes.store(0, std::memory_order_relaxed);\n    }\n    if (s_parse_buf.empty()) return;'
    if old_take not in t:
        raise SystemExit('gx.cpp: drain take anchor missing')
    t = t.replace(old_take, new_take, 1)
    if '#include <atomic>' not in t:
        t = '#include <atomic>\n' + t
    save(gx, t, nl)
    print('gx.cpp: staged')

# ---- ios.cpp: idle request
ios = SRC / 'hle/ios.cpp'
t, nl = load(ios)
if 'NATIVE_GX_IDLE_DRAIN' in t:
    print('ios.cpp: already staged')
else:
    needle = 'bool process_pending_callbacks(CPUContext &ctx) {\n'
    if t.count(needle) != 1:
        raise SystemExit('ios.cpp: callback context changed')
    pre = 'extern std::atomic<size_t> g_gx_pending_bytes;\nextern std::atomic<bool> g_gx_drain_request;\n'
    body = ('  // NATIVE_GX_IDLE_DRAIN: guest idle with GX bytes pending -> ask the host loop to drain now.\n'
            '  { static const bool idle_drain = std::getenv("NWII_NO_GX_IDLE_DRAIN") == nullptr;\n'
            '    if (idle_drain && ctx.pc == 0x803BBFA0u && g_gx_pending_bytes.load(std::memory_order_relaxed) > 0)\n'
            '      g_gx_drain_request.store(true, std::memory_order_relaxed); }\n')
    t = t.replace(needle, pre + needle + body, 1)
    if '#include <atomic>' not in t:
        t = '#include <atomic>\n' + t
    save(ios, t, nl)
    print('ios.cpp: staged')

# ---- main.cpp: service the request while waiting for the field deadline
mp = SRC / 'core/main.cpp'
t, nl = load(mp)
if 'g_gx_drain_request' in t:
    print('main.cpp: already staged')
else:
    old_sleep = '    std::this_thread::sleep_until(field_deadline);'
    new_sleep = '''    // NATIVE_GX_IDLE_DRAIN: service on-demand GX drains until the field deadline.
    extern void ProcessGXFifo();
    for (;;) {
      const auto wait_now = std::chrono::steady_clock::now();
      if (wait_now >= field_deadline) break;
      if (nwii::runtime::g_gx_drain_request.exchange(false, std::memory_order_relaxed)) {
        if (!headless) { glBindFramebuffer(GL_FRAMEBUFFER, efb_fbo); glViewport(0, 0, 640, 480); }
        ProcessGXFifo();
        continue;
      }
      const auto remaining = std::chrono::duration_cast<std::chrono::microseconds>(field_deadline - wait_now);
      std::this_thread::sleep_for(std::min(remaining, std::chrono::microseconds(1000)));
    }'''
    if old_sleep not in t:
        raise SystemExit('main.cpp: sleep_until anchor missing')
    t = t.replace(old_sleep, new_sleep, 1)
    decl = 'namespace nwii::runtime { extern std::atomic<bool> g_gx_drain_request; }\n'
    idx = t.index('int main(')
    t = t[:idx] + decl + t[idx:]
    if '#include <atomic>' not in t:
        t = '#include <atomic>\n' + t
    if '#include <algorithm>' not in t:
        t = '#include <algorithm>\n' + t
    save(mp, t, nl)
    print('main.cpp: staged')

# NOTE: condition-variable wakeup variant is applied by tools/fix_gx_drain_cv.py (kept separate).
