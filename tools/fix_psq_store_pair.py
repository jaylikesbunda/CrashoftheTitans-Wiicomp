"""NATIVE_PSQ_F32_STORE_PAIR_V1: one RAM guard + one 8-byte store for a plain-F32 paired-single store.

psq_store_val's st_type==0 branch (the GX emitters' GQR0 vertex writes) did two separate
mmu.write_f32 calls, i.e. two independent address guards, for a W==0 pair. When both halves lie
inside MEM1 and neither the page-watch nor the span-watch can observe them, they can be one
range check and one 8-byte copy; everything else keeps the original two-call path.
Byte-identical: write_f32(a,v) stores bswap32(bitcast(v)) little-endian, so the 8-byte value is
(bswap32(bitcast(f1)) << 32) | bswap32(bitcast(f0)).
Build rollback: NWII_BUILD_PSQ_F32_STORE_PAIR=0. Idempotent.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_PSQ_STORE_FLAT_V1'

OLD = """      if(!W){const uint32_t physical=addr&0x3fffffffu;const intptr_t bias=mmu.page_bias[physical>>16];
        if(bias>1 && (physical&65535u)<=65528u && !native_watch_span_may_hit(physical,8)){
          const float a=float(fval),b=float(pval);uint32_t av,bv;std::memcpy(&av,&a,4);std::memcpy(&bv,&b,4);
          uint64_t bits=__builtin_bswap64((uint64_t(av)<<32)|bv);std::memcpy(reinterpret_cast<uint8_t*>(bias+physical),&bits,8);return;
        }"""

NEW = """      if(!W){const uint32_t physical=addr&0x3fffffffu;
        // NATIVE_PSQ_STORE_FLAT_V1: flat RAM base; no dependent page_bias load. Page 0 (table
        // value 1) resolves to mem1 too, so only the watch span still diverts.
        if(physical+8u<=mmu.kMem1Limit && !native_watch_span_may_hit(physical,8)){
          const float a=float(fval),b=float(pval);uint32_t av,bv;std::memcpy(&av,&a,4);std::memcpy(&bv,&b,4);
          uint64_t bits=__builtin_bswap64((uint64_t(av)<<32)|bv);std::memcpy(mmu.mem1.data()+physical,&bits,8);return;
        }"""


def apply(s):
    if MARK in s:
        return s
    if s.count(OLD) != 1:
        raise SystemExit('psq f32 store pair anchor matched %d times (expected 1)' % s.count(OLD))
    return s.replace(OLD, NEW, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_PSQ_F32_STORE_PAIR', '1') == '0':
        print('psq f32 store pair disabled')
        return
    path = args.port / 'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h'
    before = path.read_text()
    after = apply(before)
    if before != after:
        path.write_text(after)
    print('psq f32 store pair staged')


if __name__ == '__main__':
    main()
