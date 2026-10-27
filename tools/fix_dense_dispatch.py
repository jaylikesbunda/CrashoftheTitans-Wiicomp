"""Constant-time immutable word-PC dispatch with exact irregular-PC fallback."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]
f=p/'build/crash-generated/main_output.cpp';s=f.read_text()
if 'NATIVE_DENSE_DISPATCH_V1' not in s:
 baseline=p/'runtime-overlays/dense-dispatch-before.cpp'
 if not baseline.exists():baseline.write_text(s)
 a=s.index('// NATIVE_DISPATCH_CACHE_V1:');b=s.index('\nnamespace nwii::runtime { bool native_wpad_dispatch',a)
 helper='''// NATIVE_DENSE_DISPATCH_V1: immutable 16-bit indices, not guest pointers.
static DispatchChoice find_dispatch_target(uint32_t target) {
    constexpr size_t count=std::size(g_func_bounds)-1;
    constexpr size_t overrides=std::size(g_dispatch_overrides)-1;
    static_assert(count+overrides<65536);
    constexpr uint32_t low=g_func_bounds[0].start;
    static const uint32_t high=[](){uint32_t h=low;for(auto& b:g_func_bounds)if(b.end>h)h=b.end;return h;}();
    static const auto words=[](){
        std::vector<uint16_t> table((high-low+3u)/4u);
        // Reverse fill retains the original first-containing-range priority.
        for(size_t i=count;i-->0;){auto& b=g_func_bounds[i];
            const size_t first=(uint64_t(b.start)-low+3u)/4u;
            const size_t end=(uint64_t(b.end)-low+3u)/4u;
            std::fill(table.begin()+first,table.begin()+end,uint16_t(i+1));
        }
        for(size_t i=0;i<overrides;++i){auto& o=g_dispatch_overrides[i];
            if(!(o.pc&3u) && o.pc>=low && o.pc<high)table[(o.pc-low)/4u]=uint16_t(count+i+1);
        }
        return table;
    }();
    const uint32_t offset=target-low;
    if((target&3u) || offset>=high-low)return find_dispatch_target_uncached(target);
    const unsigned id=words[offset/4u];
    if(id>count){auto out=g_dispatch_overrides[id-count-1];out.pc=target;return out;}
    return {target,id?g_func_bounds[id-1].func:nullptr,false};
}
'''
 # Bounds are compile-time records; required for the constant low address.
 s=s.replace('static const FuncBound g_func_bounds[]', 'static constexpr FuncBound g_func_bounds[]',1)
 # Replacement above changes the offset. Locate the resolver anew.
 a=s.index('// NATIVE_DISPATCH_CACHE_V1:') if '// NATIVE_DISPATCH_CACHE_V1:' in s else -1
 if a>=0:
  b=s.index('\nnamespace nwii::runtime { bool native_wpad_dispatch',a);s=s[:a]+helper+s[b:]
 if '#include <vector>' not in s:s='#include <vector>\n#include <algorithm>\n'+s
 f.write_text(s)
print('Dense immutable word-PC dispatch staged, with overlap/override/gap/interior fallbacks.')

# Preserve the faster original cache-hit path; replace only expensive misses.
s=f.read_text()
if 'NATIVE_DENSE_HOT_CACHE_V1' not in s:
 s=s.replace('static DispatchChoice find_dispatch_target(uint32_t target) {\n    constexpr size_t count=', 'static DispatchChoice find_dispatch_target_dense(uint32_t target) {\n    constexpr size_t count=',1)
 anchor='\nnamespace nwii::runtime { bool native_wpad_dispatch'
 wrapper='''
// NATIVE_DENSE_HOT_CACHE_V1: original exact-tag hot path, dense cold resolver.
static DispatchChoice find_dispatch_target(uint32_t target) {
    static std::array<DispatchChoice,4096> cache{};
    auto& entry=cache[((target>>2)^(target>>14))&4095u];
    if(entry.pc==target)return entry;
    entry=find_dispatch_target_dense(target);return entry;
}
'''
 assert s.count(anchor)==1;s=s.replace(anchor,wrapper+anchor,1);f.write_text(s)
print('Original hot dispatch cache retained; misses use the dense immutable table.')
