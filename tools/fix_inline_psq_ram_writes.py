"""Inline ordinary RAM stores and the common floating paired-single memory form."""
from pathlib import Path
import re
p=Path(__file__).resolve().parents[1];h=p/'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h';s=h.read_text()
saved=p/'runtime-overlays/psq-writes-before.h'
if not saved.exists():saved.write_text(s)
if 'NATIVE_INLINE_RAM_WRITE_V1' not in s:
    assert 'void custom_watch(uint32_t, uint32_t, int) {}' in s,'Requalify custom watch visibility before direct ordinary RAM stores'
    for name,typ,bytes_ in [('write8','uint8_t',1),('write16','uint16_t',2),('write32','uint32_t',4),('write64','uint64_t',8),('write_f32','float',4),('write_f64','double',8)]:
        signature='  void '+name+'(uint32_t addr, '+typ+' value) {'
        assert s.count(signature)==1,(name,s.count(signature))
        bits='uint'+str(bytes_*8)+'_t'
        value='uint'+str(bytes_*8)+'_t bits;std::memcpy(&bits,&value,sizeof(bits));' if typ in ('float','double') else bits+' bits=value;'
        if bytes_>1:value+='bits=__builtin_bswap'+str(bytes_*8)+'(bits);'
        skip='if(paddr==0x0cu)return;' if name=='write32' else ''
        replacement='''  // NATIVE_INLINE_RAM_WRITE_V1: watches and external addresses retain original methods.
  __attribute__((always_inline)) inline void NAME(uint32_t addr, TYPE value) {
    const uint32_t paddr=addr&0x3fffffffu;
    if(__builtin_expect(paddr<mem1.size()-8 && g_watch_addr==0,1)) {
      SKIP VALUE std::memcpy(mem1.data()+paddr,&bits,sizeof(bits));return;
    }
    native_NAME_slow(addr,value);
  }
  __attribute__((noinline)) void native_NAME_slow(uint32_t addr, TYPE value) {'''.replace('NAME',name).replace('TYPE',typ).replace('SKIP',skip).replace('VALUE',value)
        s=s.replace(signature,replacement,1)
if 'NATIVE_INLINE_PSQ_F32_V1' not in s:
    for name,arg in [('psq_load','frD'),('psq_store','frS')]:
        signature='  void '+name+'(uint32_t '+arg+', uint32_t addr, uint32_t W, uint32_t I) {'
        assert s.count(signature)==1
        typebits='((gqr[I]>>16)&7u)==0' if name=='psq_load' else '(gqr[I]&7u)==0'
        operation='fpr[frD]=mmu.read_f32(addr);ps1[frD]=W?1.:double(mmu.read_f32(addr+4));' if name=='psq_load' else 'mmu.write_f32(addr,float(fpr[frS]));if(!W)mmu.write_f32(addr+4,float(ps1[frS]));'
        replacement='''  // NATIVE_INLINE_PSQ_F32_V1: scale bits have no effect on the F32 form.
  __attribute__((always_inline)) inline void NAME(uint32_t REG, uint32_t addr, uint32_t W, uint32_t I) {
    if(__builtin_expect(CONDITION,1)){OPERATION return;}
    native_NAME_slow(REG,addr,W,I);
  }
  __attribute__((noinline)) void native_NAME_slow(uint32_t REG, uint32_t addr, uint32_t W, uint32_t I) {'''.replace('NAME',name).replace('REG',arg).replace('CONDITION',typebits).replace('OPERATION',operation)
        s=s.replace(signature,replacement,1)
if h.read_text()!=s:h.write_text(s)
print('Inline ordinary RAM writes and paired-single F32 memory forms staged; all slow forms retained.')
