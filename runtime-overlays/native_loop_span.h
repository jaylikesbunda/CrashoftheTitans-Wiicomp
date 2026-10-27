#pragma once
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include "runtime/cpu_context.h"
namespace native_guest_loop {
inline bool Enabled(){static const bool enabled=!std::getenv("NWII_NO_LOOP_FASTPATH");return enabled;}
template<class Memory> __attribute__((always_inline)) inline const uint8_t* Preflight(Memory& memory,uint32_t address,uint32_t count,unsigned step,unsigned first,unsigned last){
 if(!Enabled())return nullptr;
 const uint64_t begin=uint64_t(address)+first;
 const uint64_t bytes=count?uint64_t(count-1)*step+last-first+4:0;
 if(!bytes || begin>UINT32_MAX || bytes>UINT32_MAX-begin+1)return nullptr;
 const uint32_t physical=uint32_t(begin)&0x3fffffffu;
 if(bytes>65536u-(physical&65535u))return nullptr;
 const intptr_t bias=memory.page_bias[physical>>16];
 return bias>1?reinterpret_cast<const uint8_t*>(bias+physical):nullptr;
}
// Revalidate after every callback poll. No cached RAM bytes and no reordered
// loads: only the address proof is reused across a statically verified loop.
struct Span {
 const uint8_t* pointer=nullptr;uint32_t origin=0;bool attempted=false;
 void invalidate(){pointer=nullptr;attempted=false;}
 template<class Memory> const uint8_t* get(Memory& memory,uint32_t address,uint32_t count,unsigned step,unsigned first,unsigned last){
  if(!attempted){attempted=true;origin=address;
   const uint64_t begin=uint64_t(address)+first;
   const uint64_t bytes=count?uint64_t(count-1)*step+last-first+4:0;
   if(!bytes || begin>UINT32_MAX || bytes>UINT32_MAX-begin+1)return nullptr;
   const uint32_t physical=uint32_t(begin)&0x3fffffffu;
   if(bytes>65536u-(physical&65535u))return nullptr;
   const intptr_t bias=memory.page_bias[physical>>16];
   if(bias<=1)return nullptr;
   pointer=reinterpret_cast<const uint8_t*>(bias+physical);
  }
  return pointer?pointer+uint32_t(address-origin):nullptr;
 }
};
__attribute__((always_inline)) inline float F32(const uint8_t* ptr){
 uint32_t bits;std::memcpy(&bits,ptr,4);bits=__builtin_bswap32(bits);float value;std::memcpy(&value,&bits,4);return value;
}
// GCC may fold float->double->float across the removed guards differently for
// signaling NaNs. Keep all exceptional encodings on the exact original lane.
__attribute__((always_inline)) inline bool Exceptional(const uint8_t* ptr){
 uint32_t bits;std::memcpy(&bits,ptr,4);return (__builtin_bswap32(bits)&0x7f800000u)==0x7f800000u;
}
__attribute__((always_inline)) inline void WritePair(float first,float second){
 uint32_t a,b;std::memcpy(&a,&first,4);std::memcpy(&b,&second,4);
 const uint64_t bits=(uint64_t(a)<<32)|b;double packet;std::memcpy(&packet,&bits,8);
 NWII_CTX_BARRIER();nwii::runtime::GX_WGPIPE_WriteF64(packet);
}
}
