"""Cache exact-address region indices; preserve first registration and callbacks."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
h=rt/'include/runtime/hw/mmio_dispatcher.h';c=rt/'src/hw/mmio_dispatcher.cpp'
s=h.read_text();t=c.read_text();marker='NATIVE_MMIO_EXACT_LOOKUP_V1'
if marker not in s:
    (p/'runtime-overlays/mmio-lookup-before.h').write_text(s)
    (p/'runtime-overlays/mmio-lookup-before.cpp').write_text(t)
    s=s.replace('#include <vector>','#include <vector>\n#include <array>\n#include <cstddef>')
    s=s.replace('    std::vector<MMIORegion> m_regions;',r'''    std::vector<MMIORegion> m_regions;
    // NATIVE_MMIO_EXACT_LOOKUP_V1: indices survive vector growth; mutations invalidate.
    struct LookupEntry { uint32_t address=0; size_t index=0; bool valid=false; };
    std::array<LookupEntry,256> m_lookup{};
    void invalidate_lookup(){for(auto& entry:m_lookup)entry.valid=false;}
    const MMIORegion* find_region(uint32_t address) {
        auto& entry=m_lookup[((address>>2)^(address>>10))&255u];
        if(entry.valid && entry.address==address)return entry.index<m_regions.size()?&m_regions[entry.index]:nullptr;
        size_t index=0;
        for(;index<m_regions.size();++index){const auto& region=m_regions[index];if(address>=region.start_addr && address<=region.end_addr)break;}
        entry={address,index,true};
        return index<m_regions.size()?&m_regions[index]:nullptr;
    }''')
    t=t.replace('    m_regions.push_back(', '    invalidate_lookup();\n    m_regions.push_back(')
    t=t.replace('    m_regions.clear();','    invalidate_lookup();\n    m_regions.clear();')
    import re
    pattern=r'    for \(const auto& region : m_regions\) \{\n        if \(addr >= region.start_addr && addr <= region.end_addr\) \{(.*?)\n        \}\n    \}'
    t,n=re.subn(pattern,lambda m:'    if (const auto* selected=find_region(addr)) {\n        const auto& region=*selected;'+m[1]+'\n    }',t,flags=re.S)
    assert n==4
    h.write_text(s);c.write_text(t)
# The first registered region already takes one comparison pair. Do not pay a
# cache hash/load on this common CP path (offline negative fixture exposed it).
s=h.read_text()
if 'NATIVE_MMIO_FIRST_REGION_V1' not in s:
    s=s.replace('    const MMIORegion* find_region(uint32_t address) {','''    const MMIORegion* find_region(uint32_t address) {
        // NATIVE_MMIO_FIRST_REGION_V1: keep the original first-region fast path.
        if(!m_regions.empty() && address>=m_regions[0].start_addr && address<=m_regions[0].end_addr)return &m_regions[0];''',1)
    h.write_text(s)
print('Exact-address MMIO cache staged; first-region path retained; mutations invalidate.')
