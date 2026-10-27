// The four state setters the raster-shadow stage tracks are called as (glX)(...) on purpose: the shadow stage rewrites plain glX( calls in
// source files and asserts on header writers. This overlay saves and restores every state it touches, so the shadow stays valid.
// Developer overlay (F3) and snapshot export (F4) for the native port. Included by main.cpp only.
// Font: rasterised from Consolas with GDI at start-up (no font data in the repo). Everything GL touches is saved and restored, because the
// renderer caches GL state and must not see it change underneath it.
#pragma once
#include "runtime/native_guest_region_stats.h"
#include "runtime/native_geometry_stats.h"
#include "runtime/native_pipeline_stats.h"
#include "runtime/native_perf_census_export.h"
#include "runtime/native_frame_limit.h"
#include "runtime/native_guest_entry.h"
#include "runtime/native_hud.h"
#include "runtime/native_gx_work_metrics.h"
#include "runtime/native_gl_texture_shadow.h"
#include "runtime/native_geometry_watch.h"
#include "runtime/native_flush_census.h"
#include "runtime/native_gx_batching.h"
#include "runtime/gx/command_stream.h"
#include <glad/glad.h>
#include <SDL.h>
#include <cstdarg>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <string>
#include <vector>
#include <cmath>

namespace native_hud_gl {
inline double budget_ms = 20.0;           // video field period of the region (PAL 20 ms, NTSC 16.68 ms)
inline std::string toast; inline uint64_t toast_until = 0;
inline std::string gl_renderer, extra_info;
inline bool detailed = false; // F10: per-call costs and the last incident's work/cost comparison.

struct Font { int cw = 9, ch = 18, pixels = 0; GLuint tex = 0; bool ok = false; };
inline Font font;
struct Gpu { GLuint prog = 0, vao = 0, vbo = 0; GLint u_view = -1; bool ready = false; };
inline Gpu gpu;

inline bool init_font(int pixels = 15) {
#ifdef _WIN32
  HDC dc = CreateCompatibleDC(nullptr);
  HFONT f = CreateFontA(-pixels, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                        ANTIALIASED_QUALITY, FIXED_PITCH | FF_MODERN, "Consolas");
  HGDIOBJ old = SelectObject(dc, f);
  TEXTMETRICA tm{}; GetTextMetricsA(dc, &tm);
  font.cw = std::max<int>(6, tm.tmAveCharWidth); font.ch = std::max<int>(10, tm.tmHeight);
  const int cols = 16, rows = 6, W = cols * font.cw, H = rows * font.ch;
  BITMAPINFO bi{}; bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER); bi.bmiHeader.biWidth = W; bi.bmiHeader.biHeight = -H;
  bi.bmiHeader.biPlanes = 1; bi.bmiHeader.biBitCount = 32; bi.bmiHeader.biCompression = BI_RGB;
  void* bits = nullptr; HBITMAP bmp = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
  if (!bmp || !bits) { SelectObject(dc, old); DeleteObject(f); DeleteDC(dc); return false; }
  HGDIOBJ oldbmp = SelectObject(dc, bmp);
  std::memset(bits, 0, size_t(W) * H * 4);
  SetBkMode(dc, TRANSPARENT); SetTextColor(dc, RGB(255, 255, 255));
  for (int c = 32; c < 127; ++c) { const int i = c - 32; char ch = char(c); TextOutA(dc, (i % cols) * font.cw, (i / cols) * font.ch, &ch, 1); }
  GdiFlush();
  std::vector<unsigned char> a(size_t(W) * H);
  const unsigned char* p = static_cast<const unsigned char*>(bits);
  for (size_t i = 0; i < a.size(); ++i) a[i] = p[i * 4 + 1];              // grey-scale AA: green channel
  // cell 95 (character 127) is a solid block used for rectangles
  for (int y = 0; y < font.ch; ++y) for (int x = 0; x < font.cw; ++x) a[size_t((5 * font.ch + y)) * W + (15 * font.cw + x)] = 255;
  SelectObject(dc, oldbmp); DeleteObject(bmp); SelectObject(dc, old); DeleteObject(f); DeleteDC(dc);
  GLint unpack = 4; glGetIntegerv(GL_UNPACK_ALIGNMENT, &unpack); glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
  GLint bound = 0; glGetIntegerv(GL_TEXTURE_BINDING_2D, &bound);
  if(!font.tex)glGenTextures(1, &font.tex); glBindTexture(GL_TEXTURE_2D, font.tex);
  glTexImage2D(GL_TEXTURE_2D, 0, GL_R8, W, H, 0, GL_RED, GL_UNSIGNED_BYTE, a.data());
  // NATIVE_HUD_CRISP_TEXT_V1: the atlas already contains grayscale antialiasing.
  // Draw it at its native pixel size; filtering a stretched small atlas blurs it.
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, 0);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
  glBindTexture(GL_TEXTURE_2D, GLuint(bound)); glPixelStorei(GL_UNPACK_ALIGNMENT, unpack);
  font.pixels = pixels; font.ok = true; return true;
#else
  return false;
#endif
}
inline bool init_gpu() {
  if (gpu.ready) return true;
  if (!font.ok && !init_font()) return false;
  const char* vs = "#version 330 core\nlayout(location=0) in vec2 p; layout(location=1) in vec2 t; layout(location=2) in vec4 c;\n"
                   "uniform vec2 uView; out vec2 vt; out vec4 vc;\nvoid main(){vt=t;vc=c;gl_Position=vec4(p.x/uView.x*2.0-1.0,1.0-p.y/uView.y*2.0,0.0,1.0);}\n";
  const char* fs = "#version 330 core\nuniform sampler2D uTex; in vec2 vt; in vec4 vc; out vec4 o;\nvoid main(){o=vec4(vc.rgb,vc.a*texture(uTex,vt).r);}\n";
  auto compile = [](GLenum type, const char* src) { GLuint s = glCreateShader(type); glShaderSource(s, 1, &src, nullptr); glCompileShader(s); return s; };
  GLuint v = compile(GL_VERTEX_SHADER, vs), f = compile(GL_FRAGMENT_SHADER, fs);
  gpu.prog = glCreateProgram(); glAttachShader(gpu.prog, v); glAttachShader(gpu.prog, f); glLinkProgram(gpu.prog);
  glDeleteShader(v); glDeleteShader(f);
  GLint ok = 0; glGetProgramiv(gpu.prog, GL_LINK_STATUS, &ok); if (!ok) return false;
  gpu.u_view = glGetUniformLocation(gpu.prog, "uView");
  GLint vao = 0, vbo = 0; glGetIntegerv(GL_VERTEX_ARRAY_BINDING, &vao); glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &vbo);
  glGenVertexArrays(1, &gpu.vao); glBindVertexArray(gpu.vao); glGenBuffers(1, &gpu.vbo); glBindBuffer(GL_ARRAY_BUFFER, gpu.vbo);
  const GLsizei stride = 8 * sizeof(float);
  glEnableVertexAttribArray(0); glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, stride, (void*)0);
  glEnableVertexAttribArray(1); glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, stride, (void*)(2 * sizeof(float)));
  glEnableVertexAttribArray(2); glVertexAttribPointer(2, 4, GL_FLOAT, GL_FALSE, stride, (void*)(4 * sizeof(float)));
  glBindVertexArray(GLuint(vao)); glBindBuffer(GL_ARRAY_BUFFER, GLuint(vbo));
  gpu.ready = true; return true;
}

struct Canvas {
  std::vector<float> v; float k = 1.f;
  void quad(float x0, float y0, float x1, float y1, float u0, float v0, float u1, float v1, const float c[4]) {
    const float q[6][8] = {{x0, y0, u0, v0, c[0], c[1], c[2], c[3]}, {x1, y0, u1, v0, c[0], c[1], c[2], c[3]}, {x1, y1, u1, v1, c[0], c[1], c[2], c[3]},
                           {x0, y0, u0, v0, c[0], c[1], c[2], c[3]}, {x1, y1, u1, v1, c[0], c[1], c[2], c[3]}, {x0, y1, u0, v1, c[0], c[1], c[2], c[3]}};
    const size_t begin=v.size();v.resize(begin+48);std::memcpy(v.data()+begin,q,sizeof(q));
  }
  void rect(float x0, float y0, float x1, float y1, const float c[4]) {
    const float W = 16.f * font.cw, H = 6.f * font.ch;
    const float u = (15.5f * font.cw) / W, w = (5.5f * font.ch) / H;      // centre of the solid cell
    quad(x0, y0, x1, y1, u, w, u, w, c);
  }
  float text(float x, float y, const std::string& s, const float c[4]) {
    x=std::round(x);y=std::round(y);
    const float W = 16.f * font.cw, H = 6.f * font.ch, cw = font.cw * k, ch = font.ch * k;
    for (char ch_ : s) {
      const int code = (ch_ >= 32 && ch_ < 127) ? ch_ - 32 : 0, cx = code % 16, cy = code / 16;
      if (ch_ != ' ') quad(x, y, x + cw, y + ch, cx * font.cw / W, cy * font.ch / H, (cx + 1) * font.cw / W, (cy + 1) * font.ch / H, c);
      x += cw;
    }
    return x;
  }
};
inline std::string fmt(const char* f, ...) {
  char b[256];va_list a,copy;va_start(a,f);va_copy(copy,a);
  const int n=vsnprintf(b,sizeof b,f,a);va_end(a);
  if(n<0){va_end(copy);return {};}
  if(size_t(n)>=sizeof b){std::string out(size_t(n),'\0');vsnprintf(out.data(),out.size()+1,f,copy);va_end(copy);return out;}
  va_end(copy);return b;
}
inline const char* role(const native_hud::Row& r) { return r.tid == native_hud::guest_tid ? "G" : r.tid == native_hud::render_tid ? "R" : "h"; }

inline uint64_t GxMetricFrames(){return native_perf_census::enabled?native_perf_census::totals[native_perf_census::Frames].load(std::memory_order_relaxed):native_hud::frames_total.load(std::memory_order_relaxed);}
inline void ResetGxWork(){native_gx_work::Reset(GxMetricFrames());native_texture_shadow::ResetBaseline();native_geometry_watch::ResetBaseline();native_flush_census::ResetBaseline();native_gx_batching::Reset();}
inline std::string TextureBindingReport(){
 const auto x=native_texture_shadow::GetStats();
 return fmt("Texture binding shadow requested/skipped: active %llu/%llu, texture %llu/%llu, sampler %llu/%llu (since recording/reset).\n",x.active_requests,x.active_skipped,x.texture_requests,x.texture_skipped,x.sampler_requests,x.sampler_skipped);
}
inline std::string GxArchitectureReport(){
 const auto w=native_geometry_watch::GetStats();const auto f=native_flush_census::GetStats();
 std::string out=fmt("Direct FIFO compact emission: %s. BP/CP packet counts exclude cached display-list replay; stream record counts include replay.\n",nwii::runtime::gx::NativeDirectFifoEnabled()?"enabled":"disabled (legacy heavy emission)");
 out+=fmt("GX batching: %s | early requests %llu, park-entry rearms %llu, smaller publications deferred %llu (counts, not proven time savings).\n",native_gx_batching::Name(),native_gx_batching::Value(native_gx_batching::EarlyRequests),native_gx_batching::Value(native_gx_batching::ParkRearms),native_gx_batching::Value(native_gx_batching::SmallSuppressed));
 out+=fmt("Geometry dirty watch: eligible queries %llu, clean hits %llu, avoided %.2f MiB, dirty/fallback compares %llu/%llu (since recording/reset).\n",w.eligible_queries,w.clean_hits,w.bytes_compare_avoided/1048576.0,w.dirty_compares,w.fallback_compares);
 static const char* names[]={"opening","topology","mode","matrix","BP","XF","other","drain-end"};
 out+="Batch flush reasons: requests / empty / submitted / vertices (since recording/reset).\n";
 for(unsigned i=0;i<native_flush_census::Count;++i)out+=fmt("  %s: %llu / %llu / %llu / %llu\n",names[i],f.requests[i],f.empty[i],f.submitted[i],f.vertices[i]);
 return out;
}
inline std::string GxArchitectureJson(){
 const auto w=native_geometry_watch::GetStats();const auto f=native_flush_census::GetStats();
 std::string out=fmt("{\"direct_fifo_compact\":%s,\"geometry_watch\":{\"eligible_queries\":%llu,\"clean_hits\":%llu,\"bytes_compare_avoided\":%llu,\"dirty_compares\":%llu,\"fallback_compares\":%llu},\"flush_reasons\":{",nwii::runtime::gx::NativeDirectFifoEnabled()?"true":"false",w.eligible_queries,w.clean_hits,w.bytes_compare_avoided,w.dirty_compares,w.fallback_compares);
 static const char* names[]={"opening","topology","mode","matrix","bp","xf","other","drain_end"};
 for(unsigned i=0;i<native_flush_census::Count;++i){if(i)out+=',';out+=fmt("\"%s\":{\"requests\":%llu,\"empty\":%llu,\"submitted\":%llu,\"vertices\":%llu}",names[i],f.requests[i],f.empty[i],f.submitted[i],f.vertices[i]);}
 return out+"},\"gx_batching\":"+fmt("{\"mode\":\"%s\",\"early_requests\":%llu,\"park_rearms\":%llu,\"small_suppressed\":%llu}}",native_gx_batching::Name(),native_gx_batching::Value(native_gx_batching::EarlyRequests),native_gx_batching::Value(native_gx_batching::ParkRearms),native_gx_batching::Value(native_gx_batching::SmallSuppressed));
}
// ---- report (shared by the overlay's numbers and the export)
inline std::string report_text(const native_hud::Snapshot& s) {
  std::string t;
  t += fmt("Crash of the Titans native port - frame statistics\n%s\n", extra_info.c_str());
  if(!native_hud::on.load(std::memory_order_relaxed))return t+"Timing unavailable with F3 hidden in this build; workload counts below remain valid.\n"+native_gx_work::Text(GxMetricFrames())+TextureBindingReport()+GxArchitectureReport()+native_perf_census::Text();
  t += fmt("new frames %.1f/s | presented %.1f/s | XFB copies %.1f/s | pacing budget %.2f ms (%.1f/s)\n", s.new_fps, s.present_fps, s.xfb_fps, budget_ms, 1000.0 / budget_ms);
  t += fmt("frame interval ms (XFB to XFB, last %zu): avg %.2f p50 %.2f p95 %.2f p99 %.2f max %.2f\n", s.recent.size(), s.avg_ms, s.p50_ms, s.p95_ms, s.p99_ms, s.max_ms);
  t += fmt("CPU per frame: guest thread %.2f ms | render thread %.2f ms\n", s.guest_cpu_ms, s.render_cpu_ms);
  t += "exclusive time per frame by scope ([G] guest thread, [R] render thread, [h] helper threads):\n";
  for (auto& r : s.rows) t += fmt("  [%s] %-28s %7.3f ms  %7.1f calls  %7.2f us/call\n", role(r), r.name.c_str(), r.ms_per_frame, r.calls_per_frame, r.calls_per_frame?1000.0*r.ms_per_frame/r.calls_per_frame:0.0);
  t += native_hud::stutters.report();
  t += fmt("Guest normal-entry/original fallback calls: %llu/%llu (batched lower bounds; <128 pending per category; since reset)\n",native_guest_entry::fast_calls.load(),native_guest_entry::resume_calls.load());
  t += fmt("Optimized guest continuation calls: %llu (since reset)\n",native_guest_entry::continuation_calls.load());
  t += fmt("Gameplay timestep: %llu updates | last %.3f ms | game %.3f s / observed %.3f s (active gameplay; since reset)\n",native_frame_limit::timestep_count.load(),native_frame_limit::last_dt.load()*1000.0,native_frame_limit::timestep_seconds.load(),native_frame_limit::ObservedSeconds());
  t += fmt("Decode scheduling since reset: serial/pool batches %llu/%llu | serial/pool vertices %llu/%llu | chunk jobs %llu | largest job %llu vertices / %.3f ms\n",native_pipeline::serial_batches.load(),native_pipeline::pool_batches.load(),native_pipeline::serial_vertices.load(),native_pipeline::pool_vertices.load(),native_pipeline::chunk_jobs.load(),native_pipeline::largest_vertices.load(),native_pipeline::largest_job_us.load()/1000.0);
  t += fmt("Drain requests: %llu (%llu coalesced) | consumed %llu | request-to-consume avg %.3f ms max %.3f ms | generation %llu\n",native_pipeline::requests.load(),native_pipeline::coalesced.load(),native_pipeline::drains.load(),native_pipeline::latency_us.load()/1000.0/std::max(uint64_t(1),native_pipeline::drains.load()),native_pipeline::max_latency_us.load()/1000.0,native_pipeline::last_generation.load());
  t += fmt("Callee effects: fast/fallback %llu/%llu | avoided stores/reloads %llu/%llu\n",native_pipeline::effect_fast.load(),native_pipeline::effect_fallback.load(),native_pipeline::saved_stores.load(),native_pipeline::saved_loads.load());
  t += fmt("Format loaders: %llu draws; retention ineligible: %llu (since F3/F9 reset)\n",native_geometry::format_draws.load(),native_geometry::retained_rejects.load());
  t += fmt("Geometry reuse since F3/F9 reset: decode hit/miss %llu/%llu | GPU hit/miss %llu/%llu | validated %.2f MiB | uploads avoided %.2f MiB\n",native_geometry::decoded_hits.load(),native_geometry::decoded_misses.load(),native_geometry::gpu_hits.load(),native_geometry::gpu_misses.load(),native_geometry::validated_bytes.load()/1048576.0,native_geometry::saved_upload_bytes.load()/1048576.0);
  t += fmt("\nGuest compiler SDK regions v1: fast %llu / legacy %llu calls since F3/F9 reset (overlay enabled only; counts are not CPU-time coverage)\n",(unsigned long long)native_guest_regions::fast_calls.load(),(unsigned long long)native_guest_regions::legacy_calls.load());
  t += native_gx_work::Text(GxMetricFrames());
  t += TextureBindingReport();
  t += GxArchitectureReport();
  t += native_perf_census::Text();
  return t;
}
inline std::string report_json(const native_hud::Snapshot& s) {
  std::string j = "{\"performance_census\":" + native_perf_census::Json() + ",";
  j += "\"gx_optimization_work\":"+native_gx_work::Json(GxMetricFrames())+",";
  j += "\"gx_architecture\":"+GxArchitectureJson()+",";
  j += fmt("\"timing_available\":%s,",native_hud::on.load(std::memory_order_relaxed)?"true":"false");
  j += fmt("\"new_fps\":%.3f,\"present_fps\":%.3f,\"xfb_fps\":%.3f,\"budget_ms\":%.3f,", s.new_fps, s.present_fps, s.xfb_fps, budget_ms);
  j += fmt("\"frame_ms\":{\"avg\":%.3f,\"p50\":%.3f,\"p95\":%.3f,\"p99\":%.3f,\"max\":%.3f},", s.avg_ms, s.p50_ms, s.p95_ms, s.p99_ms, s.max_ms);
  j += fmt("\"guest_cpu_ms_per_frame\":%.3f,\"render_cpu_ms_per_frame\":%.3f,\"window_s\":%.2f,\"frames\":%.0f,", s.guest_cpu_ms, s.render_cpu_ms, s.window_s, s.frames);
  j += "\"info\":\"" + extra_info + "\",\"recent_frame_ms\":[";
  for (size_t i = 0; i < s.recent.size(); ++i) j += fmt(i ? ",%.2f" : "%.2f", s.recent[i]);
  j += "],\"scopes\":[";
  for (size_t i = 0; i < s.rows.size(); ++i)
    j += fmt("%s{\"name\":\"%s\",\"thread\":\"%s\",\"ms_per_frame\":%.4f,\"calls_per_frame\":%.2f}", i ? "," : "", s.rows[i].name.c_str(), role(s.rows[i]), s.rows[i].ms_per_frame, s.rows[i].calls_per_frame);
  return j + "],\"guest_entry\":" + fmt("{\"fast_calls\":%llu,\"resume_calls\":%llu,\"continuation_calls\":%llu,\"counts_are_lower_bounds\":true,\"batch_size\":128},",native_guest_entry::fast_calls.load(),native_guest_entry::resume_calls.load(),native_guest_entry::continuation_calls.load()) + "\"gameplay_timestep\":" + fmt("{\"updates\":%llu,\"last_seconds\":%.9f,\"game_seconds\":%.6f,\"observed_seconds\":%.6f},",native_frame_limit::timestep_count.load(),native_frame_limit::last_dt.load(),native_frame_limit::timestep_seconds.load(),native_frame_limit::ObservedSeconds()) + "\"pipeline\":" + fmt("{\"serial_batches\":%llu,\"pool_batches\":%llu,\"chunk_jobs\":%llu,\"serial_vertices\":%llu,\"pool_vertices\":%llu,\"largest_job_vertices\":%llu,\"largest_job_us\":%llu,\"drain_requests\":%llu,\"coalesced\":%llu,\"drains\":%llu,\"drain_latency_us\":%llu,\"max_drain_latency_us\":%llu,\"generation\":%llu,\"effect_fast\":%llu,\"effect_fallback\":%llu,\"saved_stores\":%llu,\"saved_loads\":%llu},",native_pipeline::serial_batches.load(),native_pipeline::pool_batches.load(),native_pipeline::chunk_jobs.load(),native_pipeline::serial_vertices.load(),native_pipeline::pool_vertices.load(),native_pipeline::largest_vertices.load(),native_pipeline::largest_job_us.load(),native_pipeline::requests.load(),native_pipeline::coalesced.load(),native_pipeline::drains.load(),native_pipeline::latency_us.load(),native_pipeline::max_latency_us.load(),native_pipeline::last_generation.load(),native_pipeline::effect_fast.load(),native_pipeline::effect_fallback.load(),native_pipeline::saved_stores.load(),native_pipeline::saved_loads.load()) + "\"geometry_reuse\":" + fmt("{\"format_draws\":%llu,\"retained_rejects\":%llu,\"decode_hits\":%llu,\"decode_misses\":%llu,\"gpu_hits\":%llu,\"gpu_misses\":%llu,\"validated_bytes\":%llu,\"saved_upload_bytes\":%llu},",native_geometry::format_draws.load(),native_geometry::retained_rejects.load(),native_geometry::decoded_hits.load(),native_geometry::decoded_misses.load(),native_geometry::gpu_hits.load(),native_geometry::gpu_misses.load(),native_geometry::validated_bytes.load(),native_geometry::saved_upload_bytes.load()) + "\"guest_regions\":" + fmt("{\"schema\":1,\"permitted\":%s,\"fast_calls\":%llu,\"legacy_calls\":%llu,\"counts_since_reset\":true},",native_guest_regions::Enabled()?"true":"false",(unsigned long long)native_guest_regions::fast_calls.load(),(unsigned long long)native_guest_regions::legacy_calls.load()) + "\"stutter_watch\":" + native_hud::stutters.json() + "}\n";
}
inline void export_snapshot() {
  native_hud::ScopeState export_cost;if(native_hud::on.load())export_cost.begin("hud.export");
  struct EndExport{native_hud::ScopeState& scope;~EndExport(){scope.end();}} end_export{export_cost};
  native_hud::Snapshot s = native_hud::current;
  native_hud::compute(s, false);                         // fresh numbers for the window so far (does not disturb the overlay's window)
  if (!s.valid || s.window_s < 1.5) {                    // too short to trust the 15.6 ms thread-time ticks: use the last full window
    if (native_hud::last_long.valid) s = native_hud::last_long; else if (!s.valid) s = native_hud::current;
  }
  if (!s.valid) { toast = "no frames measured yet"; toast_until = native_hud::now_us() + 2500000; return; }
  const std::time_t now = std::time(nullptr); char stamp[32]; std::strftime(stamp, sizeof stamp, "%Y%m%d-%H%M%S", std::localtime(&now));
  const std::string base = std::string("frame-stats-") + stamp;
  const std::string text = report_text(s);
  std::string picked; { std::lock_guard<std::mutex> l(native_hud::picker.mutex); picked = native_hud::picker.text; }
  { std::ofstream f(base + ".txt"); f << text; if (!picked.empty()) f << "\npicked draw state:\n" << picked; }
  { std::ofstream f(base + ".json"); f << report_json(s); }
  SDL_SetClipboardText(text.c_str());
  std::printf("[HUD] exported %s.txt/.json (copied to clipboard)\n", base.c_str()); std::fflush(stdout);
  toast = "exported " + base + " (text copied to clipboard)"; toast_until = native_hud::now_us() + 4000000;
}
inline void set_on(bool v) {
  native_gx_work::SetCapture(v);
  if(v && !native_hud::on.load()){native_hud::reset_stutters();native_guest_regions::ResetStats();native_geometry::Reset();native_pipeline::Reset();native_frame_limit::ResetTimestep();native_guest_entry::Reset();ResetGxWork();}
  native_hud::on.store(v);
  if (v) { native_hud::snap_stamp = 0; native_hud::current = native_hud::Snapshot(); native_hud::last_frame_stamp = 0; native_hud::frame_n = 0; }
}
inline void key(SDL_Keycode k) {
  if (k == SDLK_F3) set_on(!native_hud::on.load());
  else if (k == SDLK_F4) export_snapshot();
  else if (k == SDLK_F9) {native_hud::reset_stutters();native_guest_regions::ResetStats();native_geometry::Reset();native_pipeline::Reset();native_frame_limit::ResetTimestep();native_guest_entry::Reset();native_perf_census::Reset();ResetGxWork();toast="stutter baseline reset";toast_until=native_hud::now_us()+2500000;}
  else if (k == SDLK_F10) {detailed=!detailed;}
  else if (k == SDLK_F6 || k == SDLK_F5) {                       // pick the next / previous draw state on screen
    auto& pk = native_hud::picker; if (pk.mode.load() == 0) pk.mode.store(1);
    pk.step.store(k == SDLK_F6 ? 1 : -1);
  } else if (k == SDLK_F8) {                                     // indirect-texturing debug view: 0 normal, 1 no warp, 2 hide indirect stages, 3 only indirect stages, 4 unfiltered indirect map
    const int m = (native_hud::ind_debug.load() + 1) % 5; native_hud::ind_debug.store(m);
    static const char* names[] = {"indirect: normal", "indirect: NO WARP (offset 0)", "indirect: indirect stages HIDDEN", "indirect: ONLY indirect stages", "indirect: map UNFILTERED"};
    toast = names[m]; toast_until = native_hud::now_us() + 3000000;
  } else if (k == SDLK_F7) {                                     // off -> hide selected -> solo selected -> off
    auto& pk = native_hud::picker; pk.mode.store((pk.mode.load() + 1) % 3);
    if (pk.mode.load() == 0) { std::lock_guard<std::mutex> l(pk.mutex); pk.text.clear(); }
    pk.step.store(0);
  }
}

inline void draw(int W, int H) {
  if (!native_hud::on.load(std::memory_order_relaxed)) return;
  static uint64_t next_refresh = 0;
  const uint64_t t = native_hud::now_us();
  if (!native_hud::guest_tid) { /* set by the guest thread via register_guest_thread */ }
  if (t >= next_refresh) { next_refresh = t + 500000; native_hud::refresh(); }
  const int font_pixels=std::clamp(int(std::lround(15.f*std::clamp(H/760.f,0.9f,2.4f))),14,28);
  if((!font.ok || font.pixels!=font_pixels) && !init_font(font_pixels))return;
  if (!init_gpu()) return;
  const native_hud::Snapshot& s = native_hud::current;

  // ---- save GL state
  GLint prog, vao, abuf, aunit, viewport[4], blendsrc_rgb, blenddst_rgb, blendsrc_a, blenddst_a, eq_rgb, eq_a, fbo;
  GLboolean blend = glIsEnabled(GL_BLEND), depth = glIsEnabled(GL_DEPTH_TEST), cull = glIsEnabled(GL_CULL_FACE), scissor = glIsEnabled(GL_SCISSOR_TEST), mask[4];
  glGetIntegerv(GL_CURRENT_PROGRAM, &prog); glGetIntegerv(GL_VERTEX_ARRAY_BINDING, &vao); glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &abuf);
  glGetIntegerv(GL_ACTIVE_TEXTURE, &aunit); glGetIntegerv(GL_VIEWPORT, viewport); glGetBooleanv(GL_COLOR_WRITEMASK, mask);
  glGetIntegerv(GL_BLEND_SRC_RGB, &blendsrc_rgb); glGetIntegerv(GL_BLEND_DST_RGB, &blenddst_rgb);
  glGetIntegerv(GL_BLEND_SRC_ALPHA, &blendsrc_a); glGetIntegerv(GL_BLEND_DST_ALPHA, &blenddst_a);
  glGetIntegerv(GL_BLEND_EQUATION_RGB, &eq_rgb); glGetIntegerv(GL_BLEND_EQUATION_ALPHA, &eq_a); glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &fbo);
  glActiveTexture(GL_TEXTURE0); GLint tex0 = 0, sampler0 = 0; glGetIntegerv(GL_TEXTURE_BINDING_2D, &tex0);
  glGetIntegeri_v(GL_SAMPLER_BINDING,0,&sampler0);

  // ---- build the frame
  // NATIVE_HUD_MESH_CACHE_V1: reuse capacity and redraw the mesh at 8 Hz.
  // Resize, detail mode, toast and picker actions invalidate it immediately.
  static Canvas c;static uint64_t built=0,last_toast=0;static int old_w=0,old_h=0,old_font=0,old_picker=-1,old_debug=-1;static bool old_detail=false;
  const int pick=native_hud::picker.mode.load(),debug=native_hud::ind_debug.load();
  const bool rebuild=t>=built+125000 || W!=old_w || H!=old_h || font.pixels!=old_font || old_detail!=detailed || last_toast!=toast_until || old_picker!=pick || old_debug!=debug;
  if(rebuild){
  built=t;old_w=W;old_h=H;old_font=font.pixels;old_detail=detailed;last_toast=toast_until;old_picker=pick;old_debug=debug;
  c.v.clear();if(c.v.capacity()<131072)c.v.reserve(131072);c.k=1.f;
  const float lh = font.ch * c.k, pad = 10 * c.k, x0 = 12 * c.k, y0 = 12 * c.k, pw = font.cw * c.k * (detailed?72:62) + pad * 2;
  const float white[4] = {0.93f, 0.94f, 0.97f, 1}, dim[4] = {0.58f, 0.62f, 0.70f, 1}, green[4] = {0.30f, 0.87f, 0.50f, 1}, amber[4] = {1.0f, 0.70f, 0.25f, 1},
              red[4] = {0.97f, 0.40f, 0.40f, 1}, bg[4] = {0.035f, 0.045f, 0.06f, 0.93f}, accent[4] = {1.0f, 0.54f, 0.17f, 1}, grey[4] = {0.16f, 0.20f, 0.26f, 0.85f};
  const unsigned fixed_lines=detailed?26:14;
  const float graph_h = 46 * c.k;
  const size_t room=size_t(std::max(1.f,std::floor((H-y0-12-pad*2-graph_h-8)/lh)-fixed_lines));
  const size_t nrows = std::min<size_t>(s.rows.size(), std::min<size_t>(detailed?24:14,room));
  const float height = pad * 2 + lh * (fixed_lines + nrows) + graph_h + 8 * c.k;
  c.rect(x0, y0, x0 + pw, y0 + height, bg); c.rect(x0, y0, x0 + 3 * c.k, y0 + height, accent);
  float y = y0 + pad; const float x = x0 + pad + 4 * c.k;
  c.text(x, y, "DEV STATS", accent); c.text(x + font.cw * c.k * 20, y, "F3 hide  F4 export  F10 details", dim);
  if (native_hud::ind_debug.load()) c.text(x + font.cw * c.k * 52, y + lh, fmt("IND %d", native_hud::ind_debug.load()), amber);
  y += lh;
  const bool on_target = s.new_fps >= (1000.0 / budget_ms) * 0.95;
  { float xx = c.text(x, y, fmt("NEW %5.1f", s.new_fps), on_target ? green : (s.new_fps >= 0.6 * 1000.0 / budget_ms ? amber : red));
    xx = c.text(xx, y, fmt("  PRESENT %5.1f", s.present_fps), white); c.text(xx, y, fmt("  XFB %5.1f  fps", s.xfb_fps), white); }
  y += lh;
  c.text(x, y, fmt("Frame ms   avg %5.1f   p95 %5.1f   p99 %5.1f", s.avg_ms, s.p95_ms, s.p99_ms), white); y += lh;
  c.text(x, y, fmt("           max %5.1f   budget %5.1f", s.max_ms, budget_ms), dim); y += lh;
  // frame-time graph (last 120 intervals)
  { const float gx = x, gw = pw - pad * 2 - 8 * c.k, gh = graph_h - 6 * c.k; const float top_ms = std::max(2.5 * budget_ms, s.max_ms * 1.05);
    c.rect(gx, y, gx + gw, y + gh, grey);
    const size_t n = std::min<size_t>(s.recent.size(), 120); const float bw = gw / 120.f;
    for (size_t i = 0; i < n; ++i) { const double ms = s.recent[s.recent.size() - n + i]; const float hh = float(std::min(1.0, ms / top_ms)) * gh;
      const float* col = ms <= budget_ms * 1.15 ? green : ms <= budget_ms * 2.15 ? amber : red;
      c.rect(gx + (120 - n + i) * bw, y + gh - hh, gx + (120 - n + i + 1) * bw - 0.5f, y + gh, col); }
    const float by = y + gh - float(budget_ms / top_ms) * gh; c.rect(gx, by, gx + gw, by + 1.5f * c.k, white);
    y += graph_h; }
  c.text(x, y, fmt("CPU/frame  guest %.1f ms   render %.1f ms", s.guest_cpu_ms, s.render_cpu_ms), white); y += lh;
  c.text(x, y, detailed?"Scope                        ms/frame calls/frame us/call":"Scope                        ms/frame", dim); y += lh;
  const double scale_ms = std::max(budget_ms * 1.5, s.rows.empty() ? 1.0 : s.rows.front().ms_per_frame);
  for (size_t i = 0; i < nrows; ++i) {
    const auto& r = s.rows[i];
    const float* col = r.tid == native_hud::guest_tid ? amber : r.tid == native_hud::render_tid ? green : white;
    std::string name = r.name.size() > 24 ? r.name.substr(0, 24) : r.name;
    c.text(x, y, fmt("[%s] %-24s %6.2f", role(r), name.c_str(), r.ms_per_frame), col);
    if(detailed)c.text(x+font.cw*38,y,fmt("%9.1f %8.2f",r.calls_per_frame,r.calls_per_frame?1000.0*r.ms_per_frame/r.calls_per_frame:0.0),dim);
    else {const float bx = x + font.cw * c.k * 38, bwid = pw - pad * 2 - font.cw * c.k * 38 - 8 * c.k;
    c.rect(bx, y + lh * 0.25f, bx + std::max(1.0f, float(r.ms_per_frame / scale_ms) * bwid), y + lh * 0.80f, col);}
    y += lh;
  }
  c.text(x,y,"[G] guest   [R] render   [h] helper",dim);
  y+=lh;
  {const std::string line=native_hud::stutters.summary();c.text(x,y,line.substr(0,60),amber);y+=lh;
   const std::string detail=native_hud::stutters.detail();c.text(x,y,detail.substr(0,60),dim);y+=lh;
   const auto* f=native_hud::stutters.latest_frame();
   const native_stutter::Work w=f?f->work:native_stutter::Work{};
   c.text(x,y,fmt("Last frame: %.1fk vertices  packed %.0f%%  upload %.2f MiB",w.vertices/1000.0,w.vertices?100.0*double(w.packed)/double(w.vertices):0.0,w.upload_bytes/1048576.0),white);y+=lh;
   c.text(x,y,fmt("Draws %llu   texture checks %llu   misses %llu",(unsigned long long)w.draws,(unsigned long long)w.texture_checks,(unsigned long long)w.texture_misses),dim);y+=lh;
   c.text(x,y,fmt("Shaders: compile %llu  binary hit %llu  source reuse %llu",(unsigned long long)w.shader_compiles,(unsigned long long)w.shader_binary_hits,(unsigned long long)w.shader_source_hits),dim);y+=lh;
   c.text(x,y,fmt("SDK regions fast %llu / legacy %llu calls since reset",(unsigned long long)native_guest_regions::fast_calls.load(),(unsigned long long)native_guest_regions::legacy_calls.load()),dim);y+=lh;
   c.text(x,y,"Elapsed scope times; helper threads overlap. F9 reset",dim);y+=lh;
   if(detailed){const auto* e=native_hud::stutters.latest();
    c.text(x,y,fmt("Decode batches serial/pool %llu/%llu chunk jobs %llu",native_pipeline::serial_batches.load(),native_pipeline::pool_batches.load(),native_pipeline::chunk_jobs.load()),dim);y+=lh;
    c.text(x,y,fmt("Largest decode job %.2f ms | drain latency max %.2f ms",native_pipeline::largest_job_us.load()/1000.0,native_pipeline::max_latency_us.load()/1000.0),dim);y+=lh;
    c.text(x,y,fmt("Callee effects fast/fallback %llu/%llu",native_pipeline::effect_fast.load(),native_pipeline::effect_fallback.load()),dim);y+=lh;
    c.text(x,y,"LAST INCIDENT: recorded increases, not proven cause",amber);y+=lh;
    c.text(x,y,e?fmt("CPU guest/render %.1f / %.1f ms   baseline %.1f ms",e->sample.guest_cpu,e->sample.render_cpu,e->baseline):"No incident recorded yet",dim);y+=lh;
    c.text(x,y,e?fmt("Vertices %llu vs %llu   draws %llu vs %llu",(unsigned long long)e->sample.work.vertices,(unsigned long long)e->usual.vertices,(unsigned long long)e->sample.work.draws,(unsigned long long)e->usual.draws):"F4 includes the last 240 frames and 16 incidents",dim);y+=lh;
    const auto& g=w.guest_wait;const auto& h=w.field_wait;
    c.text(x,y,fmt("Guest wakes evt/timer/err %llu/%llu/%llu max/late %.2f/%.2f ms",(unsigned long long)g.signal,(unsigned long long)g.timer,(unsigned long long)g.other,g.max_us/1000.0,g.max_late_us/1000.0),dim);y+=lh;
    c.text(x,y,fmt("Field wakes GX/poll/end %llu/%llu/%llu max/late %.2f/%.2f ms",(unsigned long long)h.signal,(unsigned long long)h.timer,(unsigned long long)h.other,h.max_us/1000.0,h.max_late_us/1000.0),dim);y+=lh;
    c.text(x,y,fmt("Idle sample PC %08x IRQ %08x | F4 thread wait queues",w.guest_context.pc,w.guest_context.irq),dim);y+=lh;
    for(unsigned j=0;j<3;++j){const native_stutter::Cost cost=e?e->increases[j]:native_stutter::Cost{};
     c.text(x,y,cost.name?fmt("[%c] %-24s %5.2f vs %5.2f (+%.2f) ms",cost.role,cost.name,cost.ms,cost.baseline,cost.ms-cost.baseline):"--",dim);y+=lh;}
   }}
  { std::string picked; { std::lock_guard<std::mutex> l(native_hud::picker.mutex); picked = native_hud::picker.text; }
    if (!picked.empty() && native_hud::picker.mode.load() != 0) {
      std::vector<std::string> lines; size_t a = 0; while (a < picked.size()) { size_t b = picked.find('\n', a); if (b == std::string::npos) b = picked.size(); lines.push_back(picked.substr(a, b - a)); a = b + 1; }
      const float ph = pad * 2 + lh * (lines.size() + 1), py = float(H) - ph - 12 * c.k, pwid = font.cw * c.k * 78 + pad * 2;
      c.rect(x0, py, x0 + pwid, py + ph, bg); c.rect(x0, py, x0 + 3 * c.k, py + ph, amber);
      float yy = py + pad; c.text(x + 0, yy, "DRAW-STATE PICKER   F6 next  F5 prev  F7 hide/solo/off", amber); yy += lh;
      for (auto& l : lines) { c.text(x, yy, l.size() > 78 ? l.substr(0, 78) : l, white); yy += lh; } } }
  if (t < toast_until) { const float tw = font.cw * c.k * float(toast.size()) + pad * 2; const float ty = float(H) - lh * 2.2f;
    c.rect(x0, ty, x0 + tw, ty + lh * 1.5f, bg); c.text(x0 + pad, ty + lh * 0.25f, toast, green); }

  } // cached mesh remains valid between refreshes

  // ---- draw
  glBindFramebuffer(GL_DRAW_FRAMEBUFFER, GLuint(fbo));
  glViewport(0,0,W,H);
  (glDisable)(GL_DEPTH_TEST); (glDisable)(GL_CULL_FACE); (glDisable)(GL_SCISSOR_TEST); (glEnable)(GL_BLEND);
  glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ONE, GL_ONE_MINUS_SRC_ALPHA); (glBlendEquation)(GL_FUNC_ADD);
  (glColorMask)(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
  glUseProgram(gpu.prog); glUniform2f(gpu.u_view, float(W), float(H)); glBindVertexArray(gpu.vao); glBindBuffer(GL_ARRAY_BUFFER, gpu.vbo);
  glBindTexture(GL_TEXTURE_2D, font.tex);
  glBindSampler(0,0); // use the atlas filter, not the game's water/texture sampler
  if(rebuild)glBufferData(GL_ARRAY_BUFFER, GLsizeiptr(c.v.size() * sizeof(float)), c.v.data(), GL_STREAM_DRAW);
  glDrawArrays(GL_TRIANGLES, 0, GLsizei(c.v.size() / 8));

  // ---- restore GL state
  glBindTexture(GL_TEXTURE_2D, GLuint(tex0)); glBindSampler(0,GLuint(sampler0)); glActiveTexture(GLenum(aunit));
  glBindBuffer(GL_ARRAY_BUFFER, GLuint(abuf)); glBindVertexArray(GLuint(vao)); glUseProgram(GLuint(prog));
  glBlendFuncSeparate(GLenum(blendsrc_rgb), GLenum(blenddst_rgb), GLenum(blendsrc_a), GLenum(blenddst_a)); glBlendEquationSeparate(GLenum(eq_rgb), GLenum(eq_a));
  if (!blend) (glDisable)(GL_BLEND); if (depth) (glEnable)(GL_DEPTH_TEST); if (cull) (glEnable)(GL_CULL_FACE); if (scissor) (glEnable)(GL_SCISSOR_TEST);
  (glColorMask)(mask[0], mask[1], mask[2], mask[3]); glViewport(viewport[0], viewport[1], viewport[2], viewport[3]);
}
}  // namespace native_hud_gl
