"""NATIVE_MMU_FLAT_RAM_V1: remove the dependent page_bias load from the hot RAM accessors.

The inline accessors compute `bias = page_bias[paddr>>16]` and then address memory as
`bias + paddr`. That second load's *address* depends on the first load's result, so every
guest access carries a 2-deep dependent load chain (the dominant cost in the guest-thread
profile: mmu.address_guard 16.3%).

For MEM1 pages the table value is exactly `mem1.data()`, and for MEM2 pages it is exactly
`mem2.data() - 0x10000000`, so `bias + paddr` is identical to `mem1.data() + paddr` /
`mem2.data() + (paddr - 0x10000000)`. The memory base has a fixed address (context offset
or, with NWII_STATIC_RAM_BASE, a constant), so its load is *independent* of paddr and the
dependency chain disappears. Reads never observe the write watch, so the read path is
unconditionally equivalent. Writes keep the watch semantics: with NWII_WATCH_PAGE_MARKING
(defined in the shipping build) the single watched 64 KiB page is diverted to the slow path
exactly as the table did (bias==0); without it the original span test is kept.

Page 0 (paddr < 0x10000, table value 1) and the MMIO/0x0C008000 mirror keep the original
slow/mirror handling. Idempotent; rollback NWII_BUILD_MMU_FLAT_RAM=0.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_MMU_FLAT_RAM_V1'

REPLACEMENTS = [
    # ---- read8 ----
    (
        """    const uint32_t paddr=addr&0x3fffffffu;
    const intptr_t bias=page_bias[paddr>>16]; // NATIVE_MMU_PAGE_TABLE_V1
    if (__builtin_expect(bias>1,1)) { uint8_t value; std::memcpy(&value,reinterpret_cast<const uint8_t*>(bias+paddr),1); return value; }
    // NATIVE_PAGE_ZERO_READS_V1_8: common RAM and all stores stay unchanged.
    if (__builtin_expect(bias == 1, 0)) {
      uint8_t value;
      std::memcpy(&value, mem1.data() + paddr, 1);
      return value;
    }
    return native_read8_slow(addr);""",
        """    const uint32_t paddr=addr&0x3fffffffu;
    // NATIVE_MMU_FLAT_RAM_V1: RAM is [base + paddr]; no dependent page_bias load.
    if (__builtin_expect(paddr<kMem1Limit,1)) { uint8_t value; std::memcpy(&value,mem1.data()+paddr,1); return value; }
    if (__builtin_expect(paddr-0x10000000u<kMem2Limit,0)) { uint8_t value; std::memcpy(&value,mem2.data()+(paddr-0x10000000u),1); return value; }
    return native_read8_slow(addr);""",
    ),
    # ---- read16 ----
    (
        """    const uint32_t paddr=addr&0x3fffffffu;
    const intptr_t bias=page_bias[paddr>>16]; // NATIVE_MMU_PAGE_TABLE_V1
    if (__builtin_expect(bias>1,1)) { uint16_t value; std::memcpy(&value,reinterpret_cast<const uint8_t*>(bias+paddr),2); return __builtin_bswap16(value); }
    // NATIVE_PAGE_ZERO_READS_V1_16: common RAM and all stores stay unchanged.
    if (__builtin_expect(bias == 1, 0)) {
      uint16_t value;
      std::memcpy(&value, mem1.data() + paddr, 2);
      return __builtin_bswap16(value);
    }
    return native_read16_slow(addr);""",
        """    const uint32_t paddr=addr&0x3fffffffu;
    // NATIVE_MMU_FLAT_RAM_V1: RAM is [base + paddr]; no dependent page_bias load.
    if (__builtin_expect(paddr<kMem1Limit,1)) { uint16_t value; std::memcpy(&value,mem1.data()+paddr,2); return __builtin_bswap16(value); }
    if (__builtin_expect(paddr-0x10000000u<kMem2Limit,0)) { uint16_t value; std::memcpy(&value,mem2.data()+(paddr-0x10000000u),2); return __builtin_bswap16(value); }
    return native_read16_slow(addr);""",
    ),
    # ---- read32 ----
    (
        """    const uint32_t paddr=addr&0x3fffffffu;
    const intptr_t bias=page_bias[paddr>>16]; // NATIVE_MMU_PAGE_TABLE_V1
    if (__builtin_expect(bias>1,1)) { uint32_t value; std::memcpy(&value,reinterpret_cast<const uint8_t*>(bias+paddr),4); return __builtin_bswap32(value); }
    // NATIVE_PAGE_ZERO_READS_V1_32: common RAM and all stores stay unchanged.
    if (__builtin_expect(bias == 1, 0)) {
      if (paddr == 12u) return 0;
      uint32_t value;
      std::memcpy(&value, mem1.data() + paddr, 4);
      return __builtin_bswap32(value);
    }
    return native_read32_slow(addr);""",
        """    const uint32_t paddr=addr&0x3fffffffu;
    // NATIVE_MMU_FLAT_RAM_V1: RAM is [base + paddr]; no dependent page_bias load.
    if (__builtin_expect(paddr<kMem1Limit,1)) {
      if (__builtin_expect(paddr==12u,0)) return 0;
      uint32_t value; std::memcpy(&value,mem1.data()+paddr,4); return __builtin_bswap32(value);
    }
    if (__builtin_expect(paddr-0x10000000u<kMem2Limit,0)) { uint32_t value; std::memcpy(&value,mem2.data()+(paddr-0x10000000u),4); return __builtin_bswap32(value); }
    return native_read32_slow(addr);""",
    ),
    # ---- write8 ----
    (
        """    const uint32_t paddr=addr&0x3fffffffu;
    const intptr_t bias=page_bias[paddr>>16]; // NATIVE_MMU_PAGE_TABLE_V1
    if(__builtin_expect(bias>1 && NWII_WATCH_CLEAR(paddr,1),1)) {
       uint8_t bits=value; std::memcpy(reinterpret_cast<uint8_t*>(bias+paddr),&bits,sizeof(bits));return;
    }""",
        """    const uint32_t paddr=addr&0x3fffffffu;
    // NATIVE_MMU_FLAT_RAM_V1: RAM writes skip the table; the watched page still goes slow.
    if(__builtin_expect(paddr-0x10000u<kMem1Limit-0x10000u,1)) {
#ifdef NWII_WATCH_PAGE_MARKING
       if(__builtin_expect((paddr>>16)==((g_watch_addr&0x3fffffffu)>>16),0)){native_write8_slow(addr,value);return;}
#else
       if(!NWII_WATCH_CLEAR(paddr,1)){native_write8_slow(addr,value);return;}
#endif
       uint8_t bits=value; std::memcpy(mem1.data()+paddr,&bits,sizeof(bits));return;
    }
    if(__builtin_expect(paddr-0x10000000u<kMem2Limit,0)) {
       uint8_t bits=value; std::memcpy(mem2.data()+(paddr-0x10000000u),&bits,sizeof(bits));return;
    }""",
    ),
    # ---- write16 ----
    (
        """    const uint32_t paddr=addr&0x3fffffffu;
    const intptr_t bias=page_bias[paddr>>16]; // NATIVE_MMU_PAGE_TABLE_V1
    if(__builtin_expect(bias>1 && NWII_WATCH_CLEAR(paddr,2),1)) {
       uint16_t bits=value;bits=__builtin_bswap16(bits); std::memcpy(reinterpret_cast<uint8_t*>(bias+paddr),&bits,sizeof(bits));return;
    }""",
        """    const uint32_t paddr=addr&0x3fffffffu;
    // NATIVE_MMU_FLAT_RAM_V1: RAM writes skip the table; the watched page still goes slow.
    if(__builtin_expect(paddr-0x10000u<kMem1Limit-0x10000u,1)) {
#ifdef NWII_WATCH_PAGE_MARKING
       if(__builtin_expect((paddr>>16)==((g_watch_addr&0x3fffffffu)>>16),0)){native_write16_slow(addr,value);return;}
#else
       if(!NWII_WATCH_CLEAR(paddr,2)){native_write16_slow(addr,value);return;}
#endif
       uint16_t bits=__builtin_bswap16(value); std::memcpy(mem1.data()+paddr,&bits,sizeof(bits));return;
    }
    if(__builtin_expect(paddr-0x10000000u<kMem2Limit,0)) {
       uint16_t bits=__builtin_bswap16(value); std::memcpy(mem2.data()+(paddr-0x10000000u),&bits,sizeof(bits));return;
    }""",
    ),
    # ---- write_f32 (the GX vertex emitters' stfs path) ----
    (
        """    const uint32_t paddr=addr&0x3fffffffu;
    const intptr_t bias=page_bias[paddr>>16]; // NATIVE_MMU_PAGE_TABLE_V1
    if(__builtin_expect(bias>1 && NWII_WATCH_CLEAR(paddr,4),1)) {
       uint32_t bits;std::memcpy(&bits,&value,sizeof(bits));bits=__builtin_bswap32(bits); std::memcpy(reinterpret_cast<uint8_t*>(bias+paddr),&bits,sizeof(bits));return;
    }""",
        """    const uint32_t paddr=addr&0x3fffffffu;
    // NATIVE_MMU_FLAT_RAM_V1: RAM writes skip the table; the watched page still goes slow.
    if(__builtin_expect(paddr-0x10000u<kMem1Limit-0x10000u,1)) {
#ifdef NWII_WATCH_PAGE_MARKING
       if(__builtin_expect((paddr>>16)==((g_watch_addr&0x3fffffffu)>>16),0)){native_write_f32_slow(addr,value);return;}
#else
       if(!NWII_WATCH_CLEAR(paddr,4)){native_write_f32_slow(addr,value);return;}
#endif
       uint32_t bits;std::memcpy(&bits,&value,sizeof(bits));bits=__builtin_bswap32(bits); std::memcpy(mem1.data()+paddr,&bits,sizeof(bits));return;
    }
    if(__builtin_expect(paddr-0x10000000u<kMem2Limit,0)) {
       uint32_t bits;std::memcpy(&bits,&value,sizeof(bits));bits=__builtin_bswap32(bits); std::memcpy(mem2.data()+(paddr-0x10000000u),&bits,sizeof(bits));return;
    }""",
    ),
    # ---- write32 ----
    (
        """    const uint32_t paddr=addr&0x3fffffffu;
    const intptr_t bias=page_bias[paddr>>16]; // NATIVE_MMU_PAGE_TABLE_V1
    if(__builtin_expect(bias>1 && NWII_WATCH_CLEAR(paddr,4),1)) {
       uint32_t bits=value;bits=__builtin_bswap32(bits); std::memcpy(reinterpret_cast<uint8_t*>(bias+paddr),&bits,sizeof(bits));return;
    }""",
        """    const uint32_t paddr=addr&0x3fffffffu;
    // NATIVE_MMU_FLAT_RAM_V1: RAM writes skip the table; the watched page still goes slow.
    if(__builtin_expect(paddr-0x10000u<kMem1Limit-0x10000u,1)) {
#ifdef NWII_WATCH_PAGE_MARKING
       if(__builtin_expect((paddr>>16)==((g_watch_addr&0x3fffffffu)>>16),0)){native_write32_slow(addr,value);return;}
#else
       if(!NWII_WATCH_CLEAR(paddr,4)){native_write32_slow(addr,value);return;}
#endif
       uint32_t bits=__builtin_bswap32(value); std::memcpy(mem1.data()+paddr,&bits,sizeof(bits));return;
    }
    if(__builtin_expect(paddr-0x10000000u<kMem2Limit,0)) {
       uint32_t bits=__builtin_bswap32(value); std::memcpy(mem2.data()+(paddr-0x10000000u),&bits,sizeof(bits));return;
    }""",
    ),
]


def apply(s):
    if MARK in s:
        return s
    out = s
    for old, new in REPLACEMENTS:
        n = out.count(old)
        if n != 1:
            raise SystemExit('flat-ram anchor matched %d times (expected 1): %r' % (n, old[:60]))
        out = out.replace(old, new, 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_MMU_FLAT_RAM', '1') == '0':
        print('mmu flat ram disabled')
        return
    path = args.port / 'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h'
    before = path.read_text()
    after = apply(before)
    if before != after:
        path.write_text(after)
    print('mmu flat ram staged')


if __name__ == '__main__':
    main()
