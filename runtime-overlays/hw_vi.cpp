// VI register storage with explicit big-endian word/halfword views.
// Beam timing and field scheduling remain subject to original-state validation.
#include "runtime/hw/hw.h"
#include "runtime/config.h"
#include <array>
#include <cstdint>

namespace nwii::runtime::hw {
static std::array<uint16_t,128> vi_regs{};
uint32_t g_vi_top_field_base=0,g_vi_btm_field_base=0;
static uint32_t vi_word(unsigned offset) {
  return (uint32_t(vi_regs[offset/2])<<16)|vi_regs[offset/2+1];
}
static void vi_irq() {
  bool active=false;
  for(unsigned i=0;i<4;++i) active|=(vi_word(0x30+i*4)&0x90000000u)==0x90000000u;
  if(active)trigger_pi_interrupt(0x100);else clear_pi_interrupt(0x100);
}
static uint32_t vi_fb(unsigned offset) {
  uint32_t value=vi_word(offset),addr=value&0xffffff;
  return (value&0x10000000)?addr<<5:addr;
}
void vi_trigger_interrupt() {
  // Field cadence is currently supplied by the existing runtime main loop.
  for(unsigned i=0;i<2;++i)
    if(vi_regs[(0x30+i*4)/2]&0x1000)vi_regs[(0x30+i*4)/2]|=0x8000;
  vi_irq();
}
void register_vi(MMIODispatcher &dispatcher) {
  // The original Wii boot environment supplies an already enabled VI.
  // An all-zero DCR makes VIInit install NTSC and overwrite the PAL boot mode.
  const auto &id=nwii::runtime::Config::get().game_id;
  char region=id.size()>=4?id[3]:'E';
  bool pal=region=='P'||region=='D'||region=='F'||region=='I'||region=='S'||
           region=='H'||region=='U'||region=='X'||region=='Y'||region=='Z';
  vi_regs[1]=pal?0x0101:0x0001;
  // SDK timing tables in the original DOL: NTSC 805758D8, PAL 80575924.
  // __VIInit uses field +0x18 / 2 + 1 and +0x1A + 1 for pre-retrace,
  // and comparator (1,1) for post-retrace. Preserve that boot environment.
  vi_regs[0x30/2]=0x1000|(pal?313:263);
  vi_regs[0x32/2]=pal?433:430;
  vi_regs[0x34/2]=0x1001;
  vi_regs[0x36/2]=1;
  auto read16=[](uint32_t addr)->uint16_t {
    unsigned offset=addr&0xff;
    if(offset==0x2c)return uint16_t((nwii::runtime::get_os_time()/64)%313+1);
    if(offset==0x2e)return 1;
    return vi_regs[offset/2];
  };
  auto write16=[](uint32_t addr,uint16_t value) {
    unsigned offset=addr&0xff;vi_regs[offset/2]=value;
    if(offset==0x1c||offset==0x1e)g_vi_top_field_base=vi_fb(0x1c);
    if(offset==0x24||offset==0x26)g_vi_btm_field_base=vi_fb(0x24);
    if(offset>=0x30&&offset<=0x3e)vi_irq();
  };
  auto read32=[read16](uint32_t addr)->uint32_t {
    return (uint32_t(read16(addr))<<16)|read16(addr+2);
  };
  auto write32=[write16](uint32_t addr,uint32_t value) {
    write16(addr,uint16_t(value>>16));write16(addr+2,uint16_t(value));
  };
  dispatcher.register_region(0xcc002000,0xcc0020ff,read32,write32,read16,write16);
}
}
