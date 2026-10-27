"""Draw GX lines, line strips and points.

EmitPrimitive only handled quads, triangles, strips and fans, so primitives
0xA8 (lines), 0xB0 (line strips) and 0xB8 (points) were parsed and then silently
dropped. They are expanded into screen-space quads using the BP 0x22 widths
(low byte = line width, next byte = point size, both in 1/6 pixel). Quads are
emitted with both windings so the draw's cull mode cannot remove them.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
p = port / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
t = p.read_text()
marker = 'NATIVE_LINES_POINTS_V1'
if marker not in t:
    anchor = 'static void EmitPrimitive(const GXCommand &cmd) {'
    assert t.count(anchor) == 1
    helper = r'''// NATIVE_LINES_POINTS_V1
static void NativeQuadBoth(const BatchVertex &a, const BatchVertex &b, const BatchVertex &c, const BatchVertex &d) {
  s_batch.push_back(a); s_batch.push_back(b); s_batch.push_back(c);
  s_batch.push_back(c); s_batch.push_back(b); s_batch.push_back(d);
  s_batch.push_back(c); s_batch.push_back(b); s_batch.push_back(a);
  s_batch.push_back(d); s_batch.push_back(b); s_batch.push_back(c);
}

static void NativeEmitLinesPoints(const GXCommand &cmd) {
  const auto &v = cmd.vertices;
  const size_t n = v.size();
  const size_t base = s_batch.size();
  for (const auto &vtx : v) EmitVertex(vtx);
  std::vector<BatchVertex> pts(s_batch.begin() + base, s_batch.end());
  s_batch.resize(base);
  const bool persp = CurrentIsPerspective();
  const float *P = g_state.projection;
  const float sx = std::fabs(P[0]) * 320.0f;
  const float sy = std::fabs(P[2]) * (float(kNativeEfbHeight) * 0.5f);
  if (sx <= 0.0f || sy <= 0.0f || pts.size() != n) return;
  const uint32_t width_reg = g_state.bp[0x22];
  const float line_half = std::max(0.5f, float(width_reg & 0xff) / 6.0f * 0.5f);
  const float point_half = std::max(0.5f, float((width_reg >> 8) & 0xff) / 6.0f * 0.5f);
  auto depth = [&](const BatchVertex &q) { return persp ? std::max(1e-4f, std::fabs(q.z)) : 1.0f; };
  auto segment = [&](const BatchVertex &a, const BatchVertex &b) {
    const float da = depth(a), db = depth(b);
    float dx = b.x * sx / db - a.x * sx / da, dy = b.y * sy / db - a.y * sy / da;
    const float len = std::sqrt(dx * dx + dy * dy);
    if (len < 1e-6f) { dx = 1.0f; dy = 0.0f; } else { dx /= len; dy /= len; }
    auto corner = [&](const BatchVertex &q, float sign) {
      BatchVertex o = q;
      const float d = depth(q);
      o.x += sign * (-dy) * line_half * d / sx;
      o.y += sign * dx * line_half * d / sy;
      return o;
    };
    NativeQuadBoth(corner(a, 1.0f), corner(a, -1.0f), corner(b, 1.0f), corner(b, -1.0f));
  };
  if (cmd.prim_type == 0xA8) {
    for (size_t i = 0; i + 1 < n; i += 2) segment(pts[i], pts[i + 1]);
  } else if (cmd.prim_type == 0xB0) {
    for (size_t i = 0; i + 1 < n; ++i) segment(pts[i], pts[i + 1]);
  } else {
    for (const auto &q : pts) {
      const float d = depth(q);
      const float ox = point_half * d / sx, oy = point_half * d / sy;
      BatchVertex a = q, b = q, c = q, e = q;
      a.x -= ox; a.y -= oy; b.x += ox; b.y -= oy; c.x -= ox; c.y += oy; e.x += ox; e.y += oy;
      NativeQuadBoth(a, b, c, e);
    }
  }
}

'''
    t = t.replace(anchor, helper + anchor)
    old = '  switch (cmd.prim_type) {\n  case 0x80:'
    assert t.count(old) == 1
    t = t.replace(old, '  if (cmd.prim_type >= 0xA8 && cmd.prim_type <= 0xB8) { NativeEmitLinesPoints(cmd); return; }\n' + old)
    p.write_text(t)
print('GX lines and points are now drawn.')
