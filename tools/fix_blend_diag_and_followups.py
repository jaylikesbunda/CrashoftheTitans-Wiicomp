"""Follow-ups after the build-21 report (audio drop-outs, slightly see-through objects).

1. Audio: after an underrun the jitter buffer waited for 60 ms of audio before
   playing again. When the guest runs slower than real time that means long
   silences and short effects never playing. Re-prime at 20 ms instead; the
   steady-state target stays 60 ms.
2. Rasterised-colour "null" selection goes back to alpha 1. Dolphin yields zero,
   but the change was a precision tweak made without evidence and can only make
   draws more transparent; keep the behaviour that rendered correctly before.
3. Opt-in diagnostics (NWII_BLENDDIAG=1 enables; default off so the per-batch
   map lookup, clock read and periodic per-vertex scan do not run during play):
   every 10 s print the most frequent (blend, z, pixel-format, alpha-test, TEV)
   draw signatures with their stage setup, so a translucent object can be
   matched to the exact GX state that draws it.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
src = port / 'build/nwii-full-source/nWiiRuntime/src'

p = src / 'hw/hw_dsp.cpp'
t = p.read_text()
if 'NATIVE_AUDIO_PRIME_V2' not in t:
    old = '        if (have < size_t(kTarget)) {'
    assert t.count(old) == 1
    t = t.replace(old, '        if (have < size_t(640)) { // NATIVE_AUDIO_PRIME_V2')
    p.write_text(t)

p = src / 'hle/gx/tev_shader_gen.cpp'
t = p.read_text()
if 'NATIVE_RAS_NULL_ALPHA_ONE_V1' not in t:
    old = 'else fs << "    rasColor = vec4(0.0);\\n"; // NATIVE_TEV_FIDELITY_V1'
    assert t.count(old) == 1
    t = t.replace(old, 'else fs << "    rasColor = vec4(0.0, 0.0, 0.0, 1.0);\\n"; // NATIVE_RAS_NULL_ALPHA_ONE_V1')
    p.write_text(t)

p = src / 'hle/gx/renderer_gl.cpp'
t = p.read_text()
if 'NATIVE_BLEND_DIAG_V1' not in t:
    old = '  uint64_t hash = NativeTevShaderHash(g_state, cmd.prim_type);\n\n  if (!g_stat_hash0)'
    assert t.count(old) == 1
    new = r'''  uint64_t hash = NativeTevShaderHash(g_state, cmd.prim_type);

  { // NATIVE_BLEND_DIAG_V1
    static const bool diag = std::getenv("NWII_BLENDDIAG") != nullptr;
    if (diag) {
      struct Sig { uint64_t n = 0, verts = 0; std::string desc; };
      static std::unordered_map<uint64_t, Sig> sigs;
      static auto t0 = std::chrono::steady_clock::now();
      const uint32_t cm = g_state.bp[0x41], zm = g_state.bp[0x40], pe = g_state.bp[0x43];
      const uint64_t key = hash ^ (uint64_t(cm) << 1) ^ (uint64_t(zm) << 29) ^ (uint64_t(pe) << 40);
      Sig &s = sigs[key];
      if (!s.n) {
        const auto &at = g_state.alphaTest;
        char b[256];
        std::snprintf(b, sizeof b, "blend=%u src=%u dst=%u sub=%u logic=%u/%u upd=%u%u z=%06X pe=%06X tev=%u gens=%u at=%u/%u,%u/%u,%u |",
                      cm & 1, (cm >> 8) & 7, (cm >> 5) & 7, (cm >> 11) & 1, (cm >> 1) & 1, (cm >> 12) & 15,
                      (cm >> 3) & 1, (cm >> 4) & 1, zm, pe, unsigned(g_state.numTevStages), g_state.bp[0] & 0xF,
                      at.comp0, at.ref0, at.comp1, at.ref1, at.logic);
        s.desc = b;
        for (unsigned i = 0; i < g_state.numTevStages && i < 16; ++i) {
          const auto &st = g_state.tevStages[i];
          std::snprintf(b, sizeof b, " [%u: a=%u%u%u%u bias%u op%u sc%u cl%u dst%u | A=%u%u%u%u bias%u op%u sc%u dst%u | ch%u map%u]", i,
                        st.colorInA, st.colorInB, st.colorInC, st.colorInD, st.colorBias, st.colorOp, st.colorScale,
                        st.colorClamp, st.colorRegId, st.alphaInA, st.alphaInB, st.alphaInC, st.alphaInD, st.alphaBias,
                        st.alphaOp, st.alphaScale, st.alphaRegId, st.colorChan, unsigned(st.texMap));
          s.desc += b;
        }
      }
      ++s.n;
      s.verts += cmd.raw ? cmd.raw->count : cmd.vertices.size();
      const auto now = std::chrono::steady_clock::now();
      if (std::chrono::duration_cast<std::chrono::seconds>(now - t0).count() >= 10) {
        t0 = now;
        std::vector<const Sig *> order;
        for (const auto &kv : sigs) order.push_back(&kv.second);
        std::sort(order.begin(), order.end(), [](const Sig *a, const Sig *b2) { return a->n > b2->n; });
        std::printf("[BLENDDIAG] ---- %zu signatures ----\n", order.size());
        for (size_t i = 0; i < order.size() && i < 14; ++i)
          std::printf("[BLENDDIAG] n=%llu verts=%llu %s\n", (unsigned long long)order[i]->n,
                      (unsigned long long)order[i]->verts, order[i]->desc.c_str());
        std::fflush(stdout);
        sigs.clear();
      }
    }
  }

  if (!g_stat_hash0)'''
    t = t.replace(old, new)
    p.write_text(t)
print('Audio re-prime 20 ms, ras-null alpha restored, blend diagnostics added.')
