"""Give ordinary MEM2 the same guarded inline scalar path as MEM1."""
from pathlib import Path
import re
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h';s=f.read_text()
if 'NATIVE_MEM2_INLINE_V1' not in s and 'NATIVE_MMU_PAGE_TABLE_V1' not in s:   # the page-table stage (fix_mmu_page_table.py) supersedes the inline MEM1/MEM2 range checks
    baseline=p/'runtime-overlays/mem2-inline-before.h'
    if not baseline.exists():baseline.write_text(s)
    for method in ('read8','read16','read32','read64','write8','write16','write32','write64','write_f32','write_f64'):
        a=s.index(' inline '+('uint'+method[4:]+'_t ' if method.startswith('read') else 'void ')+method+'(')
        b=s.index('  __attribute__((noinline))',a);body=s[a:b]
        m=re.search(r'    if\s*\(__builtin_expect\(paddr<mem1.size\(\)-8.*?\}\n',body,re.S);assert m,method
        fast=m[0].replace('paddr<mem1.size()-8','mem2offset<mem2.size()-8').replace('mem1.data()+paddr','mem2.data()+mem2offset')
        fast='    // NATIVE_MEM2_INLINE_V1: same start bounds/padding and watch fallback.\n    const uint32_t mem2offset=paddr-0x10000000u;\n'+fast
        at=m.end();body=body[:at]+fast+body[at:];s=s[:a]+body+s[b:]
    a=s.index('  double read_f64(uint32_t addr) {');b=s.index('\n  }',a)+4
    s=s[:a]+'''  __attribute__((always_inline)) inline double read_f64(uint32_t addr) {
      uint64_t value=read64(addr);double result;std::memcpy(&result,&value,8);return result;
  }'''+s[b:]
    f.write_text(s)
print('MEM2 scalar RAM accesses inline; original watches/MMIO/invalid paths retained.')
