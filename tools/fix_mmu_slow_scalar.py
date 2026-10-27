"""Use alias-safe wide loads for qualified slow RAM reads.

The existing MMIO dispatch, 0x0C sentinel, address translation and null tests
remain authoritative. memcpy permits unaligned RAM and preserves padded-end
reads without invoking alignment or strict-aliasing undefined behavior.
"""
from pathlib import Path
import argparse

OLD32 = '''    uint32_t val = ((uint32_t)ptr[0] << 24) | ((uint32_t)ptr[1] << 16) | ((uint32_t)ptr[2] << 8) | ptr[3];

    return val;'''
NEW32 = '''    // NATIVE_MMU_SLOW_SCALAR_V1: unaligned, alias-safe host load.
    uint32_t val;
    std::memcpy(&val, ptr, sizeof(val));
    return __builtin_bswap32(val);'''
OLD16 = '''    if (ptr) return (ptr[0] << 8) | ptr[1];
    return 0;'''
NEW16 = '''    // NATIVE_MMU_SLOW_SCALAR16_V1: preserve MMIO dispatch above.
    if (!ptr) return 0;
    uint16_t val;
    std::memcpy(&val, ptr, sizeof(val));
    return __builtin_bswap16(val);'''
OLD64 = '''    return ((uint64_t)ptr[0] << 56) | ((uint64_t)ptr[1] << 48) | ((uint64_t)ptr[2] << 40) | ((uint64_t)ptr[3] << 32) |
           ((uint64_t)ptr[4] << 24) | ((uint64_t)ptr[5] << 16) | ((uint64_t)ptr[6] << 8) | ptr[7];'''
NEW64 = '''    // NATIVE_MMU_SLOW_SCALAR64_V1: keep original RAM-only policy.
    uint64_t val;
    std::memcpy(&val, ptr, sizeof(val));
    return __builtin_bswap64(val);'''

def apply(source: str) -> str:
    for width, old, new in ((32, OLD32, NEW32), (16, OLD16, NEW16), (64, OLD64, NEW64)):
        marker = new.split(':', 1)[0].strip().removeprefix('// ')
        if marker in source:
            assert source.count(new) == 1, f'modified scalar{width} marker body'
        else:
            assert source.count(old) == 1, f'slow read{width} source changed'
            source = source.replace(old, new, 1)
    return source

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--header', type=Path, default=Path(__file__).resolve().parents[1]/'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h')
    args = ap.parse_args()
    source = args.header.read_text()
    result = apply(source)
    if result != source:
        args.header.write_text(result)
    print('MMU slow scalar read qualified wide load ready.')
