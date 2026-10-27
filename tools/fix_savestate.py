"""Save states: F1 in game writes a snapshot; NWII_LOAD_STATE=<file> starts the game from one (launcher "Save states" page).

A snapshot is taken at the run_game loop head, where callbacks and thread switches have unwound every host frame, so the CPU
context and guest memory are complete. Each device module serializes its own file-scope state through native_ss::Stream (the same
function saves and loads) and re-arms the scheduler events its registers imply; ios.cpp orchestrates (native_savestate_ios.inc).
Saving waits for a quiet point (no callback, IPC, disc or ARAM transfer in flight, DSP idle, GX FIFO drained). Loading happens at the
first dispatcher step after a normal boot, before guest code runs. The GX register file is restored and also replayed to the renderer as
ordinary BP/CP/XF commands, so renderer- and parser-side shadows are rebuilt the normal way.

Installs:
  * include/runtime/native_savestate.h   (runtime-overlays/native_savestate.h)
  * src/hle/native_savestate_ios.inc      (runtime-overlays/native_savestate_ios.inc), included at the end of src/hle/ios.cpp
and appends one hook to each device module, adds Protocol::Serialize (native_ax) and IOSKernel::native_ss_fds (ios_kernel.h), F1 and the
thumbnail grab to src/core/main.cpp, and the request check to the generated run_game loop.

Runs after every other stage (it reads their final text). Contained and fail-soft: all anchors are checked before anything is written;
if one has moved, nothing is installed, a one-line reason is printed and the stage exits 0. Idempotent.
"""
from pathlib import Path
import sys

MARK = 'NATIVE_SAVESTATE_V1'
PORT = Path(__file__).resolve().parents[1]
RT = PORT / 'build/nwii-full-source/nWiiRuntime'
GENERATED = PORT / 'build/crash-generated'
INC = '#include "runtime/native_savestate.h" // ' + MARK + '\n'

CRLF, LF = chr(13) + chr(10), chr(10)


class Skip(Exception):
    pass


def read(path):
    """Text with LF line ends, plus whether the file used CRLF (written back the same way)."""
    raw = path.read_bytes().decode('utf-8', errors='surrogateescape')
    return raw.replace(CRLF, LF), CRLF in raw


def write(path, text, crlf):
    path.write_bytes((text.replace(LF, CRLF) if crlf else text).encode('utf-8', errors='surrogateescape'))


def replace_once(text, old, new, where):
    if text.count(old) != 1:
        raise Skip('%s: expected one occurrence of %r, found %d' % (where, old.strip()[:70], text.count(old)))
    return text.replace(old, new, 1)


def append_hook(text, body):
    return INC + text.rstrip() + '\n\n' + body.strip() + '\n'


HOOKS = {
    'src/hw/hw_ai.cpp': '''
namespace nwii::runtime::hw { // NATIVE_SAVESTATE_V1
void native_ss_ai(native_ss::Stream& s) {
  s.tag("AI01"); s.pod(ai_control); s.pod(ai_volume); s.pod(ai_count); s.pod(ai_target); s.pod(ai_fraction);
  if (s.loading) { ai_last = nwii::runtime::get_os_time(); ai_irq(); }   // ai_last is host time: restart the sample clock now
}
}''',
    'src/hw/hw_vi.cpp': '''
namespace nwii::runtime::hw { // NATIVE_SAVESTATE_V1
void native_ss_vi(native_ss::Stream& s) { s.tag("VI01"); s.pod(vi_regs); s.pod(g_vi_top_field_base); s.pod(g_vi_btm_field_base); }
}''',
    'src/hw/hw_pi.cpp': '''
namespace nwii::runtime::hw { // NATIVE_SAVESTATE_V1
void native_ss_pi(native_ss::Stream& s) {
  s.tag("PI01"); s.atom(pi_intsr); s.atom(pi_intmr); s.pod(pi_fifo_base); s.pod(pi_fifo_end); s.pod(pi_fifo_wptr);
}
}''',
    'src/hw/hw_ipc.cpp': '''
namespace nwii::runtime::hw { // NATIVE_SAVESTATE_V1
void native_ss_ipc(native_ss::Stream& s) {
  s.tag("IPC1"); s.pod(ipc_arm_msg); s.pod(ipc_arm_ctrl); s.pod(ipc_ppc_ctrl); s.pod(ipc_ppc_msg); s.deq(ipc_replies);
}
bool native_ss_ipc_quiet() { return ipc_replies.empty(); }
}''',
    'src/hw/hw_pe.cpp': '''
namespace nwii::runtime::hw { // NATIVE_SAVESTATE_V1
void native_ss_pe(native_ss::Stream& s) {
  std::lock_guard<std::mutex> lock(g_pe_transaction_mutex);
  s.tag("PE01"); s.atom(g_pe_ctrl); s.atom(g_pe_token_pending); s.atom(g_pe_finish_pending); s.atom(g_pe_sr); s.atom(g_pe_token);
}
}''',
    'src/hw/hw_cp.cpp': '''
namespace nwii::runtime::hw { // NATIVE_SAVESTATE_V1
void native_ss_cp(native_ss::Stream& s) {
  s.tag("CP01"); s.pod(g_cp_cr); s.pod(g_cp_sr); s.pod(g_cp_clear); s.pod(g_cp_fifo_base); s.pod(g_cp_fifo_end); s.pod(g_cp_fifo_hi);
  s.pod(g_cp_fifo_lo); s.pod(g_cp_fifo_rw_dist); s.pod(g_cp_fifo_wp); s.pod(g_cp_fifo_rp);
}
}''',
    'src/hw/hw_di.cpp': '''
namespace nwii::runtime::hw { // NATIVE_SAVESTATE_V1
void native_ss_di(native_ss::Stream& s) {
  s.tag("DI01"); s.pod(di_cmd); s.pod(di_mar); s.pod(di_len); s.pod(di_cr); s.pod(di_imm); s.pod(di_sr); s.pod(di_cvr);
  s.pod(di_stream_playing); s.pod(g_di_interrupt_delay);
}
// A transfer in flight completes through a scheduler event, which a snapshot cannot carry.
bool native_ss_di_quiet() { return (di_cr & 1u) == 0 && g_di_interrupt_delay == 0; }
}''',
    'src/hw/hw_dsp.cpp': '''
namespace nwii::runtime::hw { // NATIVE_SAVESTATE_V1
bool native_ss_dsp_quiet() { return !native_audio_inflight && !native_mail_scheduled && ar_cnt == 0; }
void native_ss_dsp(native_ss::Stream& s) {
  s.tag("DSP1");
  s.pod(dsp_mbox_cpu_hi); s.pod(dsp_mbox_cpu_lo); s.pod(dsp_mbox_dsp_hi); s.pod(dsp_mbox_dsp_lo);
  s.pod(ar_mmaddr); s.pod(ar_araddr); s.pod(ar_cnt); s.pod(dsp_control); s.pod(ar_size); s.pod(ar_mode); s.pod(ar_refresh);
  s.vec(native_dsp_mails); s.pod(adma_addr); s.pod(adma_control);
  s.tag("AXP1"); native_ax().Serialize(s);
  if (!s.loading) return;
  { std::lock_guard<std::mutex> lock(adma_mutex); adma_ring.clear(); }
  if (s_adma_event_id) { EventScheduler::get().cancel(s_adma_event_id); s_adma_event_id = 0; }
  if (adma_control & 0x8000)
    s_adma_event_id = EventScheduler::get().schedule_recurring(
        std::max<uint64_t>(1, g_ctx_ptr->tb_freq * (uint64_t(adma_control & 0x7fff) * 8) / 32000), adma_event_cb);
  native_mail_scheduled = false; native_audio_inflight = false;
  native_dsp_schedule_mail();
  dsp_update_pi();
}
}''',
    'src/hle/devices/fs_device.cpp': '''
namespace nwii::runtime::devices { // NATIVE_SAVESTATE_V1: NAND files are reopened at their saved position
void native_ss_fs(native_ss::Stream& s) {
  s.tag("NFS1");
  for (auto& h : g_handles) {
    uint8_t open = h.open ? 1 : 0; s.pod(open);
    std::string host = h.host.string(); s.str(host); s.pod(h.pos);
    if (!s.loading) continue;
    h.stream.reset(); h.open = false; h.host = host;
    if (!open) continue;
    auto st = std::make_unique<std::fstream>(h.host, std::ios::in | std::ios::out | std::ios::binary);
    if (!st->is_open()) st = std::make_unique<std::fstream>(h.host, std::ios::in | std::ios::binary);
    if (!st->is_open()) throw std::runtime_error("save state: cannot reopen NAND file " + host);
    h.stream = std::move(st); h.open = true;
  }
}
}''',
}

# Header accessors, so no module needs to open private state.
PROTOCOL_OLD = 'private:\n  enum class State{Rom,Commands,Address,Next,Upload};'
PROTOCOL_NEW = '''  // NATIVE_SAVESTATE_V1: the protocol state; the microcode object is rebuilt from its identity, as Load() builds it.
  template<class Stream> void Serialize(Stream& s){
    uint8_t has_ucode=ax?1:0; s.pod(has_ucode);
    s.pod(crc); s.pod(command_frames); s.pod(state); s.pod(rom_command); s.pod(code_address); s.pod(code_size);
    s.pod(command_size); s.pod(upload); s.pod(upload_index); s.vec(dsp.mail.pending);
    u16 compressor=(!s.loading&&ax)?ax->CompressorPos():0; s.pod(compressor);
    if(!s.loading)return;
    if(has_ucode){
      ax=std::make_unique<DSP::HLE::AXWiiUCode>(&dsp,crc);
      ax->LoadCoefficients(coefficients.data(),coefficients.size());ax->CompressorPos()=compressor;
    }else ax.reset();
  }
private:
  enum class State{Rom,Commands,Address,Next,Upload};'''
BASE_OLD = 'class AXUCode {\nprotected:\n'
BASE_NEW = 'class AXUCode {\npublic:\n  u16& CompressorPos(){return m_compressor_pos;} // NATIVE_SAVESTATE_V1\nprotected:\n'
KERNEL_OLD = '    int32_t ioctlv(CPUContext& ctx, const IpcRequest& req);\n'
KERNEL_NEW = KERNEL_OLD + '''
    // NATIVE_SAVESTATE_V1: the descriptor table for save states; devices are matched by name.
    template <class Stream> void native_ss_fds(Stream& s) {
        s.tag("IOSK");
        uint32_t n = uint32_t(m_fds.size()); s.pod(n);
        if (s.loading) { if (n > 4096) throw std::runtime_error("save state fd table too large"); m_fds.assign(n, FdEntry{}); }
        for (auto& e : m_fds) {
            std::string name = e.device ? e.device->get_name() : ""; s.str(name);
            s.pod(e.internal_fd); s.pod(e.in_use);
            if (!s.loading) continue;
            e.device = nullptr;
            for (const auto& d : m_devices) if (name == d->get_name()) e.device = d.get();
            if (!name.empty() && !e.device) throw std::runtime_error("save state names an IOS device this runtime lacks: " + name);
        }
    }
'''

GX_BUSY_OLD = '''void ProcessGXFifo() {
    native_trace::Scope trace_drain("gx.drain");
'''
GX_BUSY_NEW = '''static std::vector<uint8_t> s_parse_buf; // NATIVE_SAVESTATE_V1: file scope so a snapshot can carry a partial command
static std::atomic<int> s_native_ss_gx_busy{0};
void ProcessGXFifo() {
    native_trace::Scope trace_drain("gx.drain");
    s_native_ss_gx_busy.fetch_add(1, std::memory_order_acq_rel);
    struct NativeSsBusy { ~NativeSsBusy() { s_native_ss_gx_busy.fetch_sub(1, std::memory_order_acq_rel); } } native_ss_busy;
'''
GX_LOCAL = '    static std::vector<uint8_t> s_parse_buf;\n'
GX_TAIL = r'''
// NATIVE_SAVESTATE_V1
namespace nwii::runtime {
// Guest thread: publish everything staged and wait until the render thread has consumed it and is idle.
bool native_ss_gx_sync(int timeout_ms) {
    const auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    for (;;) {
        native_gx_publish();
        bool empty;
        { nwii::runtime::NativeFifoLockGuard lock(g_fifo_mutex); empty = g_hw_fifo.empty(); }
        if (empty && s_native_ss_gx_busy.load(std::memory_order_acquire) == 0) {
            // ProcessGXFifo may have swapped the FIFO out just before the busy check; confirm once more.
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
            { nwii::runtime::NativeFifoLockGuard lock(g_fifo_mutex); empty = g_hw_fifo.empty(); }
            if (empty && s_native_ss_gx_busy.load(std::memory_order_acquire) == 0) return true;
        }
        if (std::chrono::steady_clock::now() >= until) return false;
        NativeRequestGxDrain();
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}

static void native_ss_put32(std::vector<uint8_t>& o, uint32_t v) { o.push_back(uint8_t(v >> 24)); o.push_back(uint8_t(v >> 16)); o.push_back(uint8_t(v >> 8)); o.push_back(uint8_t(v)); }
static void native_ss_xf_floats(std::vector<uint8_t>& o, uint32_t address, const float* values, uint32_t count) {
    o.push_back(0x10); native_ss_put32(o, ((count - 1) << 16) | address);
    for (uint32_t i = 0; i < count; ++i) { uint32_t w; std::memcpy(&w, &values[i], 4); native_ss_put32(o, w); }
}
// The register file as GX commands. BP trigger registers (draw done, tokens, EFB copy, TLUT/TMEM loads, cache invalidate,
// the one-shot mask) are left out; everything else is configuration and idempotent.
static std::vector<uint8_t> native_ss_replay_stream(const gx::GXState& g) {
    std::vector<uint8_t> o;
    for (uint32_t r : {0x30u, 0x40u, 0x50u, 0x60u}) { o.push_back(0x08); o.push_back(uint8_t(r)); native_ss_put32(o, g.cp[r]); }
    for (uint32_t r = 0x70; r < 0xC0; ++r) { if ((r & 0xF) >= 8 && r < 0xA0) continue; o.push_back(0x08); o.push_back(uint8_t(r)); native_ss_put32(o, g.cp[r]); }
    {   // XF registers 0x1000-0x1057
        o.push_back(0x10); native_ss_put32(o, ((0x58u - 1) << 16) | 0x1000u);
        for (uint32_t i = 0; i < 0x58; ++i) native_ss_put32(o, g.xf[i]);
    }
    native_ss_xf_floats(o, 0x000, g.posMatrices, 256);
    native_ss_xf_floats(o, 0x400, g.normalMatrices, 96);
    native_ss_xf_floats(o, 0x500, g.postMatrices, 256);
    native_ss_xf_floats(o, 0x600, &g.lights[0][0], 128);
    for (uint32_t r = 0; r < 0x100; ++r) {
        switch (r) { case 0x45: case 0x47: case 0x48: case 0x52: case 0x63: case 0x64: case 0x65: case 0x66: case 0x67: case 0xFE: continue; }
        o.push_back(0x61); native_ss_put32(o, (r << 24) | (g.bp[r] & 0xFFFFFF));
    }
    return o;
}

void native_ss_gx(native_ss::Stream& s) {
    s.tag("GX01");
    s.pod(gx::g_state);
    s.vec(s_parse_buf);
    s.pod(s_early_bp_shadow); s.pod(s_early_bp_mask);
    s.pod(g_wpar_cur); s.pod(g_wpar_fill); s.pod(g_wpar_redirect);
    if (!s.loading) return;
    std::vector<uint8_t> stream = native_ss_replay_stream(gx::g_state);
    stream.insert(stream.end(), s_parse_buf.begin(), s_parse_buf.end());
    s_parse_buf.clear();
    {
        nwii::runtime::NativeFifoLockGuard lock(g_fifo_mutex);
        stream.insert(stream.end(), g_hw_fifo.begin(), g_hw_fifo.end());
        g_hw_fifo.swap(stream);
        g_gx_pending_bytes.store(g_hw_fifo.size(), std::memory_order_relaxed);
    }
    g_cpu_fifo.clear();
    g_native_fifo_ok = false;
    native_gather_invalidate();
    NativeRequestGxDrain();
}
}
'''

MAIN_KEY_OLD = '          else if (e.type == SDL_KEYDOWN && !e.key.repeat) native_hud_gl::key(e.key.keysym.sym); // NATIVE_DEV_HUD_V1'
MAIN_KEY_NEW = '''          else if (e.type == SDL_KEYDOWN && !e.key.repeat && e.key.keysym.sym == SDLK_F1) { // NATIVE_SAVESTATE_V1: save a state
            int expected = 0;
            if (native_ss::request.compare_exchange_strong(expected, 1)) {
              { std::lock_guard<std::mutex> lock(native_ss::thumb.mutex); native_ss::thumb.want = true; native_ss::thumb.ready = false; native_ss::thumb.bmp.clear(); }
              std::cout << "[SaveState] requested" << std::endl;
            }
          }
''' + MAIN_KEY_OLD
MAIN_PRESENT_OLD = '        native_hud_gl::draw(draw_w, draw_h); // NATIVE_DEV_HUD_V1\n'
MAIN_PRESENT_NEW = '''        { // NATIVE_SAVESTATE_V1: thumbnail of the presented frame (before the HUD), 256 pixels wide, 24-bit BMP
          bool want = false;
          { std::lock_guard<std::mutex> lock(native_ss::thumb.mutex); want = native_ss::thumb.want; }
          if (want && draw_w > 0 && draw_h > 0) {
            std::vector<uint8_t> px(size_t(draw_w) * draw_h * 3);
            GLint read_fb = 0; glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read_fb);
            glBindFramebuffer(GL_READ_FRAMEBUFFER, 0); glReadBuffer(GL_BACK);
            glPixelStorei(GL_PACK_ALIGNMENT, 1);
            glReadPixels(0, 0, draw_w, draw_h, GL_BGR, GL_UNSIGNED_BYTE, px.data());
            glBindFramebuffer(GL_READ_FRAMEBUFFER, read_fb);
            const int tw = 256, th = std::max(1, int(int64_t(draw_h) * tw / draw_w)), row = (tw * 3 + 3) & ~3;
            std::vector<uint8_t> bmp(54 + size_t(row) * th);
            auto put = [&](size_t at, uint32_t v, int n) { for (int i = 0; i < n; ++i) bmp[at + i] = uint8_t(v >> (8 * i)); };
            bmp[0] = 'B'; bmp[1] = 'M'; put(2, uint32_t(bmp.size()), 4); put(10, 54, 4); put(14, 40, 4); put(18, tw, 4); put(22, th, 4);
            put(26, 1, 2); put(28, 24, 2); put(34, uint32_t(row) * th, 4);
            for (int y = 0; y < th; ++y)   // box filter; GL rows are bottom-up like BMP
              for (int x = 0; x < tw; ++x) {
                const int x0 = x * draw_w / tw, x1 = std::max(x0 + 1, (x + 1) * draw_w / tw);
                const int y0 = y * draw_h / th, y1 = std::max(y0 + 1, (y + 1) * draw_h / th);
                uint32_t sum[3] = {0, 0, 0}, n = 0;
                for (int yy = y0; yy < y1; yy += 2) for (int xx = x0; xx < x1; xx += 2) {
                  const uint8_t* q = &px[(size_t(yy) * draw_w + xx) * 3]; sum[0] += q[0]; sum[1] += q[1]; sum[2] += q[2]; ++n;
                }
                for (int c = 0; c < 3; ++c) bmp[54 + size_t(y) * row + x * 3 + c] = uint8_t(sum[c] / std::max(1u, n));
              }
            std::lock_guard<std::mutex> lock(native_ss::thumb.mutex);
            native_ss::thumb.bmp.swap(bmp); native_ss::thumb.ready = true; native_ss::thumb.want = false;
          }
        }
''' + MAIN_PRESENT_OLD
MAIN_THREAD_OLD = '  std::thread cpu_thread(cpu_thread_func, ctx.get());\n'
MAIN_THREAD_NEW = ('  if (const char* load_state = std::getenv("NWII_LOAD_STATE"); load_state && *load_state) native_ss::request.store(2); // '
                   + MARK + '\n' + MAIN_THREAD_OLD)

RUN_GAME = 'extern "C" void run_game(nwii::runtime::CPUContext& ctx) {'
LOOP_HEAD = '    while (ctx.is_running) {\n'
HOOK_LINE = ('      if (__builtin_expect(native_ss::request.load(std::memory_order_relaxed) != 0, 0)) nwii::runtime::native_ss_service(ctx); // '
             + MARK + '\n')
GENERATED_PRELUDE = (INC + 'namespace nwii::runtime { struct CPUContext; void native_ss_service(CPUContext&); }\n')


def gx_state_hook(t):
    """Repair the FIFO hook if a prior GX stage restored its body on replay."""
    if GX_BUSY_NEW not in t:
        t = replace_once(t, GX_BUSY_OLD, GX_BUSY_NEW, 'gx.cpp')
        t = replace_once(t, GX_LOCAL, '', 'gx.cpp')
    for needed in ('static uint32_t s_early_bp_shadow[256]', 'static uint32_t s_early_bp_mask', 'static uint32_t g_wpar_cur',
                   'static NativeCpuFifo g_cpu_fifo;', 'std::vector<uint8_t> g_hw_fifo;'):
        if needed not in t:
            raise Skip('gx.cpp: %s not found' % needed)
    if 'bool native_ss_gx_sync(int timeout_ms)' not in t:
        t = t.rstrip() + '\n' + GX_TAIL
    if INC not in t:t = INC + t
    return t


def plan():
    """Every (path, new text) this stage writes, computed before anything is written."""
    out = {}

    def edit(path, fn, refresh=False):
        if not path.is_file():
            raise Skip('missing ' + str(path))
        text, crlf = out[path] if path in out else read(path)
        if MARK in text and path not in out and not refresh:
            return                                  # already installed
        changed=fn(text)
        if changed != text:out[path] = (changed, crlf)

    for rel, body in HOOKS.items():
        edit(RT / rel, lambda t, b=body: append_hook(t, b))
    edit(RT / 'include/runtime/native_ax/protocol.h', lambda t: replace_once(t, PROTOCOL_OLD, PROTOCOL_NEW, 'protocol.h'))
    edit(RT / 'include/runtime/native_ax/base.h', lambda t: replace_once(t, BASE_OLD, BASE_NEW, 'base.h'))
    edit(RT / 'include/runtime/ios_kernel.h',
         lambda t: replace_once(replace_once(t, '#include <memory>\n', '#include <memory>\n#include <stdexcept>\n#include <string>\n', 'ios_kernel.h'),
                                KERNEL_OLD, KERNEL_NEW, 'ios_kernel.h'))

    edit(RT / 'src/hle/gx.cpp', gx_state_hook, refresh=True)

    def ios(t):
        for needed in ('static std::vector<PendingCallback> g_pending_callbacks;', 'static uint32_t ipc_pool_next', 'int g_ipc_interrupt_delay',
                       'static NativeWpadState& native_wpad_state()'):
            if needed not in t:
                raise Skip('ios.cpp: %s not found' % needed)
        return INC + t.rstrip() + '\n\n#include "native_savestate_ios.inc" // ' + MARK + '\n'
    edit(RT / 'src/hle/ios.cpp', ios)

    def main(t):
        t = replace_once(t, MAIN_KEY_OLD, MAIN_KEY_NEW, 'main.cpp')
        t = replace_once(t, MAIN_PRESENT_OLD, MAIN_PRESENT_NEW, 'main.cpp')
        t = replace_once(t, MAIN_THREAD_OLD, MAIN_THREAD_NEW, 'main.cpp')
        return INC + t
    edit(RT / 'src/core/main.cpp', main)

    def generated(t):
        a = t.find(RUN_GAME)
        if a < 0 or t.count(RUN_GAME) != 1:
            raise Skip('main_output.cpp: run_game not found once')
        b = t.find(LOOP_HEAD, a)
        if b < 0:
            raise Skip('main_output.cpp: run_game loop head not found')
        b += len(LOOP_HEAD)
        return GENERATED_PRELUDE + t[:b] + HOOK_LINE + t[b:]
    edit(GENERATED / 'main_output.cpp', generated)
    return out


def main():
    installs = [(PORT / 'runtime-overlays/native_savestate.h', RT / 'include/runtime/native_savestate.h'),
                (PORT / 'runtime-overlays/native_savestate_ios.inc', RT / 'src/hle/native_savestate_ios.inc')]
    for src, _ in installs:
        if not src.is_file():
            raise Skip('missing ' + str(src))
    writes = plan()
    for src, dst in installs:
        data = src.read_bytes()
        if not dst.exists() or dst.read_bytes() != data:
            dst.write_bytes(data)
    for path, (text, crlf) in writes.items():
        write(path, text, crlf)
    print('Save states installed: %d files edited, header and orchestration refreshed.' % len(writes))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--root':   # test hook: stage a copied tree
        RT = Path(sys.argv[2]) / 'nWiiRuntime'; GENERATED = Path(sys.argv[2]) / 'crash-generated'
    try:
        main()
    except Skip as reason:
        print('Save states not installed: ' + str(reason))
