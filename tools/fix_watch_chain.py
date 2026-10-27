"""Add the current thread and guest call chain to NWII_WATCH output.

A watched guest word (for example the OS scheduler-disable counter) is logged
with pc/lr only, which names the leaf function but not who called it. For
unbalanced-counter hunts the caller chain is the information needed.
"""
from pathlib import Path
port=Path(__file__).resolve().parents[1]
path=port/'build/nwii-full-source/nWiiRuntime/src/core/os_hle_stubs.cpp'
text=path.read_text()
marker='NATIVE_WATCH_CHAIN_V1'
if marker not in text:
    old='''    std::cout << " pc=0x" << c->pc << " lr=0x" << c->lr;
'''
    new='''    std::cout << " pc=0x" << c->pc << " lr=0x" << c->lr;
    { // NATIVE_WATCH_CHAIN_V1
      std::cout << " cur=0x" << c->mmu.read32(0x800000E4) << " chain:";
      uint32_t s = c->gpr[1];
      for (int i = 0; i < 7; ++i) {
        if ((s >> 28) != 8 && (s >> 28) != 9) break;
        uint32_t nx = c->mmu.read32(s);
        if ((nx >> 28) != 8 && (nx >> 28) != 9) break;
        if (nx <= s) break;
        std::cout << " " << c->mmu.read32(nx + 4);
        s = nx;
      }
    }
'''
    assert text.count(old)==1
    path.write_text(text.replace(old,new,1))
print('NWII_WATCH now logs current thread and call chain.')
