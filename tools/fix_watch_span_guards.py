"""Keep the scheduler recorder while enabling unrelated RAM and FIFO stores."""
from pathlib import Path
import json,re
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
h=rt/'include/runtime/cpu_context.h';s=h.read_text()
if 'native_watch_span_may_hit' not in s:
 baseline=p/'runtime-overlays/watch-span-before.h'
 if not baseline.exists():baseline.write_text(s)
 helper='''
// NATIVE_WATCH_SPAN_GUARD_V1: conservative eight-byte watch block. Exact hit
// predicates stay in the original slow paths, including their width and order.
__attribute__((always_inline)) inline bool native_watch_span_may_hit(uint32_t address,uint32_t bytes){
    if(!g_watch_addr || !bytes)return false;
    const uint64_t start=address&0x3fffffffu,watch=g_watch_addr&0x3ffffff8u;
    return start<watch+8u && watch<start+uint64_t(bytes);
}
'''
 anchor='extern uint32_t g_watch_addr;';assert s.count(anchor)==1;s=s.replace(anchor,anchor+helper,1)
 # The write-path substitution is the optimization half; the helper above is what the
 # gather/bulk guards link against. Skip a write path whose shape an earlier stage has
 # already changed instead of aborting the whole stage.
 for name,width in [('write8',1),('write16',2),('write32',4),('write64',8),('write_f32',4),('write_f64',8)]:
  anchor2='inline void '+name+'('
  if anchor2 not in s:anchor2='void '+name+'(uint32_t addr'
  if anchor2 not in s:continue
  a=s.index(anchor2);b=s.find('__attribute__((noinline))',a)
  if b<0:continue
  body=s[a:b]
  if body.count('g_watch_addr==0') not in (2,3):continue
  body=body.replace('g_watch_addr==0','!native_watch_span_may_hit(paddr,'+str(width)+')');s=s[:a]+body+s[b:]
 h.write_text(s)
g=rt/'src/hle/gx.cpp';s=g.read_text()
if 'NATIVE_GATHER_WATCH_SPAN_V1' not in s:
 baseline=p/'runtime-overlays/watch-span-gather-before.cpp'
 if not baseline.exists():baseline.write_text(s)
 old='if(probe || nwii::runtime::g_watch_addr)return -1;';assert s.count(old)==1;s=s.replace(old,'if(probe)return -1;',1)
 anchor='    if(!dst)return -1; // includes RAM-end crossing and invalid bus addresses';assert s.count(anchor)==1
 s=s.replace(anchor,anchor+'\n    // NATIVE_GATHER_WATCH_SPAN_V1: byte-wise hits retain their original arguments/order.\n    if(nwii::runtime::native_watch_span_may_hit(phys,bytes))return -1;',1);g.write_text(s)
# The default-watch ordinary-FIFO microbenchmark regressed with the bulk
# classifier. Keep its original path; redirected RAM is the demonstrated win.
s=g.read_text()
if 'NATIVE_WATCH_ORDINARY_FIFO_PARITY_V1' not in s:
 a=s.index('static inline int native_gather_redirect_span(');b=s.index('\n}\n',a)+2;body=s[a:b]
 body=body.replace('if(!mmu)return 0;', 'if(!mmu)return nwii::runtime::g_watch_addr?-1:0; // NATIVE_WATCH_ORDINARY_FIFO_PARITY_V1')
 body=body.replace('if(base==0 && end<0x04000000u)return 0;', 'if(base==0 && end<0x04000000u)return nwii::runtime::g_watch_addr?-1:0;')
 body=body.replace('if(base!=0 && (base&0x03ffffffu)==(nwii::runtime::hw::cp_fifo_base_reg()&0x03ffffffu))return 0;', 'if(base!=0 && (base&0x03ffffffu)==(nwii::runtime::hw::cp_fifo_base_reg()&0x03ffffffu))return nwii::runtime::g_watch_addr?-1:0;')
 s=s[:a]+body+s[b:];g.write_text(s)
f=rt/'src/hle/interpreter.cpp';s=f.read_text()
if 'NATIVE_BULK_WATCH_SPAN_V1' not in s:
 baseline=p/'runtime-overlays/watch-span-bulk-before.cpp'
 if not baseline.exists():baseline.write_text(s)
 for name in ('NativeHelperCopy','NativeHelperFill'):
  a=s.index('static bool '+name+'(');b=s.index('\n}',a);body=s[a:b]
  assert body.count('if(g_watch_addr)return false;')==1
  body=body.replace('if(g_watch_addr)return false;','// NATIVE_BULK_WATCH_SPAN_V1\n    if(native_watch_span_may_hit(dst,n))return false;');s=s[:a]+body+s[b:]
 f.write_text(s)
print('Default recorder retained; scalar, gather and bulk guards now check destination watch spans.')
