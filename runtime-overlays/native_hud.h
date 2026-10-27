// Live frame-time accounting for the developer overlay (F3) and snapshot export (F4). No GL in here: native_hud_gl.h draws it.
#include "runtime/native_perf_census.h"
//
// Every native_trace::Scope reports its *exclusive* time (scope time minus child scopes) into a slot keyed by (scope name, thread) while the
// HUD is on. Frames are XFB copies (the game finishing a picture). Once per snapshot the slot deltas are turned into ms per frame, so the
// table answers "what is the frame spent on, on which thread" without a trace capture. Cost when off: one relaxed atomic load per scope.
#pragma once
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>
#include "native_stutter.h"
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace native_hud {
inline std::atomic<bool> on{false};
inline uint64_t now_us() {
  return uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());
}
struct Slot {
  const char* name = nullptr; unsigned tid = 0;
  std::atomic<uint64_t> self_us{0}, count{0};
  uint64_t last_self = 0, last_count = 0;   // snapshot baselines (snapshot thread only)
  uint64_t frame_self=0;double usual_ms=0;
};
inline std::mutex slot_mutex;
inline std::vector<Slot*> slots;            // append-only, slot addresses are stable
struct ScopeState;
struct SlotCache { const char* name=nullptr; Slot* slot=nullptr; };
struct ThreadState {
  ScopeState* current=nullptr;
  unsigned tid=0;
  SlotCache cache[64]{};
};
inline ThreadState& thread_state() {
#ifdef _WIN32
  // MinGW's C++ thread_local accessor uses emulated TLS and pthread bookkeeping.
  // One native lookup at scope entry supplies both the stack and slot cache.
  static const DWORD index=TlsAlloc();
  if(index!=TLS_OUT_OF_INDEXES) {
    if(auto* state=static_cast<ThreadState*>(TlsGetValue(index)))return *state;
  }
#endif
  // The CRT owns this storage and releases it at thread exit. Its emulated-TLS
  // accessor runs once per Windows thread; subsequent entries return above.
  // Native TLS allocation failure and other platforms retain this fallback.
  static thread_local ThreadState state;
#ifdef _WIN32
  if(!state.tid)state.tid=GetCurrentThreadId();
  if(index!=TLS_OUT_OF_INDEXES)TlsSetValue(index,&state);
#endif
  return state;
}
inline unsigned thread_id() {
#ifdef _WIN32
  return thread_state().tid;
#else
  return 0;
#endif
}
inline Slot* slot_for(const char* name,ThreadState& state) {
  SlotCache& c = state.cache[(reinterpret_cast<uintptr_t>(name) >> 3) & 63];
  if (c.name == name && c.slot) return c.slot;
  const unsigned tid = state.tid;
  std::lock_guard<std::mutex> lock(slot_mutex);
  for (Slot* s : slots) if (s->tid == tid && s->name == name) { c = {name, s}; return s; }
  Slot* s = new Slot(); s->name = name; s->tid = tid; slots.push_back(s); c = {name, s};
  return s;
}
inline Slot* slot_for(const char* name) {return slot_for(name,thread_state());}
// Per-scope exclusive timing. Embedded in native_trace::Scope.
struct ScopeState {
  uint64_t start = 0, child = 0; ScopeState* parent = nullptr; Slot* slot = nullptr;
  ThreadState* owner=nullptr;
  void begin(const char* name) {owner=&thread_state();slot=slot_for(name,*owner);parent=owner->current;owner->current=this;start=now_us();}
  void end() {
    if (!start) return;
    const uint64_t dur = now_us() - start, self = dur > child ? dur - child : 0;
#ifdef _WIN32
    // Windows slots are keyed by live thread ID and only that thread writes
    // these cumulative counters. Snapshots/reset read them without replacing
    // them; atomic publication protects readers without locked RMW operations.
    slot->self_us.store(slot->self_us.load(std::memory_order_relaxed)+self,std::memory_order_relaxed);
    slot->count.store(slot->count.load(std::memory_order_relaxed)+1,std::memory_order_relaxed);
#else
    // Non-Windows thread_id() currently returns zero, so its slots can have
    // several writers. Preserve atomic read-modify-write accounting there.
    slot->self_us.fetch_add(self,std::memory_order_relaxed);slot->count.fetch_add(1,std::memory_order_relaxed);
#endif
    if (parent) parent->child += dur;
    if (owner->current == this) owner->current = parent;
    start = 0;
  }
};

// ---- draw-state picker (state census in renderer_gl.cpp reads this; the overlay prints `text`)
struct Picker {
  std::atomic<int> mode{0};      // 0 off, 1 hide the selected state, 2 draw only the selected state
  std::atomic<int> step{0};      // +1 / -1 requested by F6 / F5, consumed by the renderer
  std::mutex mutex; std::string text;
};
inline Picker picker;
inline std::atomic<int> ind_debug{0};   // F8: indirect-texturing debug view (shader generation reads it; see indirect_codegen.inc)

// ---- frames
constexpr unsigned kRing = 480;
inline uint64_t frame_us[kRing] = {}; inline unsigned frame_n = 0;     // XFB-to-XFB intervals (written by the renderer thread)
inline std::atomic<uint64_t> frames_total{0}, presents_total{0}, fresh_total{0};
inline uint64_t last_frame_stamp = 0;
inline std::atomic<unsigned long long> render_cpu_us{0};                 // cumulative CPU of the thread that completes frames
#ifdef _WIN32
inline std::atomic<HANDLE> guest_thread_handle{nullptr};
inline uint64_t thread_cpu_us(HANDLE h) {
  FILETIME c, e, k, u; if (!h || !GetThreadTimes(h, &c, &e, &k, &u)) return 0;
  auto t = [](FILETIME f) { return (uint64_t(f.dwHighDateTime) << 32) | f.dwLowDateTime; };
  return (t(k) + t(u)) / 10;
}
#endif
inline unsigned guest_tid = 0, render_tid = 0;
inline native_stutter::History stutters;
inline std::atomic<uint64_t> work_vertices{0},work_packed{0},work_upload{0},work_draws{0},work_checks{0},work_misses{0};
inline std::atomic<uint64_t> work_shader_compiles{0},work_shader_binary_hits{0},work_shader_source_hits{0};
inline uint64_t previous_guest_cpu=0,previous_render_cpu=0;
struct WaitAccumulator {
 std::atomic<uint64_t> kinds[3]{};std::atomic<uint64_t> total{0},maximum{0},late{0};
 static void max(std::atomic<uint64_t>& target,uint64_t value){uint64_t old=target.load(std::memory_order_relaxed);while(old<value && !target.compare_exchange_weak(old,value,std::memory_order_relaxed)){} }
 void add(unsigned kind,uint64_t elapsed,uint64_t oversleep){kinds[std::min(kind,2u)].fetch_add(1,std::memory_order_relaxed);total.fetch_add(elapsed,std::memory_order_relaxed);max(maximum,elapsed);max(late,oversleep);}
 native_stutter::WaitStats consume(){return {kinds[0].exchange(0),kinds[1].exchange(0),kinds[2].exchange(0),total.exchange(0),maximum.exchange(0),late.exchange(0)};}
};
inline WaitAccumulator guest_waits,field_waits;
inline std::mutex guest_context_mutex;inline native_stutter::GuestContext guest_context;
inline void record_wait(bool guest,unsigned kind,uint64_t elapsed,uint64_t late){if(on.load(std::memory_order_relaxed))(guest?guest_waits:field_waits).add(kind,elapsed,late);}
inline void observe_guest_context(const native_stutter::GuestContext& context){std::lock_guard<std::mutex> lock(guest_context_mutex);guest_context=context;}
inline void reset_stutters(){
 stutters.reset();previous_guest_cpu=previous_render_cpu=0;
 work_vertices=0;work_packed=0;work_upload=0;work_draws=0;work_checks=0;work_misses=0;
 work_shader_compiles=0;work_shader_binary_hits=0;work_shader_source_hits=0;
 guest_waits.consume();field_waits.consume();{std::lock_guard<std::mutex> lock(guest_context_mutex);guest_context={};}
 std::lock_guard<std::mutex> lock(slot_mutex);for(auto* s:slots){s->frame_self=s->self_us.load();s->usual_ms=0;}
}
inline void work_decoded(uint64_t vertices,uint64_t packed){if(on.load(std::memory_order_relaxed)){work_vertices.fetch_add(vertices,std::memory_order_relaxed);work_packed.fetch_add(packed,std::memory_order_relaxed);}}
inline void work_uploaded(uint64_t bytes){if(on.load(std::memory_order_relaxed))work_upload.fetch_add(bytes,std::memory_order_relaxed);}
inline void work_draw(){if(on.load(std::memory_order_relaxed))work_draws.fetch_add(1,std::memory_order_relaxed);}
inline void work_texture(bool miss){if(on.load(std::memory_order_relaxed))(miss?work_misses:work_checks).fetch_add(1,std::memory_order_relaxed);}
inline void work_shader(unsigned kind){if(on.load(std::memory_order_relaxed))(kind==0?work_shader_compiles:kind==1?work_shader_binary_hits:work_shader_source_hits).fetch_add(1,std::memory_order_relaxed);}
inline void register_guest_thread() {
#ifdef _WIN32
  guest_thread_handle.store(OpenThread(THREAD_QUERY_LIMITED_INFORMATION, FALSE, GetCurrentThreadId()));
  guest_tid = GetCurrentThreadId();
#endif
}
inline void frame() {                       // call once per completed XFB copy, on the renderer thread
  native_perf_census::Frame();
  if (!on.load(std::memory_order_relaxed)) { last_frame_stamp = 0; return; }
  render_tid = thread_id();
  const uint64_t t = now_us();
  uint64_t gcpu=0,rcpu=0;
#ifdef _WIN32
  gcpu=thread_cpu_us(guest_thread_handle.load());rcpu=thread_cpu_us(GetCurrentThread());
#endif
  native_stutter::Work w{work_vertices.exchange(0),work_packed.exchange(0),work_upload.exchange(0),work_draws.exchange(0),work_checks.exchange(0),work_misses.exchange(0),work_shader_compiles.exchange(0),work_shader_binary_hits.exchange(0),work_shader_source_hits.exchange(0)};
  w.guest_wait=guest_waits.consume();w.field_wait=field_waits.consume();{std::lock_guard<std::mutex> lock(guest_context_mutex);w.guest_context=guest_context;}
  std::array<native_stutter::Cost,128> costs{};size_t nc=0;
  {std::lock_guard<std::mutex> lock(slot_mutex);for(auto* s:slots){uint64_t total=s->self_us.load();double ms=double(total-s->frame_self)/1000.0;s->frame_self=total;
    if(nc<costs.size())costs[nc++]={s->name,s->tid==guest_tid?'G':s->tid==render_tid?'R':'h',ms,s->usual_ms};}}
  bool incident=false;
  if(last_frame_stamp)incident=stutters.observe({t,double(t-last_frame_stamp)/1000.0,previous_guest_cpu?double(gcpu-previous_guest_cpu)/1000.0:0,previous_render_cpu?double(rcpu-previous_render_cpu)/1000.0:0,w},costs.data(),nc);
  {std::lock_guard<std::mutex> lock(slot_mutex);for(size_t i=0;i<nc;++i)if(!incident)slots[i]->usual_ms+=(costs[i].ms-slots[i]->usual_ms)*(stutters.ready()?0.02:0.15);}
  previous_guest_cpu=gcpu;previous_render_cpu=rcpu;
  if (last_frame_stamp) { frame_us[frame_n % kRing] = t - last_frame_stamp; ++frame_n; }
  last_frame_stamp = t; frames_total.fetch_add(1, std::memory_order_relaxed);
#ifdef _WIN32
  render_cpu_us.store(thread_cpu_us(GetCurrentThread()), std::memory_order_relaxed);
#endif
}
inline void present(bool fresh) {
  if (!on.load(std::memory_order_relaxed)) return;
  presents_total.fetch_add(1, std::memory_order_relaxed); if (fresh) fresh_total.fetch_add(1, std::memory_order_relaxed);
}

// ---- snapshots
struct Row { std::string name; unsigned tid; double ms_per_frame; double calls_per_frame; };
struct Snapshot {
  double window_s = 0, frames = 0;
  double new_fps = 0, present_fps = 0, xfb_fps = 0;
  double guest_cpu_ms = 0, render_cpu_ms = 0;       // CPU per frame
  double avg_ms = 0, p50_ms = 0, p95_ms = 0, p99_ms = 0, max_ms = 0;
  std::vector<double> recent;                        // last frame intervals, ms
  std::vector<Row> rows;                             // exclusive ms per frame, sorted
  unsigned guest_tid = 0;
  bool valid = false;
};
inline Snapshot current;                              // last computed snapshot
inline uint64_t snap_stamp = 0, snap_frames = 0, snap_presents = 0, snap_fresh = 0, snap_guest_cpu = 0, snap_render_cpu = 0;
inline void compute(Snapshot& s, bool restart_window) {
  const uint64_t now = now_us();
  const uint64_t frames = frames_total.load(), presents = presents_total.load(), fresh = fresh_total.load();
  uint64_t guest_cpu = 0;
#ifdef _WIN32
  guest_cpu = thread_cpu_us(guest_thread_handle.load());
#endif
  uint64_t render_cpu = render_cpu_us.load();
#ifdef _WIN32
  // Snapshot computation runs on the render owner. Sample its CPU now, so
  // enabling F3 cannot charge earlier lifetime CPU from a stale XFB sample.
  render_cpu = thread_cpu_us(GetCurrentThread());
#endif
  if (!snap_stamp) {                                     // first call only establishes baselines
    snap_stamp = now; snap_frames = frames; snap_presents = presents; snap_fresh = fresh; snap_guest_cpu = guest_cpu; snap_render_cpu = render_cpu;
    for (Slot* sl : slots) { sl->last_self = sl->self_us.load(); sl->last_count = sl->count.load(); }
    return;
  }
  const double win = double(now - snap_stamp) / 1e6, df = double(frames - snap_frames);
  if (win < 0.5) return;                                 // thread CPU time is only reported in 15.6 ms ticks: short windows are noise
  s.window_s = win; s.frames = df; s.xfb_fps = df / win;
  s.present_fps = double(presents - snap_presents) / win; s.new_fps = double(fresh - snap_fresh) / win;
  const double fr = df > 0 ? df : 1;
  s.guest_cpu_ms = double(guest_cpu - snap_guest_cpu) / 1000.0 / fr; s.render_cpu_ms = double(render_cpu - snap_render_cpu) / 1000.0 / fr;
  s.rows.clear();
  { std::lock_guard<std::mutex> lock(slot_mutex);
    for (Slot* sl : slots) {
      const uint64_t a = sl->self_us.load(), c = sl->count.load();
      if (a > sl->last_self) s.rows.push_back({sl->name, sl->tid, double(a - sl->last_self) / 1000.0 / fr, double(c - sl->last_count) / fr});
      if (restart_window) { sl->last_self = a; sl->last_count = c; }
    } }
  std::sort(s.rows.begin(), s.rows.end(), [](const Row& a, const Row& b) { return a.ms_per_frame > b.ms_per_frame; });
  // frame interval statistics over the last 240 frames
  const unsigned n = std::min<unsigned>(frame_n, 240);
  s.recent.clear(); std::vector<double> v;
  for (unsigned i = 0; i < n; ++i) { const double ms = double(frame_us[(frame_n - n + i) % kRing]) / 1000.0; s.recent.push_back(ms); v.push_back(ms); }
  if (!v.empty()) {
    std::sort(v.begin(), v.end()); double sum = 0; for (double x : v) sum += x;
    auto pct = [&](double p) { return v[std::min<size_t>(v.size() - 1, size_t(p * (v.size() - 1) + 0.5))]; };
    s.avg_ms = sum / v.size(); s.p50_ms = pct(0.5); s.p95_ms = pct(0.95); s.p99_ms = pct(0.99); s.max_ms = v.back();
  }
  s.guest_tid = guest_tid; s.valid = true;
  if (restart_window) { snap_stamp = now; snap_frames = frames; snap_presents = presents; snap_fresh = fresh; snap_guest_cpu = guest_cpu; snap_render_cpu = render_cpu; }
}
inline Snapshot last_long;                              // the last completed >= 4 s window (what an export falls back to right after a restart)
inline void refresh() {                                 // called about twice a second by the overlay
  Snapshot s = current; compute(s, false);
  if (s.valid) { current = s; if (s.window_s >= 4.0) { last_long = s; Snapshot t = s; compute(t, true); } }
}
}  // namespace native_hud
