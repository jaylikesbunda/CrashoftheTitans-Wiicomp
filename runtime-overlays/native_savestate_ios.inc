// NATIVE_SAVESTATE_V1: snapshot orchestration, included at the end of ios.cpp (which owns the callback, IPC and WPAD state) by
// tools/fix_savestate.py. See native_savestate.h for the model. Serviced from the run_game loop head on the guest thread.
#include <filesystem>
#include <fstream>
#include <thread>

namespace nwii::runtime {
namespace hw {
void native_ss_ai(native_ss::Stream&); void native_ss_vi(native_ss::Stream&); void native_ss_pi(native_ss::Stream&);
void native_ss_ipc(native_ss::Stream&); void native_ss_pe(native_ss::Stream&); void native_ss_cp(native_ss::Stream&);
void native_ss_di(native_ss::Stream&); void native_ss_dsp(native_ss::Stream&);
bool native_ss_di_quiet(); bool native_ss_dsp_quiet(); bool native_ss_ipc_quiet();
}
namespace devices { void native_ss_fs(native_ss::Stream&); }
void native_ss_gx(native_ss::Stream&);
bool native_ss_gx_sync(int timeout_ms);

// Guest memory: 4 KiB pages, all-zero pages stored as one flag byte.
static void native_ss_ram(native_ss::Stream& s, uint8_t* base, size_t bytes) {
  uint64_t n = bytes; s.pod(n);
  if (s.loading && n != bytes) throw std::runtime_error("save state memory size differs from this runtime");
  static const uint8_t zero[4096] = {};
  for (size_t off = 0; off < bytes; off += 4096) {
    const size_t len = std::min<size_t>(4096, bytes - off);
    uint8_t present = s.loading ? 0 : uint8_t(std::memcmp(base + off, zero, len) != 0);
    s.pod(present);
    if (present) s.raw(base + off, len);
    else if (s.loading) std::memset(base + off, 0, len);
  }
}

static void native_ss_cpu(native_ss::Stream& s, CPUContext& ctx) {
  s.tag("CPU1");
  s.pod(ctx.gpr); s.pod(ctx.fpr); s.pod(ctx.ps1); s.pod(ctx.cr);
  s.pod(ctx.pc); s.pod(ctx.lr); s.pod(ctx.ctr); s.pod(ctx.xer); s.pod(ctx.hid2); s.pod(ctx.msr); s.pod(ctx.fpscr);
  s.pod(ctx.srr0); s.pod(ctx.srr1); s.pod(ctx.gqr); s.pod(ctx.sprg); s.pod(ctx.exception_pc); s.pod(ctx.inst_count);
  s.pod(ctx.reservation_addr); s.pod(ctx.dispatch_saved_ctx); s.pod(ctx.dispatch_saved_thread); s.pod(ctx.ext_resched_pending);
  uint64_t freq = ctx.tb_freq; s.pod(freq);
  if (s.loading && freq != ctx.tb_freq) throw std::runtime_error("save state timebase frequency differs");
  // The timebase continues from the saved value; the decrementer keeps its remaining count.
  uint64_t tb = ctx.read_timebase(); s.pod(tb);
  uint64_t dec_elapsed = tb - ctx.dec_written_tb; s.pod(dec_elapsed); s.pod(ctx.dec_value); s.pod(ctx.dec_irq_pending);
  s.atom(ctx.vblank_pending);
  if (s.loading) {
    const uint64_t us = tb / ctx.tb_freq * 1000000u + tb % ctx.tb_freq * 1000000u / ctx.tb_freq;
    ctx.tb_start = std::chrono::steady_clock::now() - std::chrono::microseconds(us);
    ctx.tb_cache_valid = false;
    ctx.dec_written_tb = ctx.read_timebase() - dec_elapsed;
    ctx.in_callback = false; ctx.callback_depth = 0;
    while (!ctx.backup_stack.empty()) ctx.backup_stack.pop();
    { std::lock_guard<std::mutex> lock(ctx.cb_mutex); std::queue<CallbackInfo>().swap(ctx.pending_callbacks); ctx.pending_cb_count.store(0); }
  }
}

// IPC bookkeeping, queued HLE callbacks and the emulated Wii Remotes.
static void native_ss_ios(native_ss::Stream& s) {
  s.tag("IOS1");
  s.pod(ipc_pool_next); s.pod(ios_guest_heap_ptr); s.pod(ios_ipc_heap_ptr);
  s.vec(g_pending_callbacks); s.pod(g_ipc_reply_pending); s.pod(g_ipc_interrupt_delay);
  s.tag("WPD1");
  auto& w = native_wpad_state();
  for (auto& c : w.channels) {
    s.pod(c.sampling); s.pod(c.connect); s.pod(c.extension); s.pod(c.buffer); s.pod(c.count); s.pod(c.format); s.pod(c.index); s.pod(c.dpd);
    s.pod(c.connected);   // the physical state is re-read next poll; a difference fires the connect callback as a hot-plug would
  }
  s.pod(w.next_sample);
  if (s.loading) w.connected = w.channels[0].connected;
}

static bool native_ss_quiet(CPUContext& ctx) {
  return !ctx.in_callback && ctx.callback_depth == 0 && ctx.backup_stack.empty() && ctx.pending_cb_count.load() == 0 &&
         ctx.dispatch_saved_ctx == 0 && ctx.pc != 0 && ctx.pc != 0xFFFFFFFCu &&
         g_pending_callbacks.empty() && !g_ipc_reply_pending && g_ipc_interrupt_delay == 0 &&
         hw::native_ss_di_quiet() && hw::native_ss_dsp_quiet() && hw::native_ss_ipc_quiet();
}

static void native_ss_body(native_ss::Stream& s, CPUContext& ctx) {
  s.tag("NWSS");
  uint32_t format = native_ss::kFormat; s.pod(format);
  if (s.loading && format != native_ss::kFormat) throw std::runtime_error("save state format " + std::to_string(format) + " is not supported");
  s.tag("MEM1"); native_ss_ram(s, ctx.mmu.mem1.data(), ctx.mmu.mem1.size());
  s.tag("MEM2"); native_ss_ram(s, ctx.mmu.mem2.data(), ctx.mmu.mem2.size());
  native_ss_cpu(s, ctx);
  if (s.loading) EventScheduler::get().advance(ctx, ctx.read_timebase());   // events re-armed below are relative to now
  native_ss_ios(s);
  hw::native_ss_ai(s); hw::native_ss_vi(s); hw::native_ss_pi(s); hw::native_ss_ipc(s); hw::native_ss_pe(s); hw::native_ss_cp(s);
  hw::native_ss_di(s); hw::native_ss_dsp(s);
  IOSKernel::get().native_ss_fds(s); devices::native_ss_fs(s);
  native_ss_gx(s);
  s.tag("END!");
}

static std::string native_ss_id() {
  const auto now = std::chrono::system_clock::now();
  const std::time_t t = std::chrono::system_clock::to_time_t(now);
  std::tm tm{}; localtime_s(&tm, &t);
  char b[64];
  std::snprintf(b, sizeof b, "%04d%02d%02d-%02d%02d%02d-%03d", tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec,
                int(std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count() % 1000));
  return b;
}

static void native_ss_save(CPUContext& ctx) {
  const auto t0 = std::chrono::steady_clock::now();
  if (!native_ss_gx_sync(2000)) { std::cout << "[SaveState] failed: the renderer did not drain" << std::endl; return; }
  native_ss::Stream s;
  s.buf.reserve(96u << 20);
  try { native_ss_body(s, ctx); }
  catch (const std::exception& e) { std::cout << "[SaveState] failed: " << e.what() << std::endl; return; }
  const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
  const char* dir_env = std::getenv("NWII_STATE_DIR");
  const std::filesystem::path dir = dir_env && *dir_env ? std::filesystem::path(dir_env) : std::filesystem::path("savestates");
  const std::string id = native_ss_id();
  const uint64_t saved_ms = uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count());
  std::cout << "[SaveState] captured " << (s.buf.size() >> 20) << " MiB in " << ms << " ms; writing " << id << std::endl;
  std::thread([buf = std::move(s.buf), dir, id, saved_ms]() {
    std::error_code ec; std::filesystem::create_directories(dir, ec);
    const auto file = dir / (id + ".nwstate"), part = dir / (id + ".partial");
    { std::ofstream f(part, std::ios::binary); f.write(reinterpret_cast<const char*>(buf.data()), std::streamsize(buf.size()));
      if (!f) { std::cout << "[SaveState] failed: cannot write " << part.string() << std::endl; return; } }
    std::filesystem::rename(part, file, ec);
    if (ec) { std::cout << "[SaveState] failed: " << ec.message() << std::endl; return; }
    // The host thread grabs a screenshot at its next present; give it a moment.
    std::vector<uint8_t> bmp;
    for (int i = 0; i < 100 && bmp.empty(); ++i) {
      { std::lock_guard<std::mutex> lock(native_ss::thumb.mutex); if (native_ss::thumb.ready) { bmp.swap(native_ss::thumb.bmp); native_ss::thumb.ready = false; } }
      if (bmp.empty()) std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    if (!bmp.empty()) { std::ofstream f(dir / (id + ".bmp"), std::ios::binary); f.write(reinterpret_cast<const char*>(bmp.data()), std::streamsize(bmp.size())); }
    const char* dol = std::getenv("NWII_DOL_SHA256");
    { std::ofstream f(dir / (id + ".json"));
      f << "{\"format\":" << native_ss::kFormat << ",\"saved_ms\":" << saved_ms << ",\"dol_sha256\":\"" << (dol ? dol : "") << "\"}\n"; }
    std::cout << "[SaveState] saved " << file.string() << std::endl;
  }).detach();
}

static void native_ss_load(CPUContext& ctx) {
  const char* path = std::getenv("NWII_LOAD_STATE");
  if (!path || !*path) return;
  const auto t0 = std::chrono::steady_clock::now();
  native_ss::Stream s; s.loading = true;
  {
    std::ifstream f(path, std::ios::binary);
    if (!f) { std::cout << "[SaveState] cannot open " << path << "; starting normally" << std::endl; return; }
    s.buf.assign(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
  }
  try { native_ss_body(s, ctx); }
  catch (const std::exception& e) {
    // Part of the state may already be applied; continuing would run a mixture of two machines.
    std::cout << "[SaveState] load failed: " << e.what() << std::endl;
    std::cerr << "[SaveState] load failed: " << e.what() << std::endl;
    std::exit(3);
  }
  std::cout << "[SaveState] loaded " << path << " in "
            << std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count() << " ms, pc=0x" << std::hex << ctx.pc
            << std::dec << std::endl;
}

void native_ss_service(CPUContext& ctx) {
  const int req = native_ss::request.load(std::memory_order_acquire);
  if (req == 2) { native_ss::request.store(0); native_ss_load(ctx); return; }
  if (req != 1) return;
  static std::chrono::steady_clock::time_point first{};
  const auto now = std::chrono::steady_clock::now();
  if (first == std::chrono::steady_clock::time_point{}) first = now;
  if (!native_ss_quiet(ctx)) {
    if (now - first > std::chrono::seconds(5)) {
      std::cout << "[SaveState] failed: no quiet point within 5 s (disc or audio busy)" << std::endl;
      native_ss::request.store(0); first = {};
    }
    return;
  }
  native_ss::request.store(0); first = {};
  native_ss_save(ctx);
}
}
