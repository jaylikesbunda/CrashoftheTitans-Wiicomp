"""Convert strip/fan/quad vertices once; retain original expanded triangle order."""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
path = port/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
s = path.read_text()
if 'NATIVE_VERTEX_REUSE_V1' not in s:
    a = s.index('static void EmitVertex(')
    b = s.index('\nstatic bool s_batch_open', a)
    c = s.index('static void EmitPrimitive(const GXCommand &cmd) {')
    d = s.index('\n}', c)+2
    baseline = port/'runtime-overlays/vertex_reuse_baseline.inc'
    if not baseline.exists():
        baseline.write_text(s[a:b]+'\n'+s[c:d]+'\n')
    convert = s[a:b].replace('static void EmitVertex(', 'static BatchVertex ConvertVertex(', 1)
    convert = convert.replace('  s_batch.push_back(bv);', '  return bv;', 1)
    convert += '\nstatic void EmitVertex(const VertexData &vtx) { s_batch.push_back(ConvertVertex(vtx)); }\n'
    primitive = '''static void EmitPrimitive(const GXCommand &cmd) {
  // NATIVE_VERTEX_REUSE_V1: reuse within this draw only; state remains live.
  const auto &v = cmd.vertices;
  const size_t n = v.size();
  if (cmd.prim_type >= 0xA8 && cmd.prim_type <= 0xB8) { NativeEmitLinesPoints(cmd); return; }
  switch (cmd.prim_type) {
  case 0x80:
    for (size_t i=0; i+3<n; i+=4) {
      const BatchVertex a=ConvertVertex(v[i]), b=ConvertVertex(v[i+1]);
      const BatchVertex c=ConvertVertex(v[i+2]), d=ConvertVertex(v[i+3]);
      s_batch.push_back(a); s_batch.push_back(b); s_batch.push_back(c);
      s_batch.push_back(c); s_batch.push_back(d); s_batch.push_back(a);
    }
    break;
  case 0x90:
    for (const auto &vtx : v) EmitVertex(vtx);
    break;
  case 0x98:
    if (n>=3) {
      BatchVertex a=ConvertVertex(v[0]), b=ConvertVertex(v[1]);
      for (size_t i=2; i<n; ++i) {
        const BatchVertex c=ConvertVertex(v[i]);
        if (i&1) { s_batch.push_back(b); s_batch.push_back(a); }
        else { s_batch.push_back(a); s_batch.push_back(b); }
        s_batch.push_back(c); a=b; b=c;
      }
    }
    break;
  case 0xA0:
    if (n>=3) {
      const BatchVertex a=ConvertVertex(v[0]);
      BatchVertex b=ConvertVertex(v[1]);
      for (size_t i=2; i<n; ++i) {
        const BatchVertex c=ConvertVertex(v[i]);
        s_batch.push_back(a); s_batch.push_back(b); s_batch.push_back(c); b=c;
      }
    }
    break;
  }
}'''
    s = s[:c]+primitive+s[d:]
    s = s[:a]+convert+s[b:]
    path.write_text(s)
build = port/'tools/build_native.py'
s = build.read_text()
if "run('native-vertex-reuse-stage'" not in s:
    anchor = "run('native-game-build',"
    assert s.count(anchor)==1
    s = s.replace(anchor,"run('native-vertex-reuse-stage',[sys.executable,PORT/'tools/fix_vertex_reuse.py'])\n"+anchor,1)
    build.write_text(s)
print('Per-draw vertex reuse installed.')
