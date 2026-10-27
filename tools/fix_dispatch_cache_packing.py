"""Pack only the internal exact-tag dispatcher cache; preserve public choices."""
from pathlib import Path
import re

MARKER='NATIVE_PACKED_DISPATCH_CACHE_V1'

def inline_text(source):
    """Inline just the tag check; the expensive cold resolver remains a call."""
    marker='NATIVE_INLINE_HOT_DISPATCH_V1'
    if marker in source:return source,False
    signature='static DispatchChoice find_dispatch_target(uint32_t target) {'
    if source.count(signature)!=1:raise ValueError('Expected one hot dispatch lookup wrapper')
    replacement='// '+marker+': avoid the 24-byte return-object call ABI.\nstatic __attribute__((always_inline)) inline DispatchChoice find_dispatch_target(uint32_t target) {'
    return source.replace(signature,replacement,1),True

def patch_text(source):
    if MARKER in source:return source,False
    pattern=re.compile(r'static DispatchChoice find_dispatch_target\(uint32_t target\) \{\n'
        r'    static std::array<DispatchChoice,4096> cache\{\};\n'
        r'    auto& entry=cache\[\(\(target>>2\)\^\(target>>14\)\)&4095u\];\n'
        r'    if\(entry.pc==target\)return entry;\n'
        r'    entry=(find_dispatch_target_(?:dense|uncached))\(target\);return entry;\n\}')
    match=pattern.search(source)
    if not match:raise ValueError('Expected exact-tag dense/uncached dispatch cache shape; requalify before patching')
    replacement='''// NATIVE_PACKED_DISPATCH_CACHE_V1: 16-byte hot records, unchanged choice ABI.
struct NativeHotDispatchRecord {
    void(*func)(CPUContext&)=nullptr;
    uint32_t pc=0;
    bool hle=false;
};
static_assert(sizeof(NativeHotDispatchRecord)<=sizeof(void(*)(CPUContext&))+8);
static DispatchChoice find_dispatch_target(uint32_t target) {
    static std::array<NativeHotDispatchRecord,4096> cache{};
    auto& entry=cache[((target>>2)^(target>>14))&4095u];
    if(entry.pc==target)return {target,entry.func,entry.hle};
    const auto result=RESOLVER(target);
    entry={result.func,result.pc,result.hle};return result;
}'''.replace('RESOLVER',match[1])
    return source[:match.start()]+replacement+source[match.end():],True

def main():
    port=Path(__file__).resolve().parents[1]
    path=port/'build/crash-generated/main_output.cpp'
    original=path.read_text();patched,changed=patch_text(original)
    if changed:path.write_text(patched)
    print('Dispatch hot records packed; choice ABI, hash, and cold resolution preserved.')

if __name__=='__main__':main()
