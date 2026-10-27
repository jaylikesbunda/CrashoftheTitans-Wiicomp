"""Remove repeated RAM dispatch and indexed-attribute resolution; preserve fallbacks."""
from pathlib import Path
import re
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
path=rt/'include/runtime/cpu_context.h';s=path.read_text()
if 'NATIVE_RAM_INLINE_V1' not in s:
    a=s.index('struct MMU {');b=s.index('\nstruct CPUContext {',a)
    (p/'runtime-overlays/ram_inline_baseline.inc').write_text(s[a:b])
    # Keep the entire original MMIO/invalid-address implementations out of line.
    for width in (8,16,32,64):
        signature=f'  uint{width}_t read{width}(uint32_t addr) {{'
        assert s.count(signature)==1
        suffix='\n    if (paddr == 0x0000000Cu) return 0;' if width==32 else ''
        load=f'uint{width}_t value; std::memcpy(&value,mem1.data()+paddr,{width//8});'
        result='value' if width==8 else f'__builtin_bswap{width}(value)'
        wrapper=f'''  // NATIVE_RAM_INLINE_V1: normal MEM1 reads have no external call or MMIO dispatch.
  __attribute__((always_inline)) inline uint{width}_t read{width}(uint32_t addr) {{
    const uint32_t paddr=addr&0x3fffffffu;{suffix}
    if (__builtin_expect(paddr<mem1.size()-8,1)) {{ {load} return {result}; }}
    return native_read{width}_slow(addr);
  }}
  __attribute__((noinline)) uint{width}_t native_read{width}_slow(uint32_t addr) {{'''
        s=s.replace(signature,wrapper,1)
    # Floating reads inherit the same fast access rather than an extra wrapper call.
    s=s.replace('  float read_f32(', '  __attribute__((always_inline)) inline float read_f32(',1)
    path.write_text(s)

if 'NATIVE_FPU_INLINE_GUARD_V1' not in s:
    old='  bool require_fpu(uint32_t fault_pc) {'
    new='''  // NATIVE_FPU_INLINE_GUARD_V1: keep the common FP-enabled path inside generated code.
  __attribute__((always_inline)) inline bool require_fpu(uint32_t fault_pc) {
    if(msr&0x2000u)return false;
    return native_require_fpu_slow(fault_pc);
  }
  __attribute__((noinline)) bool native_require_fpu_slow(uint32_t fault_pc) {'''
    assert s.count(old)==1;s=s.replace(old,new,1);path.write_text(s)

# Resolve an indexed array's complete index domain once per draw. This cache is
# draw-local, so it cannot survive RAM writes or a change in the MMU instance.
plan=p/'runtime-overlays/vector_decode_plan.inc';s=plan.read_text()
if 'NATIVE_INDEX_ARRAY_SPAN_V1' not in s:
    s=s.replace('    uint32_t base, stride;','''    uint32_t base, stride;
    const uint8_t* span=nullptr; // NATIVE_INDEX_ARRAY_SPAN_V1
    void resolve_span(VtxAttrMask mask,int components,int type) {
        if(!nwii::runtime::g_mmu || (int(mask)!=2 && int(mask)!=3))return;
        const uint64_t physical=base&0x3fffffffu;
        const uint64_t last=uint64_t(int(mask)==2?255:65535)*stride;
        const uint64_t bytes=uint64_t(components)*(type<2?1:type<4?2:4);
        auto& m=*nwii::runtime::g_mmu;
        auto valid=[&](uint64_t start,const auto& bank){
            return start+last<bank.size()-8 && start+last+bytes<=bank.size();
        };
        if(physical<m.mem1.size()-8 && valid(physical,m.mem1))span=m.mem1.data()+physical;
        else if(physical>=0x10000000 && physical<0x10000000+m.mem2.size()-8 && valid(physical-0x10000000,m.mem2))span=m.mem2.data()+physical-0x10000000;
    }''',1)
    s=s.replace('        for(int i=0;i<Components;++i)out[i]=0.0f;\n        if constexpr(Mask==0)return true;',
                '        if constexpr(Mask==0){for(int i=0;i<Components;++i)out[i]=0.0f;return true;}',1)
    old='''            if(!nwii::runtime::g_mmu)return true;
            src=nwii::runtime::g_mmu->get_ptr(p.base+index*p.stride);
            if(!src)return true;'''
    new='''            if(p.span)src=p.span+size_t(index)*p.stride;
            else if(nwii::runtime::g_mmu)src=nwii::runtime::g_mmu->get_ptr(p.base+index*p.stride);
            if(!src){for(int i=0;i<Components;++i)out[i]=0.0f;return true;}'''
    assert s.count(old)==1;s=s.replace(old,new,1)
    s=s.replace('        switch(n) {case 1:reader=', '        resolve_span(mask,n,int(type));\n        switch(n) {case 1:reader=',1)
    plan.write_text(s)

path=rt/'src/hle/gx/fifo_parser.cpp';s=path.read_text()
# Restage the general plan independently of the indexed draw specialization.
a=s.index('// NATIVE_VECTOR_DECODE_PLAN_V1:');b=s.index('template<bool Direct> static void NativeDecodeDraw(',a)
s=s[:a]+plan.read_text()+'\n'+s[b:]
if 'NATIVE_INDEXED_DRAW_DECODE_V1' not in s:
    (p/'runtime-overlays/indexed_draw_baseline.cpp').write_text(s)
    a=s.index('template<bool Direct> static void NativeDecodeDraw(');b=s.index('\nvoid FifoParser::DecodeDraw(',a)
    body=s[a:b].replace('template<bool Direct> static void NativeDecodeDraw(GXCommand& c)',
        'template<int P,int N,int T> static void NativeIndexedDecodeDraw(GXCommand& c)',1)
    body=body.replace('Direct ? NativeVectorPlan::Read<1,4,3>(pos,buf,off,buf_size,vtx.pos) : pos.read(buf,off,buf_size,vtx.pos)',
        'NativeVectorPlan::Read<P,4,3>(pos,buf,off,buf_size,vtx.pos)',1)
    body=body.replace('Direct ? NativeVectorPlan::Read<1,4,3>(nrm,buf,off,buf_size,nbt) : nrm.read(buf,off,buf_size,nbt)',
        'NativeVectorPlan::Read<N,4,3>(nrm,buf,off,buf_size,nbt)',1)
    body=body.replace('Direct ? NativeVectorPlan::Read<1,4,2>(tex[t],buf,off,buf_size,vtx.tex[t]) : tex[t].read(buf,off,buf_size,vtx.tex[t])',
        'NativeVectorPlan::Read<T,4,2>(tex[t],buf,off,buf_size,vtx.tex[t])',1)
    body=body.replace('    c.vertices.reserve(r.count);','    c.vertices.resize(r.count);',1)
    old='''        c.vertices.emplace_back();
        VertexData& vtx=c.vertices.back();'''
    assert body.count(old)==1;body=body.replace(old,'        VertexData& vtx=c.vertices[i];',1)
    body=body.replace('c.vertices.pop_back();return;', 'c.vertices.resize(i);return;')
    # Instantiated only after a complete stream is checked; failure logic retained.
    dispatch='''
// NATIVE_INDEXED_DRAW_DECODE_V1: specialize a complete common indexed layout.
static bool NativeTryIndexedDecode(GXCommand& c) {
    if(c.type!=GXCommandType::DrawPrimitive || !c.raw || !c.vertices.empty())return false;
    const auto& r=*c.raw;const auto& v=r.vat;
    auto indexed=[](VtxAttrMask m){return m==VtxAttrMask::Index8 || m==VtxAttrMask::Index16;};
    if(!indexed(v.posMask)||!indexed(v.nrmMask)||!indexed(v.texMask[0]) ||
       v.posType!=VtxAttrType::F32 || !v.posElements || v.nrmType!=VtxAttrType::F32 ||
       v.nrmElements || v.texType[0]!=VtxAttrType::F32 || !v.texElements[0])return false;
    for(unsigned t=1;t<8;++t)if(v.texMask[t]!=VtxAttrMask::None)return false;
    if(uint64_t(VertexSize(v))*r.count>r.bytes.size())return false;
    const unsigned key=(v.posMask==VtxAttrMask::Index16?4:0)|(v.nrmMask==VtxAttrMask::Index16?2:0)|(v.texMask[0]==VtxAttrMask::Index16?1:0);
    switch(key) {
'''
    for key in range(8):dispatch+=f'    case {key}:NativeIndexedDecodeDraw<{3 if key&4 else 2},{3 if key&2 else 2},{3 if key&1 else 2}>(c);break;\n'
    dispatch+='    }\n    return true;\n}\n'
    s=s[:b]+'\n'+body+dispatch+s[b:]
    anchor='void FifoParser::DecodeDraw(GXCommand& c) {'
    s=s.replace(anchor,anchor+'\n    if(NativeTryIndexedDecode(c))return;',1)
path.write_text(s)
print('Broad inlined RAM reads, draw-local indexed spans and compiled indexed layouts staged.')
