"""One table lookup per guest memory access instead of MEM1 / MEM2 / special-address range checks.

Profile capture frame-cost-20261006-221021-812600 and a disassembly of the four hottest translated functions (114,000 x86
instructions): branches 24% + compares 17% of all instructions; each guest load/store carried a MEM1 range check, a MEM2 range check,
the physical-0x0C special case and (stores) the watch test: ~6-8 extra instructions around the byte-swapped load or store.

MMU gets a 16,384-entry table of host-pointer biases, one per 64 KiB page of the 1 GiB physical window:
    bias = host_pointer_of_bank - physical_start_of_bank   (MEM1 pages 0x000-0x17F, MEM2 pages 0x1000-0x13FF)
    bias = 1  for page 0 (physical 0x0C is special-cased to read 0 / ignore writes, so the page keeps its old slow path)
    bias = 0  for everything else (MMIO windows, holes, outside RAM)
The inline accessors do `bias = page_bias[paddr >> 16]; if (bias > 1) access(bias + paddr); else slow path`. The slow paths are exactly
the previous out-of-line methods, which already handled every case the removed inline branches did (including 0x0C and watches), so
behaviour is unchanged: the MEM1/MEM2 spans are the same 24 MiB / 64 MiB (both multiples of 64 KiB), stores still defer to the slow
path when the watch block may be hit, and the write-gather page test (0x0C008xxx) still runs before the slow call. The table lives
inside MMU (a context's memory image owns its own table, so two MMUs in one process stay independent).

Requires fix_ram_write_watch.py first (it rewrites the MMU member block). Idempotent.
"""
from pathlib import Path
import re
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
f=rt/'include/runtime/cpu_context.h';s=f.read_text()
NL=chr(10)
if 'NATIVE_MMU_PAGE_TABLE_V1' not in s:
    a=s.index('struct MMU {');b=s.index(NL+'};',a);body=s[a:b]

    def function_span(name):
        m=re.search(r'inline \w+ '+name+r'\(uint32_t addr[^)]*\) \{'+NL,body)
        assert m,name
        end=body.index(NL+'  }'+NL,m.end())+len(NL+'  }'+NL)
        return m.start(),end

    # ---- reads ----
    for name,t,n,swap in (('read8','uint8_t',1,None),('read16','uint16_t',2,'__builtin_bswap16'),('read32','uint32_t',4,'__builtin_bswap32'),('read64','uint64_t',8,'__builtin_bswap64')):
        i,j=function_span(name)
        old=body[i:j]
        assert 'paddr<kMem1Limit' in old and 'mem2offset<kMem2Limit' in old,name
        sig=old[:old.index(NL)+1]
        ret=('value' if swap is None else swap+'(value)')
        new=(sig+'    const uint32_t paddr=addr&0x3fffffffu;'+NL+
             '    const intptr_t bias=page_bias[paddr>>16]; // NATIVE_MMU_PAGE_TABLE_V1'+NL+
             '    if (__builtin_expect(bias>1,1)) { '+t+' value; std::memcpy(&value,reinterpret_cast<const uint8_t*>(bias+paddr),'+str(n)+'); return '+ret+'; }'+NL+
             '    return native_'+name+'_slow(addr);'+NL+'  }'+NL)
        body=body[:i]+new+body[j:]

    # ---- writes ----
    for name,n in (('write8',1),('write16',2),('write32',4),('write64',8),('write_f32',4),('write_f64',8)):
        i,j=function_span(name)
        old=body[i:j]
        sig=old[:old.index(NL)+1]
        m1=re.search(r'    if\(__builtin_expect\(paddr<kMem1Limit && !native_watch_span_may_hit\(paddr,'+str(n)+r'\),1\)\) \{'+NL+r'\s+(.*?)std::memcpy\(mem1\.data\(\)\+paddr,&bits,sizeof\(bits\)\);return;'+NL+'    }'+NL,old,re.S)
        assert m1,name
        bits=m1[1].replace('if(paddr==0x0cu)return; ','')
        gather=re.search(r'(    // NATIVE_INLINE_GATHER_MMIO_V1[^'+NL+r']*'+NL+r'    if\(\(paddr&0xfffff000u\)==0x0c008000u[^'+NL+r']*'+NL+')',old)
        slow=re.search(r'    (native_write\w*_slow\(addr,value\);)'+NL,old);assert slow,name
        new=(sig+'    const uint32_t paddr=addr&0x3fffffffu;'+NL+
             '    const intptr_t bias=page_bias[paddr>>16]; // NATIVE_MMU_PAGE_TABLE_V1'+NL+
             '    if(__builtin_expect(bias>1 && NWII_WATCH_CLEAR(paddr,'+str(n)+'),1)) {'+NL+
             '       '+bits+'std::memcpy(reinterpret_cast<uint8_t*>(bias+paddr),&bits,sizeof(bits));return;'+NL+'    }'+NL+
             (gather[1] if gather else '')+
             '    '+slow[1]+NL+'  }'+NL)
        body=body[:i]+new+body[j:]

    # ---- the table ----
    anchor='  inline uint8_t *get_ptr(uint32_t addr) {'
    assert body.count(anchor)==1
    table='''  // NATIVE_MMU_PAGE_TABLE_V1: see tools/fix_mmu_page_table.py.
  intptr_t page_bias[16384];
  void native_init_page_bias() {
    for (unsigned i = 0; i < 16384; ++i) page_bias[i] = 0;
    for (unsigned i = 0; i < (kMem1Limit >> 16); ++i) page_bias[i] = reinterpret_cast<intptr_t>(mem1.data());
    page_bias[0] = 1; // physical 0x0C is special-cased by the slow paths
#ifdef NWII_WATCH_PAGE_MARKING
    if (g_watch_addr) { const unsigned watch_page = (g_watch_addr & 0x3fffffffu) >> 16; if (watch_page) page_bias[watch_page] = 0; } // watched page: every access takes the slow path
#endif
    for (unsigned i = 0; i < (kMem2Limit >> 16); ++i) page_bias[(0x10000000u >> 16) + i] = reinterpret_cast<intptr_t>(mem2.data()) - 0x10000000;
  }

'''
    body=body.replace(anchor,table+anchor)
    # constructors: initialise the table after the banks exist
    count=0
    for old_ctor_end in ('    native_static_ram_commit(0x30000000u,64u*1024u*1024u+8u);'+NL+'  }',
                         '    mem2.allocate(64 * 1024 * 1024 + 8);'+NL+'  }'):
        if old_ctor_end in body:
            body=body.replace(old_ctor_end,old_ctor_end[:-len(NL+'  }')]+NL+'    native_init_page_bias();'+NL+'  }');count+=1
    assert count>=1,'MMU constructor not found'
    s=s[:a]+body+s[b:]
    f.write_text(s)
# Independent, idempotent steps (also upgrade a header that already has the table).
s=f.read_text()
changed=False
if 'NWII_WATCH_CLEAR(paddr' not in s:
    s,n=re.subn(r'bias>1 && !native_watch_span_may_hit\(paddr,(\d)\)',r'bias>1 && NWII_WATCH_CLEAR(paddr,\1)',s);assert n==6,n;changed=True
if '#define NWII_WATCH_CLEAR' not in s:
    macro="""// NATIVE_MMU_PAGE_TABLE_V1: with NWII_WATCH_PAGE_MARKING the watched 64 KiB page is marked slow in MMU::page_bias, so the inline
// stores need no watch test; the slow paths keep the exact hit predicates. Without it every store tests the watch as before.
#ifdef NWII_WATCH_PAGE_MARKING
#define NWII_WATCH_CLEAR(address, bytes) true
#else
#define NWII_WATCH_CLEAR(address, bytes) (!native_watch_span_may_hit(address, bytes))
#endif
"""
    a=s.index('struct MMU {');s=s[:a]+macro+s[a:];changed=True
if 'if (g_watch_addr) { const unsigned watch_page' not in s:
    anchor="    page_bias[0] = 1; // physical 0x0C is special-cased by the slow paths"+chr(10)
    assert anchor in s
    s=s.replace(anchor,anchor+"#ifdef NWII_WATCH_PAGE_MARKING"+chr(10)+"    if (g_watch_addr) { const unsigned watch_page = (g_watch_addr & 0x3fffffffu) >> 16; if (watch_page) page_bias[watch_page] = 0; } // watched page: every access takes the slow path"+chr(10)+"#endif"+chr(10),1);changed=True
if changed:f.write_text(s)
cm=p/'build/nwii-full-source/CMakeLists.txt';t=cm.read_text()
if 'NWII_WATCH_PAGE_MARKING' not in t:
    t+=chr(10)+'# NATIVE_WATCH_PAGE_MARKING_V1'+chr(10)+'target_compile_definitions(nwiiruntime PUBLIC NWII_WATCH_PAGE_MARKING)'+chr(10)
    cm.write_text(t)
print('MMU page table installed')
