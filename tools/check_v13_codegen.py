"""Check control-flow boundaries and reversible V13 guest rewrites."""
from v13_codegen import transform,restore

def main():
    source='''void func_80001000(NWII_CTX_PARAM ctx) {
    uint32_t g14, g3;
    g14 = ctx.gpr[14]; g3 = ctx.gpr[3];
    if (ctx.require_fpu(0x80001000)) return; // MSR.FP exception
    if (ctx.require_fpu(0x80001004)) return; // MSR.FP exception
    goto join;
join: ;
    if (ctx.require_fpu(0x80001008)) return; // MSR.FP exception
    ctx.pc = 0x80002000; func_80002000(ctx);
    g14 = ctx.gpr[14]; g3 = ctx.gpr[3];
    if (ctx.pc != 0x80001010) return;
    if (ctx.require_fpu(0x80001010)) return; // MSR.FP exception
    ctx.mmu.write32(g1 + 16, g14);
    // 0x80001014: 48000FEC
    ctx.pc = 0x80002000; return;
}
'''
    changed,stats=transform(source,{0x80002000:{'r3'}},{0x80002000},set(),None,True,True)
    assert stats==dict(reloads=1,fp_guards=1,tails=1,nested=0,stack=1),stats
    assert 'g3 = ctx.gpr[3]; if(native_guest_fast::verify_trim' in changed
    assert restore(changed)==source
    promoted=source.replace(')) return; // MSR.FP', ')) goto exit_0; // MSR.FP')
    changed_promoted,counts=transform(promoted,{},set(),set(),None)
    assert counts['fp_guards']==1 and restore(changed_promoted)==promoted
    assert transform(changed,{0x80002000:{'r3'}},{0x80002000},set(),None,True,True)[0]==changed
    unknown,counts=transform(source,{},set(),set(),None,True,False)
    assert counts['reloads']==counts['tails']==counts['stack']==0
    assert restore(unknown)==source
    overridden,counts=transform(source,{}, {0x80002000},{0x80002000},None)
    assert counts['tails']==0
    nested=source.replace('48000FEC','4E800420').replace('ctx.pc = 0x80002000; return;','ctx.pc = ctx.ctr; return;')
    changed,counts=transform(nested,{},set(),set(),None)
    assert counts['nested']==1 and 'native_dispatch_nested(ctx)' in changed and restore(changed)==nested
    print('V13 codegen checks passed: FPU joins/calls, selective reloads, unknown/override targets, tails, nested dispatch and reversible stores.')

if __name__=='__main__':main()
