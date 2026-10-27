"""NWII_STATIC_RAM_BASE: guest MEM1/MEM2 at fixed host addresses so every guest load/store is [index + constant].

Default build: unchanged (the std::vector members stay). With -DNWII_STATIC_RAM_BASE (see build_restrict_variant.py) MMU::mem1 and
MMU::mem2 become NativeFixedRam objects whose data() is the constant 0x20000000 (MEM1, 24 MiB + 8) and 0x30000000 (MEM2,
64 MiB + 8), committed once with VirtualAlloc by runtime-overlays/static_ram_base.cpp. Because 0x30000000 = 0x20000000 + 0x10000000,
the inline accessors' `mem2.data() + (paddr - 0x10000000)` folds to paddr + 0x20000000 as well: both banks compile to a single
disp32 addressing mode with no pointer load from the context. The sizes, +8 padding, bounds checks and MMIO/watch fallbacks are the
same. If the fixed range is not available the process aborts with a message and the normal executable should be used.

Source changes that apply to both builds (behaviour-preserving, generic over vector or NativeFixedRam):
  hw_dsp.cpp: the AX bank selection no longer binds a reference to one of two different types.
  fifo_parser.cpp: the span lambda takes `const auto&` instead of `const std::vector<uint8_t>&`.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'

f=rt/'src/hw/hw_dsp.cpp';s=f.read_text()
old='''        auto& bank=p>=0x10000000u?g_mmu->mem2:g_mmu->mem1;
        size_t offset=p>=0x10000000u?p-0x10000000u:p;
        size_t limit=bank.size()-8;
        return offset<=limit&&size<=limit-offset?bank.data()+offset:nullptr;'''
new='''        const bool second_bank=p>=0x10000000u; // NATIVE_STATIC_RAM_V1: bank-type agnostic
        uint8_t* const bank_data=second_bank?g_mmu->mem2.data():g_mmu->mem1.data();
        size_t offset=second_bank?p-0x10000000u:p;
        size_t limit=(second_bank?g_mmu->mem2.size():g_mmu->mem1.size())-8;
        return offset<=limit&&size<=limit-offset?bank_data+offset:nullptr;'''
if 'NATIVE_STATIC_RAM_V1' not in s:
    assert s.count(old)==1;f.write_text(s.replace(old,new))

f=rt/'src/hle/gx/fifo_parser.cpp';s=f.read_text()
old='auto valid=[&](uint64_t start,const std::vector<uint8_t>& bank){'
if old in s:
    assert s.count(old)==1;f.write_text(s.replace(old,'auto valid=[&](uint64_t start,const auto& bank){'))

f=rt/'include/runtime/cpu_context.h';s=f.read_text()
if 'NATIVE_STATIC_RAM_V1' not in s:
    old='''  std::vector<uint8_t> mem1;
  std::vector<uint8_t> mem2;

  MMU() {
    mem1.resize(24 * 1024 * 1024 + 8, 0);
    mem2.resize(64 * 1024 * 1024 + 8, 0);
  }
'''
    new='''#ifdef NWII_STATIC_RAM_BASE
  // NATIVE_STATIC_RAM_V1: see tools/fix_static_ram_base.py.
  NativeFixedRam<0x20000000u,24u*1024u*1024u+8u> mem1;
  NativeFixedRam<0x30000000u,64u*1024u*1024u+8u> mem2;

  MMU() {
    native_static_ram_commit(0x20000000u,24u*1024u*1024u+8u);
    native_static_ram_commit(0x30000000u,64u*1024u*1024u+8u);
  }
#else
  std::vector<uint8_t> mem1;
  std::vector<uint8_t> mem2;

  MMU() {
    mem1.resize(24 * 1024 * 1024 + 8, 0);
    mem2.resize(64 * 1024 * 1024 + 8, 0);
  }
#endif
'''
    lines=s.split(chr(10));want=["std::vector<uint8_t> mem1;","std::vector<uint8_t> mem2;","","MMU() {","mem1.resize(24 * 1024 * 1024 + 8, 0);","mem2.resize(64 * 1024 * 1024 + 8, 0);","}"]
    at=[i for i in range(len(lines)-len(want)) if [x.strip() for x in lines[i:i+len(want)]]==want]
    assert len(at)==1,"MMU members changed: requalify"
    lines[at[0]:at[0]+len(want)]=new.rstrip(chr(10)).split(chr(10));s=chr(10).join(lines)
    helper='''#ifdef NWII_STATIC_RAM_BASE
// NATIVE_STATIC_RAM_V1: a RAM bank whose base address is a compile-time constant.
void native_static_ram_commit(uintptr_t base, size_t bytes);
template<uintptr_t Base,size_t Bytes> struct NativeFixedRam {
  uint8_t* data() const { return reinterpret_cast<uint8_t*>(Base); }
  static constexpr size_t size() { return Bytes; }
  uint8_t& operator[](size_t index) const { return data()[index]; }
  uint8_t* begin() const { return data(); }
  uint8_t* end() const { return data()+Bytes; }
};
#endif
'''
    a=s.index('struct MMU {')
    s=s[:a]+helper+s[a:]
    f.write_text(s)

cm=p/'build/nwii-full-source/CMakeLists.txt';t=cm.read_text()
if 'NATIVE_STATIC_RAM_V1' not in t:
    t+='\n# NATIVE_STATIC_RAM_V1\nif(MINGW)\n  target_sources(nwiiruntime PRIVATE "'+(p/'runtime-overlays/static_ram_base.cpp').as_posix()+'")\nendif()\n'
    cm.write_text(t)
print('static RAM base stage installed (active only with -DNWII_STATIC_RAM_BASE)')
