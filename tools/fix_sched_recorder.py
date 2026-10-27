"""Always-on scheduler-lock recorder, so a stuck-scheduler stall explains itself.

Frozen video + looping audio is the guest scheduler's disable counter
(0x80597790, r13-0x2950) stuck at 1: SelectThread returns immediately, ready
threads never run, deferred interrupts never deliver. A live debugger look at such
a stall confirmed: counter 1, run queues empty, ready threads parked, thread
stacks otherwise normal. What is still unknown is who raised the counter without
lowering it.

The existing NWII_WATCH hook is wired to that address by default and, when
NWII_WATCH is not set, records every write into a 256-entry ring (value, pc, lr,
current thread, dispatch/callback state, MSR, 4-deep call chain). The 5-second
[DIAG] tick now checks the counter and, once it has been non-zero for two ticks
in a row, prints the last 48 writes plus the thread table to the log (at most
once per minute). NWII_WATCH=<addr> still gives the old full trace.

Early GX drain is now opt-in (NWII_GX_EARLY_DRAIN=1): the first run with it on
froze in this exact state, and the earlier idle-only drain had not.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
src = port / 'build/nwii-full-source/nWiiRuntime/src'
marker = 'NATIVE_SCHED_RECORDER_V1'

p = src / 'core/os_hle_stubs.cpp'
t = p.read_text()
if marker not in t:
    old = '  return env ? (uint32_t)std::strtoul(env, nullptr, 16) : 0;\n}();'
    assert t.count(old) == 1
    t = t.replace(old, '  return env ? (uint32_t)std::strtoul(env, nullptr, 16) : 0x80597790u; // NATIVE_SCHED_RECORDER_V1\n}();')
    old = 'void watch_hit(uint32_t addr, uint32_t value, int width) {\n  CPUContext *c = g_ctx_ptr;\n'
    assert t.count(old) == 1
    new = r'''struct NativeSchedRec { uint64_t n; uint32_t val, pc, lr, cur, saved, incb, msr, chain[4]; };
static NativeSchedRec g_sched_ring[256];
static uint64_t g_sched_ring_n = 0;

void native_sched_dump(CPUContext &c) {
  std::cout << std::hex << "[SCHEDREC] ---- scheduler counter stuck at "
            << c.mmu.read32(0x80597790) << " (resched=" << c.mmu.read32(0x80597794) << ") last writes, oldest first ----\n";
  const uint64_t n = g_sched_ring_n, count = n < 48 ? n : 48;
  for (uint64_t i = n - count; i < n; ++i) {
    const auto &r = g_sched_ring[i % 256];
    std::cout << "[SCHEDREC] #" << std::dec << r.n << std::hex << " val=" << r.val << " pc=" << r.pc << " lr=" << r.lr
              << " cur=" << r.cur << " saved_ctx=" << r.saved << " in_cb=" << r.incb << " msr=" << r.msr << " chain:";
    for (int k = 0; k < 4; ++k) std::cout << " " << r.chain[k];
    std::cout << "\n";
  }
  uint32_t th = c.mmu.read32(0x800000DC), cur = c.mmu.read32(0x800000E4);
  for (int g = 0; th && g < 24; ++g, th = c.mmu.read32(th + 0x2FC))
    std::cout << "[SCHEDREC] thread=" << th << (th == cur ? "*" : " ") << " state=" << std::dec << (c.mmu.read32(th + 0x2C8) & 0xFFFF)
              << " prio=" << c.mmu.read32(th + 0x2D0) << std::hex << " waitq=" << c.mmu.read32(th + 0x2DC) << "\n";
  std::cout << std::dec << std::flush;
}

void native_sched_stall_check(CPUContext &c) {
  static int consecutive = 0;
  static std::chrono::steady_clock::time_point last_dump{};
  if (c.mmu.read32(0x80597790) == 0) { consecutive = 0; return; }
  if (++consecutive < 2) return;
  const auto now = std::chrono::steady_clock::now();
  if (last_dump.time_since_epoch().count() != 0 && now - last_dump < std::chrono::seconds(60)) return;
  last_dump = now;
  native_sched_dump(c);
}

void watch_hit(uint32_t addr, uint32_t value, int width) {
  CPUContext *c = g_ctx_ptr;
  {
    static const bool ring_mode = std::getenv("NWII_WATCH") == nullptr;
    if (ring_mode) {
      if (!c || width != 32) return;
      NativeSchedRec &r = g_sched_ring[g_sched_ring_n % 256];
      r = {++g_sched_ring_n, value, c->pc, c->lr, c->mmu.read32(0x800000E4), c->dispatch_saved_ctx,
           uint32_t(c->in_callback), c->msr, {0, 0, 0, 0}};
      uint32_t s = c->gpr[1];
      for (int i = 0; i < 4; ++i) {
        if ((s >> 28) != 8 && (s >> 28) != 9) break;
        uint32_t nx = c->mmu.read32(s);
        if ((nx >> 28) != 8 && (nx >> 28) != 9) break;
        if (nx <= s) break;
        r.chain[i] = c->mmu.read32(nx + 4);
        s = nx;
      }
      return;
    }
  }
'''
    t = t.replace(old, new)
    p.write_text(t)

# NATIVE_SCHED_RECORDER_OPT_IN_V2: the ring is only ever printed by the [DIAG] tick (NWII_RUNTIME_DIAG), yet a
# non-zero default watch makes every guest store run the watch test and keeps the watched page on the slow path.
# Record by default only when that tick can print it, or when NWII_SCHED_RECORDER asks; NWII_WATCH is unchanged.
# native_launch_flags.h applies the same policy and must stay in step with this line.
p = src / 'core/os_hle_stubs.cpp'
t = p.read_text()
v1 = '  return env ? (uint32_t)std::strtoul(env, nullptr, 16) : 0x80597790u; // NATIVE_SCHED_RECORDER_V1\n}();'
v2 = ('  if (env) return (uint32_t)std::strtoul(env, nullptr, 16); // NATIVE_SCHED_RECORDER_V1\n'
      '  const char *rec = std::getenv("NWII_SCHED_RECORDER"); // NATIVE_SCHED_RECORDER_OPT_IN_V2\n'
      '  const bool on = rec ? rec[0] != \'0\' : (std::getenv("NWII_RUNTIME_DIAG") && !std::getenv("NWII_NO_DIAG"));\n'
      '  return on ? 0x80597790u : 0u;\n}();')
if 'NATIVE_SCHED_RECORDER_OPT_IN_V2' not in t:
    assert t.count(v1) == 1
    p.write_text(t.replace(v1, v2))

p = src / 'hle/ios.cpp'
t = p.read_text()
if marker not in t:
    old = 'extern void native_gx_publish(); // NATIVE_GX_PRIVATE_FIFO_V1\n'
    assert t.count(old) == 1
    t = t.replace(old, old + 'void native_sched_stall_check(CPUContext &c); // NATIVE_SCHED_RECORDER_V1\n')
    old = '        diag_last = diag_now;\n'
    assert t.count(old) == 1
    t = t.replace(old, old + '        native_sched_stall_check(ctx);\n')
    p.write_text(t)

p = src / 'hle/gx.cpp'
t = p.read_text()
old = 'static const bool early = std::getenv("NWII_NO_GX_EARLY_DRAIN") == nullptr;'
if old in t:
    t = t.replace(old, 'static const bool early = std::getenv("NWII_GX_EARLY_DRAIN") != nullptr;')
    p.write_text(t)
print('Scheduler recorder installed; early GX drain opt-in.')
