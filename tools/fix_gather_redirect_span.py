"""Group redirected gather writes; retain byte fallback at observable boundaries."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]
f=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp'
s=f.read_text()
marker='NATIVE_GATHER_REDIRECT_SPAN_V1'
if marker not in s:
    (p/'runtime-overlays/gather-span-before.cpp').write_text(s)
    helper=r'''
// NATIVE_GATHER_REDIRECT_SPAN_V1: 0=ordinary FIFO, 1=stored, -1=byte fallback.
static inline int native_gather_redirect_span(uint64_t value, unsigned bytes) {
    static const bool probe=std::getenv("NWII_GX_REDIRECT_PROBE")!=nullptr;
    if(probe || nwii::runtime::g_watch_addr)return -1;
    auto* mmu=nwii::runtime::g_mmu;
    if(!mmu)return 0;
    uint32_t base=0,end=0,ptr=0;
    if(g_wpar_redirect)ptr=g_wpar_cur;
    else {
        nwii::runtime::hw::pi_fifo_get(base,end,ptr);
        if(base==0 && end<0x04000000u)return 0;
        if(base!=0 && (base&0x03ffffffu)==(nwii::runtime::hw::cp_fifo_base_reg()&0x03ffffffu))return 0;
        // The byte path can wrap inside a scalar write; keep that path intact.
        if(!(base==0 && end>=0x04000000u) &&
           uint64_t(ptr&0x03ffffffu)+bytes-1>uint64_t(end&0x03ffffffu))return -1;
    }
    const uint32_t phys=ptr&(g_wpar_redirect?0x3fffffffu:0x1fffffffu);
    uint8_t* dst=nullptr;
    if(phys<0x01800000u && uint64_t(phys)+bytes<=0x01800000u)dst=mmu->mem1.data()+phys;
    else if(phys>=0x10000000u && uint64_t(phys)+bytes<=0x14000000u)dst=mmu->mem2.data()+(phys-0x10000000u);
    if(!dst)return -1; // includes RAM-end crossing and invalid bus addresses
    const uint64_t be=__builtin_bswap64(value<<(8*(8-bytes)));
    std::memcpy(dst,&be,bytes);
    ptr+=bytes;
    if(g_wpar_redirect){g_wpar_cur=ptr;g_wpar_fill=(g_wpar_fill+bytes)&31u;}
    else {
        if(!(base==0 && end>=0x04000000u) && (ptr&0x03ffffffu)>(end&0x03ffffffu))ptr=base;
        nwii::runtime::hw::pi_fifo_set_wptr(ptr);
    }
    return 1;
}
'''
    s=s.replace('// NATIVE_WGP_BULK_V1:',helper+'\n// NATIVE_WGP_BULK_V1:',1)
    for signature,value,width in [('void GX_WGPIPE_Write8(uint8_t val) {','val',1),('void GX_WGPIPE_Write16(uint16_t val) {','val',2),('void GX_WGPIPE_Write32(uint32_t val) {','val',4)]:
        assert s.count(signature)==1
        s=s.replace(signature,signature+f'\n    const int redirect=native_gather_redirect_span({value},{width});\n    if(redirect==1)return;\n    if(redirect==0 && !native_wgp_diagnostics()){{native_wgp_bulk({value},{width});return;}}',1)
    anchor='    union { double d; uint64_t i; } u; u.d = val;'
    assert s.count(anchor)==1
    s=s.replace(anchor,anchor+'\n    const int redirect=native_gather_redirect_span(u.i,8);\n    if(redirect==1)return;\n    if(redirect==0 && !native_wgp_diagnostics()){native_wgp_bulk(u.i,8);return;}',1)
    f.write_text(s)
print('Gather span specialization staged; boundary/watch/probe byte paths retained.')
