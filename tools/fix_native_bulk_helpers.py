"""Use actual bulk RAM operations in existing interpreter memory-helper hooks."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/src/hle/interpreter.cpp';s=f.read_text()
if 'NATIVE_BULK_HELPERS_V1' not in s:
    baseline=p/'runtime-overlays/bulk-helpers-before.cpp'
    if not baseline.exists():baseline.write_text(s)
    helper='''// NATIVE_BULK_HELPERS_V1: only complete RAM spans without watch observers.
static uint8_t* NativeHelperRamSpan(MMU& m,uint32_t a,uint32_t n){
    const uint32_t physical=a&0x3fffffffu;
    if(physical<m.mem1.size()-8 && n<=m.mem1.size()-8-physical)return m.mem1.data()+physical;
    const uint32_t offset=physical-0x10000000u;
    if(offset<m.mem2.size()-8 && n<=m.mem2.size()-8-offset)return m.mem2.data()+offset;
    return nullptr;
}
static bool NativeHelperCopy(MMU& m,uint32_t dst,uint32_t src,uint32_t n){
    if(g_watch_addr)return false;
    auto* d=NativeHelperRamSpan(m,dst,n);auto* r=NativeHelperRamSpan(m,src,n);
    if(!d || !r)return false;
    const uintptr_t dp=reinterpret_cast<uintptr_t>(d),sp=reinterpret_cast<uintptr_t>(r);
    const bool overlap=dp<sp+n && sp<dp+n;
    // Guest aliases can reverse the original byte-loop's copy direction.
    // Preserve that behavior by retaining its byte path in those cases.
    if(overlap && dp!=sp && ((src>=dst)!=(sp>=dp)))return false;
    std::memmove(d,r,n);return true;
}
static bool NativeHelperFill(MMU& m,uint32_t dst,uint8_t value,uint32_t n){
    if(g_watch_addr)return false;
    auto* d=NativeHelperRamSpan(m,dst,n);if(!d)return false;
    std::memset(d,value,n);return true;
}
'''
    s=s.replace('static bool try_native_helper(CPUContext& ctx) {',helper+'\nstatic bool try_native_helper(CPUContext& ctx) {',1)
    old='''        if (src >= dst) {
            for (uint32_t i = 0; i < n; ++i)'''
    new='''        if (NativeHelperCopy(ctx.mmu,dst,src,n)) {
            ctx.pc=ctx.lr;return true;
        }
        if (src >= dst) {
            for (uint32_t i = 0; i < n; ++i)'''
    assert s.count(old)==1;s=s.replace(old,new,1)
    old='''        if (n <= 0x2000000u)
            for (uint32_t i = 0; i < n; ++i)'''
    new='''        if(n<=0x2000000u && NativeHelperFill(ctx.mmu,dst,uint8_t(val),n)){
            ctx.pc=ctx.lr;return true;
        }
        if (n <= 0x2000000u)
            for (uint32_t i = 0; i < n; ++i)'''
    assert s.count(old)==1;s=s.replace(old,new,1);f.write_text(s)
print('Existing native helper hooks use bulk RAM copies/fills with alias-direction/watch fallbacks.')
