
// NATIVE_STATE_CENSUS_V2 (tools/fix_state_census.py): state census (NWII_STATE_CENSUS) and the draw-state picker (F5/F6/F7 in the overlay).
namespace nwii::runtime::gx {
namespace {
struct CensusEntry {
  uint64_t draws = 0, vertices = 0, first = 0, last = 0;
  uint32_t bp[256]; uint32_t xf[0x60]; uint32_t tex_addr[8], tex_w[8], tex_h[8], tex_fmt[8]; bool tex_efb[8];
  uint8_t tev_stages = 0, tex_gens = 0, chans = 0;
  float uvmin[2] = {1e30f, 1e30f}, uvmax[2] = {-1e30f, -1e30f}; uint32_t mtx_min = 255, mtx_max = 0; bool have_uv = false; // raw texcoord set 0 over the batches seen
};
static const char* min_filter_name(unsigned f) {
  switch (f) { case 0: return "near"; case 1: return "near/mip-near"; case 2: return "near/mip-lin"; case 4: return "linear"; case 5: return "lin/mip-near"; case 6: return "lin/mip-lin"; default: return "?"; }
}
static const char* wrap_name(unsigned w) { return w == 0 ? "clamp" : w == 1 ? "repeat" : w == 2 ? "mirror" : "?"; }
// Everything we know about one state, one short line per fact (the overlay prints the same text).
static std::string describe(uint64_t id, const CensusEntry& c, bool compact) {
  char b[200]; std::string t;
  const uint32_t gen = c.bp[0x00], ind_stages = (gen >> 16) & 7;
  std::snprintf(b, sizeof b, "state %016llx  %llu draws  %llu verts (%.0f/draw)\n", (unsigned long long)id, (unsigned long long)c.draws,
                (unsigned long long)c.vertices, c.draws ? double(c.vertices) / double(c.draws) : 0.0); t += b;
  std::snprintf(b, sizeof b, "tev=%u texgens=%u chans=%u ind_stages=%u  z=%06x blend=%06x alpha=%06x\n", c.tev_stages, c.tex_gens, c.chans, ind_stages,
                c.bp[0x40], c.bp[0x41], c.bp[0xF3]); t += b;
  for (unsigned i = 0; i < 8; ++i) if (c.tex_addr[i] && i < std::max<unsigned>(1, c.tex_gens)) {
    const uint32_t m0 = c.bp[(i < 4 ? 0x80 : 0xA0) + (i % 4)], m1 = c.bp[(i < 4 ? 0x84 : 0xA4) + (i % 4)];
    std::snprintf(b, sizeof b, "tex%u %08x %ux%u fmt%u%s | %s/%s mag%s min:%s bias%+d lod%u..%u\n", i, c.tex_addr[i], c.tex_w[i], c.tex_h[i], c.tex_fmt[i],
                  c.tex_efb[i] ? " EFB" : "", wrap_name(m0 & 3), wrap_name((m0 >> 2) & 3), (m0 & 16) ? "lin" : "near", min_filter_name((m0 >> 5) & 7),
                  int(int8_t((m0 >> 9) & 0xff)), (m1 & 0xff) / 16, ((m1 >> 8) & 0xff) / 16); t += b;
  }
  if (c.have_uv) { std::snprintf(b, sizeof b, "uv0 raw range u[%.3g..%.3g] v[%.3g..%.3g]  texmtx idx %u..%u\n", c.uvmin[0], c.uvmax[0], c.uvmin[1], c.uvmax[1], c.mtx_min, c.mtx_max); t += b; }
  for (unsigned t_ = 0; t_ < c.tex_gens && t_ < 8; ++t_) {
    const uint32_t tg = c.xf[0x40 + t_];
    std::snprintf(b, sizeof b, "texgen%u xf=%03x type=%u src=%u proj=%u%s\n", t_, tg, (tg >> 4) & 7, (tg >> 7) & 0x1f, (tg >> 1) & 1, ""); t += b;
  }
  for (unsigned i = 0; i < 16u && i < c.tev_stages; ++i) {
    std::snprintf(b, sizeof b, "tev%u c=%06x a=%06x tref=%06x ksel=%06x\n", i, c.bp[0xC0 + i * 2], c.bp[0xC1 + i * 2], c.bp[0x28 + i / 2], c.bp[0xF6 + i / 2]); t += b;
    if (compact && i >= 3) { t += "...\n"; break; }
  }
  for (unsigned i = 0; i < 4 && i < ind_stages; ++i) {
    std::snprintf(b, sizeof b, "ind%u cmd=%06x mtx=%06x/%06x/%06x scale=%06x\n", i, c.bp[0x10 + i], c.bp[0x06 + i * 3], c.bp[0x07 + i * 3], c.bp[0x08 + i * 3], c.bp[0x25 + i / 2]); t += b;
  }
  return t;
}
struct Census {
  std::unordered_map<uint64_t, CensusEntry> map;
  uint64_t flushes = 0; const char* path = nullptr; bool on = false;
  uint64_t selected = 0; std::vector<uint64_t> ids; uint64_t ids_at = 0;
  Census() {
    const char* e = std::getenv("NWII_STATE_CENSUS");
    if (e && *e) { // cmd.exe's "set X=1 && ..." leaves a trailing space in the value: trim it
      static std::string value; value = e; while (!value.empty() && (value.back() == ' ' || value.back() == '\t')) value.pop_back();
      if (!value.empty()) { on = true; path = (value == "1") ? "state-census.txt" : value.c_str(); }
    }
  }
  ~Census() { if (on) write(); }
  void write() {
    FILE* f = std::fopen(path, "w"); if (!f) return;
    std::vector<std::pair<uint64_t, const CensusEntry*>> order;
    for (auto& kv : map) order.emplace_back(kv.first, &kv.second);
    std::sort(order.begin(), order.end(), [](auto& a, auto& b) { return a.second->vertices > b.second->vertices; });
    std::fprintf(f, "# state census: %llu flushes, %zu distinct states (sorted by vertices)\n", (unsigned long long)flushes, map.size());
    for (auto& o : order) std::fprintf(f, "\n== %s", describe(o.first, *o.second, false).c_str());
    std::fclose(f);
  }
  // states drawn recently, busiest first
  void rebuild_ids() {
    ids.clear();
    for (auto& kv : map) if (kv.second.last + 900 >= flushes) ids.push_back(kv.first);
    std::sort(ids.begin(), ids.end(), [&](uint64_t a, uint64_t b) { return map[a].vertices > map[b].vertices; });
    ids_at = flushes;
  }
};
Census& census() { static Census c; return c; }
}
// Returns true when the picker wants this batch not to be drawn.
static bool NativeStateCensus() {
  Census& c = census();
  auto& pk = native_hud::picker;
  const int mode = pk.mode.load(std::memory_order_relaxed);
  if (!c.on && mode == 0) return false;
  native_trace::Scope census_cpu("gx.state_census");
  static const uint8_t skip[] = {0x45, 0x47, 0x48, 0x49, 0x4A, 0x4B, 0x4D, 0x4E, 0x4F, 0x50, 0x51, 0x52, 0xFE};
  uint64_t h = 14695981039346656037ull; auto mix = [&](uint32_t v) { h = (h ^ v) * 1099511628211ull; h ^= h >> 29; };
  for (unsigned i = 0; i < 256; ++i) { bool k = false; for (uint8_t s : skip) if (s == i) k = true; if (!k) mix(g_state.bp[i]); }
  for (unsigned i = 0; i < 0x60; ++i) mix(g_state.xf[i]);
  const size_t verts = NativeBatchSize();
  ++c.flushes;
  auto it = c.map.find(h);
  if (it == c.map.end()) {
    CensusEntry e; std::memcpy(e.bp, g_state.bp, sizeof e.bp); std::memcpy(e.xf, g_state.xf, sizeof e.xf);
    e.first = c.flushes; e.tev_stages = g_state.numTevStages; e.tex_gens = g_state.numTexGens; e.chans = g_state.numChans;
    for (unsigned i = 0; i < 8; ++i) {
      const auto& t = g_state.texStages[i];
      e.tex_addr[i] = t.base_addr; e.tex_w[i] = t.width; e.tex_h[i] = t.height; e.tex_fmt[i] = t.format;
      e.tex_efb[i] = t.base_addr && nwii::runtime::hle::TextureCache::get().find_efb_texture(t.base_addr) != 0;
    }
    it = c.map.emplace(h, e).first;
  }
  ++it->second.draws; it->second.vertices += verts; it->second.last = c.flushes;
  if (!s_gpu_spans.empty()) {                                       // raw texture coordinates of the first vertices of the batch
    CensusEntry& e = it->second; size_t seen = 0;
    for (const auto& sp : s_gpu_spans) for (size_t i = 0; i < sp.count && seen < 2048; ++i, ++seen) {
      const VertexData& v = sp.vertices[i];
      e.uvmin[0] = std::min(e.uvmin[0], v.tex[0][0]); e.uvmax[0] = std::max(e.uvmax[0], v.tex[0][0]);
      e.uvmin[1] = std::min(e.uvmin[1], v.tex[0][1]); e.uvmax[1] = std::max(e.uvmax[1], v.tex[0][1]);
      e.mtx_min = std::min<uint32_t>(e.mtx_min, v.texMtxIdx[0]); e.mtx_max = std::max<uint32_t>(e.mtx_max, v.texMtxIdx[0]); e.have_uv = true;
    }
  }
  // Serialize the accumulated census on exit, in ~Census(). Rewriting thousands
  // of states every 240 batches cost >100 ms per frame in the 6500-state capture.

  // ---- picker: F6/F5 step through recently drawn states, F7 cycles off / hide the selected / show only the selected
  if (mode != 0 || pk.step.load() != 0) {
    const int step = pk.step.exchange(0);
    if (step != 0 || c.selected == 0) {
      c.rebuild_ids();
      if (!c.ids.empty()) {
        size_t pos = 0; for (size_t i = 0; i < c.ids.size(); ++i) if (c.ids[i] == c.selected) pos = i;
        if (c.selected == 0 || step == 0) pos = 0; else pos = (pos + c.ids.size() + (step > 0 ? 1 : c.ids.size() - 1)) % c.ids.size();
        c.selected = c.ids[pos];
        std::lock_guard<std::mutex> lock(pk.mutex);
        pk.text = "pick " + std::to_string(pos + 1) + "/" + std::to_string(c.ids.size()) + "  " + std::string(mode == 2 ? "SOLO (only this is drawn)" : mode == 1 ? "HIDDEN (this is not drawn)" : "off") +
                  "\n" + describe(c.selected, c.map[c.selected], true);
      }
    }
    if (c.selected && (c.flushes % 30) == 0) {                      // keep the counters in the overlay text fresh
      auto sel = c.map.find(c.selected);
      if (sel != c.map.end()) { std::lock_guard<std::mutex> lock(pk.mutex);
        const size_t nl = pk.text.find('\n'); const std::string head = pk.text.substr(0, nl == std::string::npos ? 0 : nl);
        pk.text = head + "\n" + describe(c.selected, sel->second, true); }
    }
    if (mode == 1) return h == c.selected;
    if (mode == 2) return h != c.selected;
  }
  return false;
}
}
