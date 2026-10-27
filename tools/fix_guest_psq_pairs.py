"""Pair integer PSQ RAM reads and F32 RAM/FIFO writes; scalar fallback retained."""
from pathlib import Path
import re,json,hashlib
p=Path(__file__).resolve().parents[1];h=p/'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h';s=h.read_text();marker='NATIVE_GUEST_PSQ_PAIRS_V1'
def method(name):
 a=s.index('inline '+('NativePsqPair' if name=='psq_load_pair' else 'void')+' '+name+'(');begin=s.index('{',a);depth=1;end=begin+1
 while depth:depth+=(s[end]=='{')-(s[end]=='}');end+=1
 return s[a:end]
if marker not in s:
 # A production-derived, standalone old-method oracle for the differential.
 functions=[]
 for name in ('psq_load_pair','psq_store_val'):
  body=method(name);body=body.replace('inline NativePsqPair psq_load_pair(', '__attribute__((always_inline)) inline CPUContext::NativePsqPair LegacyPsqLoad(CPUContext& c, ').replace('inline void psq_store_val(', '__attribute__((always_inline)) inline void LegacyPsqStore(CPUContext& c, ')
  body=re.sub(r'\b(?:gqr|mmu|native_psq_load_slow_pair|native_psq_store_slow_val)\b',lambda m:'c.'+m[0],body).replace('NativePsqPair result','CPUContext::NativePsqPair result');functions.append(body)
 fixture=p/'reference/guest-regions/psq-pair-baseline.inc';
 if not fixture.exists():fixture.write_text('\n'.join(functions)+'\n')
 s=s.replace('void GX_WGPIPE_WriteF64(double val);','void GX_WGPIPE_WriteF64(double val);\nextern bool g_native_fifo_ok; // '+marker,1)
 load=method('psq_load_pair');anchor='      auto load_element = [&](uint32_t a) -> double {';assert load.count(anchor)==1
 pair=r'''      // NATIVE_GUEST_PSQ_PAIRS_V1: ordinary contiguous integer pair, one guard.
      if(!W){const uint32_t physical=addr&0x3fffffffu;const unsigned bytes=(ld_type==4 || ld_type==6)?2:4;
        const intptr_t bias=mmu.page_bias[physical>>16];
        if(bias>1 && (physical&65535u)<=65536u-bytes){const uint8_t* ptr=reinterpret_cast<const uint8_t*>(bias+physical);
          if(ld_type==4)return {ptr[0]*scale,ptr[1]*scale};
          if(ld_type==6)return {int8_t(ptr[0])*scale,int8_t(ptr[1])*scale};
          uint32_t bits;std::memcpy(&bits,ptr,4);bits=__builtin_bswap32(bits);
          if(ld_type==5)return {uint16_t(bits>>16)*scale,uint16_t(bits)*scale};
          return {int16_t(bits>>16)*scale,int16_t(bits)*scale};
        }
      }
'''
 s=s.replace(load,load.replace(anchor,pair+anchor,1),1)
 store=method('psq_store_val');anchor='    if (__builtin_expect(st_type == 0, 1)) {';assert store.count(anchor)==1
 pair=r'''
      // NATIVE_GUEST_PSQ_PAIRS_V1: no observer can run between these RAM writes.
      if(!W){const uint32_t physical=addr&0x3fffffffu;const intptr_t bias=mmu.page_bias[physical>>16];
        if(bias>1 && (physical&65535u)<=65528u && !native_watch_span_may_hit(physical,8)){
          const float a=float(fval),b=float(pval);uint32_t av,bv;std::memcpy(&av,&a,4);std::memcpy(&bv,&b,4);
          uint64_t bits=__builtin_bswap64((uint64_t(av)<<32)|bv);std::memcpy(reinterpret_cast<uint8_t*>(bias+physical),&bits,8);return;
        }
        // The ordinary-FIFO flag excludes redirect/probe paths. Existing bulk
        // F64 emission transports these raw eight bytes without FP arithmetic.
        if(g_native_fifo_ok && (physical&0xfffff000u)==0x0c008000u && (physical&4095u)<=4088u && !native_watch_span_may_hit(physical,8)){
          const float a=float(fval),b=float(pval);uint32_t av,bv;std::memcpy(&av,&a,4);std::memcpy(&bv,&b,4);const uint64_t bits=(uint64_t(av)<<32)|bv;double packet;std::memcpy(&packet,&bits,8);NWII_CTX_BARRIER();GX_WGPIPE_WriteF64(packet);return;
        }
      }
'''
 s=s.replace(store,store.replace(anchor,anchor+pair,1),1)
 old='if(__builtin_expect((gqr[I]&7u)==0,1)){mmu.write_f32(addr,float(fpr[frS]));if(!W)mmu.write_f32(addr+4,float(ps1[frS])); return;}'
 new='if(__builtin_expect((gqr[I]&7u)==0,1)){if(!W){psq_store_val(fpr[frS],ps1[frS],addr,W,I);return;}mmu.write_f32(addr,float(fpr[frS]));return;}'
 assert s.count(old)==1;s=s.replace(old,new,1);h.write_text(s)
print('Guest PSQ: one integer-pair RAM guard, paired F32 RAM/FIFO output; boundary/watch/redirect fallback retained.')
