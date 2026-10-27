"""Always-on frame-rate counters that cannot disagree with Fraps (or each other) about what they count.

Three rates, measured on the host thread with no tracing needed:
  * present  - SDL_GL_SwapWindow calls per second (what Fraps reports; the host presents once per VI field: 50/s PAL, 59.94/s NTSC)
  * new      - presents that showed an XFB image not shown before (new game frames actually reaching the screen)
  * xfb      - XFB copies the game completed per second (the old 'completed frame' metric)
They go to the window title and stdout once per second, and to the frame trace as `host.present` marks (value: 1 when the image is new,
aux: VI field period in ns) so tools/analyze_frame_trace.py can report the same rates and a region-correct frame budget.
Idempotent.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime/src'
MARK = 'NATIVE_PRESENT_METRICS_V1'


def apply(path, old, new, probe):
    s = path.read_text(errors='surrogateescape')
    if probe in s:
        return
    if s.count(old) == 1:
        path.write_text(s.replace(old, new, 1), errors='surrogateescape')
        return
    # A later stage can rewrite one of these anchors (the XFB copy block is edited by the frame
    # trace and the generation gate); the stage's own marker then proves it has already run.
    if s.count(old) == 0 and MARK in s:
        return
    assert s.count(old) == 1, (path.name, old[:70], s.count(old))


r = rt / 'hle/gx/renderer_gl.cpp'
apply(r, 'struct NativeXfbFrame { GLuint texture=0,fbo=0; int width=0,height=0; };',
      'struct NativeXfbFrame { GLuint texture=0,fbo=0; int width=0,height=0; uint64_t generation=0; /* ' + MARK + ' */ };',
      'uint64_t generation=0; /* ' + MARK)
apply(r, '  ++s_native_xfb_copies;\n  native_trace::boundary(s_native_xfb_copies);',
      '  ++s_native_xfb_copies;\n  frame.generation=s_native_xfb_copies; // ' + MARK + '\n  native_trace::boundary(s_native_xfb_copies);',
      'frame.generation=s_native_xfb_copies;')
apply(r, 'uint64_t NativeXfbCopyCount(){return s_native_xfb_copies;}',
      'uint64_t NativeXfbCopyCount(){return s_native_xfb_copies;}\n'
      'uint64_t NativeXfbGeneration(uint32_t address){auto it=s_native_xfb.find(address&0x3fffffff);return it==s_native_xfb.end()?0:it->second.generation;} // ' + MARK,
      'uint64_t NativeXfbGeneration(uint32_t address)')

m = rt / 'core/main.cpp'
apply(m, 'uint64_t NativeXfbCopyCount();\n} // NATIVE_XFB_MAIN_DECL',
      'uint64_t NativeXfbCopyCount();\nuint64_t NativeXfbGeneration(uint32_t address); // ' + MARK + '\n} // NATIVE_XFB_MAIN_DECL',
      'uint64_t NativeXfbGeneration(uint32_t address); // ' + MARK)
apply(m, "        if(std::getenv(\"NWII_OFFSCREEN\")==nullptr) { native_trace::Scope trace_present(\"host.present\"); SDL_GL_SwapWindow(window); }\n",
      "        if(std::getenv(\"NWII_OFFSCREEN\")==nullptr) { native_trace::Scope trace_present(\"host.present\"); SDL_GL_SwapWindow(window); }\n"
      "        { // " + MARK + ": present / new-image / XFB-copy rates (see tools/fix_present_metrics.py)\n"
      "          static uint64_t pm_presents=0,pm_new=0,pm_last_gen=0,pm_copies0=nwii::runtime::gx::NativeXfbCopyCount();\n"
      "          static auto pm_t0=std::chrono::steady_clock::now();\n"
      "          const uint64_t gen=have_present?nwii::runtime::gx::NativeXfbGeneration(xfb_addr):0;\n"
      "          const bool fresh=have_present && gen>pm_last_gen; if(fresh)pm_last_gen=gen; // newer copy than anything shown before (a flip back to an older buffer is not a new image)\n"
      "          ++pm_presents; pm_new+=fresh?1:0;\n"
      "          native_trace::mark(\"host.present\",fresh?1:0,uint64_t(field_period.count()));\n"
      "          const auto pm_now=std::chrono::steady_clock::now();const double pm_s=std::chrono::duration<double>(pm_now-pm_t0).count();\n"
      "          if(pm_s>=1.0){const uint64_t copies=nwii::runtime::gx::NativeXfbCopyCount();\n"
      "            char title[160];std::snprintf(title,sizeof title,\"NWiiRecomp | new frames %.1f fps | presented %.1f | xfb copies %.1f\",pm_new/pm_s,pm_presents/pm_s,(copies-pm_copies0)/pm_s);\n"
      "            if(window)SDL_SetWindowTitle(window,title);\n"
      "            static const bool pm_print=std::getenv(\"NWII_FPS_LOG\")!=nullptr; if(pm_print){std::printf(\"[FPS] %s\\n\",title+13);std::fflush(stdout);}\n"
      "            pm_presents=pm_new=0;pm_copies0=copies;pm_t0=pm_now;}\n"
      "        }\n",
      MARK + ': present / new-image')
# V2: a flip back to an older XFB buffer is not a new image (generation must exceed everything shown before).
_old = 'const bool fresh=have_present && gen!=0 && gen!=pm_last_gen; if(fresh)pm_last_gen=gen;'
_new = 'const bool fresh=have_present && gen>pm_last_gen; if(fresh)pm_last_gen=gen; // newer copy than anything shown before (a flip back to an older buffer is not a new image)'
_t = m.read_text(errors='surrogateescape')
if _old in _t:
    m.write_text(_t.replace(_old, _new, 1), errors='surrogateescape')
print('present metrics installed')
