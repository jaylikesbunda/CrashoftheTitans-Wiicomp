"""Developer stats overlay: F3 toggles a live frame-time breakdown, F4 exports the current numbers (txt + json + clipboard).

native_hud.h (accounting, no GL) is included by native_frame_trace.h so every native_trace::Scope can report its exclusive time while the
overlay is on (cost when off: one relaxed atomic load). native_hud_gl.h (GDI-rasterised font, tiny GL batch renderer, report/export) is
included by main.cpp, which also forwards key presses, feeds present counts and calls draw() just before the buffer swap. NWII_HUD=1 starts
with the overlay on. All GL state the overlay touches is saved and restored because the renderer caches GL state. Idempotent.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime'
MARK = 'NATIVE_DEV_HUD_V1'


def copy_header(name):
    src = (p / 'runtime-overlays' / name).read_text()
    dst = rt / 'include/runtime' / name
    if not dst.exists() or dst.read_text() != src:
        dst.write_text(src)


def apply(path, old, new, probe=None):
    s = path.read_text(errors='surrogateescape')
    if (probe or new) in s:
        return
    assert s.count(old) == 1, (path.name, old[:80], s.count(old))
    path.write_text(s.replace(old, new, 1), errors='surrogateescape')


copy_header('native_perf_census.h'); copy_header('native_perf_census_export.h')
copy_header('native_guest_entry.h'); copy_header('native_frame_limit.h'); copy_header('native_pipeline_stats.h'); copy_header('native_geometry_stats.h'); copy_header('native_stutter.h'); copy_header('native_hud.h'); copy_header('native_guest_region_stats.h'); copy_header('native_hud_gl.h')

t = rt / 'include/runtime/native_frame_trace.h'
apply(t, '#include <iomanip>\n', '#include <iomanip>\n#include "runtime/native_hud.h" // ' + MARK + '\n', '#include "runtime/native_hud.h"')
apply(t, '  const char* name;uint64_t start=0;unsigned id=0,weight=1;Buffer* owner=nullptr;\n  explicit Scope(const char* n,unsigned w=1):name(n),weight(w) {\n    if(n && *n && active.load(std::memory_order_relaxed)){',
      '  const char* name;uint64_t start=0;unsigned id=0,weight=1;Buffer* owner=nullptr;native_hud::ScopeState hud; // ' + MARK + '\n'
      '  explicit Scope(const char* n,unsigned w=1):name(n),weight(w) {\n'
      '    if(n && *n && native_hud::on.load(std::memory_order_relaxed))hud.begin(n);\n'
      '    if(n && *n && active.load(std::memory_order_relaxed)){', 'native_hud::ScopeState hud;')
apply(t, '  void close(){if(start)record(', '  void close(){hud.end();if(start)record(', 'void close(){hud.end();')
apply(t, 'inline void guest_thread() {\n  if(!enabled())return;', 'inline void guest_thread() {\n  native_hud::register_guest_thread(); // ' + MARK + '\n  if(!enabled())return;', 'native_hud::register_guest_thread();')
apply(t, 'inline void boundary(uint64_t copies) {\n  if(!enabled())return;', 'inline void boundary(uint64_t copies) {\n  native_hud::frame(); // ' + MARK + '\n  if(!enabled())return;', 'native_hud::frame();')

m = rt / 'src/core/main.cpp'
apply(m, '#include "runtime/native_frame_trace_gl.h"\n', '#include "runtime/native_frame_trace_gl.h"\n#include "runtime/native_hud_gl.h" // ' + MARK + '\n', '#include "runtime/native_hud_gl.h"')
apply(m, '          if (e.type == SDL_QUIT) quit = true;\n',
      '          if (e.type == SDL_QUIT) quit = true;\n          else if (e.type == SDL_KEYDOWN && !e.key.repeat) native_hud_gl::key(e.key.keysym.sym); // ' + MARK + '\n',
      'native_hud_gl::key(e.key.keysym.sym)')
apply(m, '        if(std::getenv("NWII_OFFSCREEN")==nullptr) { native_trace::Scope trace_present("host.present"); SDL_GL_SwapWindow(window); }\n',
      '        native_hud_gl::draw(draw_w, draw_h); // ' + MARK + '\n'
      '        if(std::getenv("NWII_OFFSCREEN")==nullptr) { native_trace::Scope trace_present("host.present"); SDL_GL_SwapWindow(window); }\n',
      'native_hud_gl::draw(draw_w, draw_h);')
apply(m, '          ++pm_presents; pm_new+=fresh?1:0;\n', '          ++pm_presents; pm_new+=fresh?1:0; native_hud::present(fresh); // ' + MARK + '\n', 'native_hud::present(fresh);')
apply(m, '  std::cout << "[VI cadence] nominal field_ns=" << field_period.count() << std::endl;\n',
      '  std::cout << "[VI cadence] nominal field_ns=" << field_period.count() << std::endl;\n'
      '  { // ' + MARK + '\n'
      '    native_hud_gl::budget_ms = double(field_period.count()) / 1e6;\n'
      '    if (!headless) { const auto* gr = glGetString(GL_RENDERER); native_hud_gl::extra_info = std::string("GPU ") + (gr ? reinterpret_cast<const char*>(gr) : "?") + " | internal " + std::to_string(NativeResolutionScale()) + "x"; }\n'
      '    if (std::getenv("NWII_HUD")) native_hud_gl::set_on(true);\n'
      '  }\n', 'native_hud_gl::budget_ms = double(field_period.count())')
print('dev HUD installed')
