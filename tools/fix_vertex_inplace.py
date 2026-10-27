"""Write converted vertices into their final batch storage, without a full temporary copy."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];path=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp';s=path.read_text()
baseline=p/'runtime-overlays/vertex_inplace_baseline.inc'
if baseline.exists() and '  BatchVertex bv = {0};' not in baseline.read_text():
    raise RuntimeError('Saved pre-inplace conversion source changed: restore and requalify it before staging')
if 'NATIVE_VERTEX_INPLACE_V1' not in s or 'static void NativeWriteConvertedVertex(' not in s or s.count('static BatchVertex ConvertVertex(')!=1:
    a=s.index('static void NativeWriteConvertedVertex(') if 'static void NativeWriteConvertedVertex(' in s else s.index('static BatchVertex ConvertVertex(')
    b=s.index('\nstatic bool s_batch_open',a)
    if not baseline.exists():baseline.write_text(s[a:b])
    old=baseline.read_text();assert '  BatchVertex bv = {0};' in old
    helper=old[:old.index('static void EmitVertex(')]
    helper=helper.replace('static BatchVertex ConvertVertex(const VertexData &vtx) {\n  BatchVertex bv = {0};',
        'static void NativeWriteConvertedVertex(const VertexData &vtx, BatchVertex& bv) {',1)
    helper=helper.replace('  return bv;','',1)
    assert helper.count('static void NativeWriteConvertedVertex(')==1
    new=helper+'''// NATIVE_VERTEX_INPLACE_V1: one initialization, no stack vertex/copy per emission.
static BatchVertex ConvertVertex(const VertexData &vtx) {
  BatchVertex result{};NativeWriteConvertedVertex(vtx,result);return result;
}
static void EmitVertex(const VertexData &vtx) {
  s_batch.emplace_back();NativeWriteConvertedVertex(vtx,s_batch.back());
}
'''
    s=s[:a]+new+s[b:];path.write_text(s)
print('Converted vertices now written directly to batch storage.')
