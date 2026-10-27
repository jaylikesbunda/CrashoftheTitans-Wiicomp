// Native AI register model. Bit layout cross-checked against Dolphin AICR;
// guest-time differential validation is still required for full audio fidelity.
#include "runtime/hw/hw.h"
#include <cstdint>
#include <cstdlib>
#include <iostream>

namespace nwii::runtime::hw {
static uint32_t ai_control=0,ai_volume=0,ai_count=0,ai_target=0;
static uint64_t ai_last=0,ai_fraction=0;

static void ai_irq() {
  if ((ai_control & 0x0c)==0x0c) trigger_pi_interrupt(0x20);
  else clear_pi_interrupt(0x20);
}
static void ai_sync() {
  uint64_t now=nwii::runtime::get_os_time();
  if ((ai_control & 1) && now>=ai_last) {
    uint64_t rate=(ai_control & 2)?48000:32000;
    uint64_t scaled=(now-ai_last)*rate+ai_fraction;
    uint32_t amount=static_cast<uint32_t>(scaled/1000000);
    ai_fraction=scaled%1000000;
    uint32_t old=ai_count;
    ai_count+=amount;
    if (amount && static_cast<uint32_t>(ai_target-old-1)<amount) ai_control|=8;
  }
  ai_last=now;
  ai_irq();
}
void register_ai(MMIODispatcher &dispatcher) {
  auto read=[](uint32_t addr)->uint32_t {
    ai_sync();
    switch(addr & 0xffff) {
      case 0x6c00:return ai_control;
      case 0x6c04:return ai_volume;
      case 0x6c08:return ai_count;
      case 0x6c0c:return ai_target;
      default:return 0;
    }
  };
  auto write=[](uint32_t addr,uint32_t value) {
    ai_sync();
    switch(addr & 0xffff) {
      case 0x6c00:
        if(std::getenv("NWII_AI_PROBE")) {
          static unsigned writes=0;
          if(writes++<100) std::cout<<"[AI_CTRL] old="<<std::hex<<ai_control<<" write="<<value<<" count="<<ai_count<<" target="<<ai_target<<std::dec<<std::endl;
        }
        if(value & 8) ai_control &= ~8u;
        if(value & 0x20) {ai_count=0;ai_fraction=0;}
        ai_control=(ai_control & 8)|(value & 0x57);
        break;
      case 0x6c04:ai_volume=value;break;
      case 0x6c08:ai_count=value;ai_fraction=0;break;
      case 0x6c0c:ai_target=value;break;
    }
    ai_irq();
  };
  for(uint32_t base:{0xcc000000u,0xcd000000u,0x0c000000u,0x0d000000u})
    dispatcher.register_region(base+0x6c00,base+0x6cff,read,write);
}
void ai_update() {ai_sync();}
}
