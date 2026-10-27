"""Inline VAT readers for one verified common layout; general decoder retained."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]
path=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/fifo_parser.cpp'
s=path.read_text();marker='NATIVE_DIRECT_VERTEX_DECODE_V1'
if marker not in s:
    (p/'runtime-overlays/perf_decoder_baseline.cpp').write_text(s)
    a=s.index('void FifoParser::DecodeDraw(GXCommand& c) {')
    b=s.index('\nvoid FifoParser::Parse(',a)
    body=s[a:b].replace('void FifoParser::DecodeDraw(GXCommand& c) {',
                          'template<bool Direct> static void NativeDecodeDraw(GXCommand& c) {',1)
    body=body.replace('if (!pos.read(buf,off,buf_size,vtx.pos))',
        'if (!(Direct ? NativeVectorPlan::Read<1,4,3>(pos,buf,off,buf_size,vtx.pos) : pos.read(buf,off,buf_size,vtx.pos)))',1)
    body=body.replace('if (!nrm.read(buf,off,buf_size,nbt))',
        'if (!(Direct ? NativeVectorPlan::Read<1,4,3>(nrm,buf,off,buf_size,nbt) : nrm.read(buf,off,buf_size,nbt)))',1)
    body=body.replace('if (!tex[t].read(buf,off,buf_size,vtx.tex[t]))',
        'if (!(Direct ? NativeVectorPlan::Read<1,4,2>(tex[t],buf,off,buf_size,vtx.tex[t]) : tex[t].read(buf,off,buf_size,vtx.tex[t])))',1)
    wrapper='''
void FifoParser::DecodeDraw(GXCommand& c) {
    // NATIVE_DIRECT_VERTEX_DECODE_V1: specialize the whole draw, not each vertex.
    if (c.raw) {
        const auto& v=c.raw->vat;
        bool direct = v.posMask==VtxAttrMask::Direct && v.posType==VtxAttrType::F32 && v.posElements &&
            v.nrmMask==VtxAttrMask::Direct && v.nrmType==VtxAttrType::F32 && !v.nrmElements &&
            v.texMask[0]==VtxAttrMask::Direct && v.texType[0]==VtxAttrType::F32 && v.texElements[0];
        for(unsigned t=1;t<8;++t)direct=direct && v.texMask[t]==VtxAttrMask::None;
        if(direct) {NativeDecodeDraw<true>(c);return;}
    }
    NativeDecodeDraw<false>(c);
}
'''
    path.write_text(s[:a]+body+wrapper+s[b:])
print('Common direct-float vertex layout specialized; full VAT fallback retained.')
