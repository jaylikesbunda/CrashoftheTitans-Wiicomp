#pragma once
#include <array>
#include <cstdint>
#include <cstdlib>
#include <cstring>

namespace native_xf_stream {
// A stream-local proof only: the first write always survives. Display-list
// calls and indexed loads invalidate the proof because their live data must
// still be replayed. No projection or unknown-register elision.
struct Shadow {
 std::array<uint8_t,0x1100> known{};
 std::array<uint32_t,0x1100> owners{};
 uint8_t epoch=1;
 unsigned misses=0;bool tracking=true,seen_hit=false;
 void Reset(){if(++epoch==0){known.fill(0);epoch=1;}}
 void BeginStream(){Reset();misses=0;tracking=true;seen_hit=false;}
 bool Allowed()const{return tracking;}
 void Hit(){seen_hit=true;}
 // Bound speculation on a changing stream. A demonstrated repeat keeps the
 // cache active; otherwise only the first 32 packets pay ownership tracking.
 void Miss(){if(!seen_hit && ++misses>=32)tracking=false;}
 static uint32_t Word(const uint8_t* p){return uint32_t(p[0])<<24|uint32_t(p[1])<<16|uint32_t(p[2])<<8|p[3];}
 static bool Range(unsigned address,unsigned count){
  const unsigned end=address<0x100?0x100:address>=0x400 && address<0x460?0x460:
   address>=0x500 && address<0x680?0x680:address>=0x1000 && address<0x1020?0x1020:
   address>=0x1040 && address<0x1048?0x1048:0;
  return count && end>address && count<=end-address;
 }
 uint32_t Reference(unsigned address,unsigned count){
  if(!Range(address,count)){Reset();return UINT32_MAX;}
  return known[address]==epoch?owners[address]:UINT32_MAX;
 }
 bool Unchanged(unsigned address,const uint8_t* bytes,unsigned count,const float* saved,uint32_t owner)const{
  // Reject a changed first word immediately, without copying a shadow or
  // scanning the rest of a changing matrix.
  for(unsigned i=0;i<count;++i){
   uint32_t old;std::memcpy(&old,saved+i,4);
   if(Word(bytes+4*i)!=old || known[address+i]!=epoch || owners[address+i]!=owner)return false;
  }
  return true;
 }
 void Remember(unsigned address,unsigned count,uint32_t owner){
  if(!Range(address,count))return;
  for(unsigned i=0;i<count;++i){owners[address+i]=owner;known[address+i]=epoch;}
 }
};
inline bool Enabled(){static const bool value=!std::getenv("NWII_NO_XF_STREAM_REUSE") &&
 !std::getenv("NWII_NO_DRAW_SUBMISSION_REUSE") &&
 !std::getenv("NWII_XFDBG") && !std::getenv("NWII_SKINDBG");return value;}
}
