"""Inline ordinary reads on the sentinel page without changing stores.

The page table reserves bias==1 only for physical page zero. Preserve the
existing common-RAM branch, then handle that marker without an out-of-line
call. Only read32 at exactly physical 0x0C has the legacy zero result.
"""
from pathlib import Path
import argparse

MARKER = 'NATIVE_PAGE_ZERO_READS_V1'

def apply(source: str) -> str:
    page_zero = '    page_bias[0] = 1; // physical 0x0C is special-cased by the slow paths'
    assert source.count(page_zero) == 1, 'Requalify sentinel page policy'
    for width in (8, 16, 32, 64):
        old = f'    return native_read{width}_slow(addr);'
        sentinel = '      if (paddr == 12u) return 0;\n' if width == 32 else ''
        value = 'value' if width == 8 else f'__builtin_bswap{width}(value)'
        fast = (f'    // {MARKER}_{width}: common RAM and all stores stay unchanged.\n'
                '    if (__builtin_expect(bias == 1, 0)) {\n' + sentinel +
                f'      uint{width}_t value;\n'
                f'      std::memcpy(&value, mem1.data() + paddr, {width // 8});\n'
                f'      return {value};\n'
                '    }\n' + old)
        marker = f'{MARKER}_{width}:'
        if marker in source:
            assert source.count(fast) == 1, f'Modified page-zero read{width}'
        else:
            assert source.count(old) == 1, f'Requalify read{width} fallback'
            source = source.replace(old, fast, 1)
    return source

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--header', type=Path, default=Path(__file__).resolve().parents[1]/'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h')
    args = ap.parse_args()
    original = args.header.read_text()
    transformed = apply(original)
    if transformed != original:
        args.header.write_text(transformed)
    print('Page-zero RAM reads inlined; stores and common RAM guards preserved.')
