"""Inline the small exact-tag dispatch front end, leaving cold resolution alone."""
from pathlib import Path

MARKER='NATIVE_INLINE_HOT_DISPATCH_V1'

def patch_text(source):
    if MARKER in source:return source,False
    signature='static DispatchChoice find_dispatch_target(uint32_t target) {'
    if source.count(signature)!=1:raise ValueError('Expected one dispatch cache front end')
    start=source.index(signature);opening=source.index('{',start);depth=1;end=opening+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    body=source[opening:end]
    if len(body)>1200 or 'static std::array<' not in body or 'cache[' not in body:
        raise ValueError('Dispatch front end is not a small exact-tag cache; requalify')
    replacement='// '+MARKER+': avoid the 24-byte return-object call ABI.\nstatic __attribute__((always_inline)) inline DispatchChoice find_dispatch_target(uint32_t target) {'
    return source.replace(signature,replacement,1),True

def main():
    port=Path(__file__).resolve().parents[1]
    path=port/'build/crash-generated/main_output.cpp'
    original=path.read_text();patched,changed=patch_text(original)
    if changed:path.write_text(patched)
    print('Small dispatch tag check inlined; cold resolvers and cache layout unchanged.')

if __name__=='__main__':main()
