"""NATIVE_PSQ_F32_PAIR_V1: one RAM guard + one 8-byte load for a plain-F32 paired-single load.

psq_load_pair's ld_type==0 branch (the vertex/matrix F32 loads the GX emitters hammer) did two
separate mmu.read_f32 calls, i.e. two independent address guards. For a W==0 pair whose 8 bytes
lie inside MEM1 they can be one range check and one 8-byte copy; everything else keeps the
original two-read path. Byte-identical: read_f32(addr) == bitcast(bswap32(the 4 bytes at addr)),
and the fallback covers MEM2/MMIO/crossing the MEM1 limit/W!=0.
Build rollback: NWII_BUILD_PSQ_F32_PAIR=0. Idempotent.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_PSQ_F32_PAIR_V1'

OLD = """    if (__builtin_expect(ld_type == 0, 1)) {
      NativePsqPair result;
      result.f = mmu.read_f32(addr);
      result.p = W ? 1. : double(mmu.read_f32(addr + 4));
      return result;
    }"""

NEW = """    if (__builtin_expect(ld_type == 0, 1)) {
      NativePsqPair result;
      // NATIVE_PSQ_F32_PAIR_V1: one guard + one 8-byte load for a W==0 MEM1 pair.
      const uint32_t native_pair_p0 = addr & 0x3fffffffu;
      if (W == 0 && __builtin_expect(native_pair_p0 + 8u <= mmu.kMem1Limit, 1)) {
        uint64_t raw; std::memcpy(&raw, mmu.mem1.data() + native_pair_p0, 8);
        const uint32_t lo = __builtin_bswap32(uint32_t(raw));
        const uint32_t hi = __builtin_bswap32(uint32_t(raw >> 32));
        float f0, f1; std::memcpy(&f0, &lo, 4); std::memcpy(&f1, &hi, 4);
        result.f = f0; result.p = double(f1);
        return result;
      }
      if (W == 0 && __builtin_expect(native_pair_p0 - 0x10000000u + 8u <= mmu.kMem2Limit, 0)) {
        const uint32_t native_pair_o = native_pair_p0 - 0x10000000u;
        uint64_t raw; std::memcpy(&raw, mmu.mem2.data() + native_pair_o, 8);
        const uint32_t lo = __builtin_bswap32(uint32_t(raw));
        const uint32_t hi = __builtin_bswap32(uint32_t(raw >> 32));
        float f0, f1; std::memcpy(&f0, &lo, 4); std::memcpy(&f1, &hi, 4);
        result.f = f0; result.p = double(f1);
        return result;
      }
      result.f = mmu.read_f32(addr);
      result.p = W ? 1. : double(mmu.read_f32(addr + 4));
      return result;
    }"""


def apply(s):
    if MARK in s:
        return s
    if s.count(OLD) != 1:
        raise SystemExit('psq f32 pair anchor matched %d times (expected 1)' % s.count(OLD))
    return s.replace(OLD, NEW, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_PSQ_F32_PAIR', '1') == '0':
        print('psq f32 pair disabled')
        return
    path = args.port / 'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h'
    before = path.read_text()
    after = apply(before)
    if before != after:
        path.write_text(after)
    print('psq f32 pair staged')


if __name__ == '__main__':
    main()
