"""Complete the existing cached ordinary-FIFO path for 16- and 64-bit writes.

Only append packets strictly below the existing 4096-byte publish boundary.
All boundary, redirect and diagnostic cases retain the existing implementation.
The shared cached flag is invalidated by the already-qualified WPAR/PI/CP
register writers; this stage adds no new cache or state.
"""
from pathlib import Path
import argparse

MARKER = 'NATIVE_FIFO_WIDE_FLAG_V1'

def apply(source: str) -> str:
    assert 'bool g_native_fifo_ok = false;' in source, 'FIFO proof flag missing'
    assert 'NATIVE_FIFO_INLINE_STORE_V1' in source, 'Fixed FIFO byte store missing'
    for width, old, value in (
        (16, 'void GX_WGPIPE_Write16(uint16_t val) {\n', 'val'),
        (64, 'void GX_WGPIPE_WriteF64(double val) {\n    union { double d; uint64_t i; } u; u.d = val;\n', 'u.i'),
    ):
        marker = f'{MARKER}_{width}'
        fast = (f'    if (__builtin_expect(g_native_fifo_ok, 1)) {{ // {marker}\n'
                '        const size_t at = g_cpu_fifo.n;\n'
                f'        if (at + {width // 8} < 4096) {{ const uint{width}_t be = __builtin_bswap{width}({value}); '
                f'std::memcpy(g_cpu_fifo.data + at, &be, {width // 8}); g_cpu_fifo.n = at + {width // 8}; return; }}\n'
                '    } else if (native_ordinary_fifo_fast()) g_native_fifo_ok = true;\n')
        if marker in source:
            assert source.count(old + fast) == 1, f'Modified FIFO{width} fast path'
        else:
            assert source.count(old) == 1, f'FIFO{width} source changed'
            source = source.replace(old, old + fast, 1)
    return source

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1]/'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp')
    args = ap.parse_args()
    before = args.source.read_text(errors='surrogateescape')
    after = apply(before)
    if after != before:
        args.source.write_text(after, errors='surrogateescape')
    print('Cached ordinary FIFO proof applied to 16- and 64-bit packet stores.')
