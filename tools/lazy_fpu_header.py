"""Compose the CPU header without changing timestamps on unchanged builds."""
FRAGMENT='''  // NATIVE_LAZY_FPU: physical FPU ownership is managed by the guest SDK.
  uint32_t hid2 = 0;
  static bool instruction_uses_fpu(uint32_t w) {
    unsigned op=w>>26, xo=(w>>1)&1023;
    return (op==4 && xo!=1014) || (op>=48 && op<=57) ||
           op==59 || op==60 || op==61 || op==63 ||
           (op==31 && (xo==535 || xo==567 || xo==599 || xo==631 ||
                       xo==663 || xo==695 || xo==727 || xo==759 || xo==983));
  }
  bool require_fpu(uint32_t fault_pc) {
    if (msr & 0x2000u) return false;
    // Hardware exception entry: retry faulting instruction after guest rfi.
    srr0=fault_pc; srr1=msr & 0x87C0FFFFu;
    msr=(msr & ~1u) | ((msr>>16)&1u);
    msr &= ~0x04EF36u;
    pc=0x800u;
    return true;
  }
'''
def inject_lazy_fpu_header(s):
    if '// NATIVE_LAZY_FPU' in s:return s
    needle='  uint32_t msr;'
    assert s.count(needle)==2
    return s.replace(needle,FRAGMENT+needle,1)
