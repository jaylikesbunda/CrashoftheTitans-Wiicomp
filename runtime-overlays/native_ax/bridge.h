// Native DSP hardware bridge. Dolphin adaptations retain GPL-2.0-or-later.
#pragma once
#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <cstring>
#include <functional>
#include <iostream>
#include <limits>
#include <memory>
#include <optional>
#include <stdexcept>
#include <unordered_set>
#include <vector>
using u8=uint8_t; using u16=uint16_t; using u32=uint32_t;
using s16=int16_t; using s32=int32_t; using s64=int64_t;
namespace Common {
inline u16 swap16(u16 v){return (v>>8)|(v<<8);}
inline u32 swap32(u32 v){return (v>>24)|((v>>8)&0xff00)|((v<<8)&0xff0000)|(v<<24);}
}
namespace MathUtil {
template<class T,class V>T SaturatingCast(V v){return T(std::clamp<V>(v,std::numeric_limits<T>::min(),std::numeric_limits<T>::max()));}
}
namespace Memory {
class MemoryManager {
public:
  std::function<u8*(u32,size_t)> pointer;
  explicit MemoryManager(decltype(pointer) p):pointer(std::move(p)){}
  u8* GetPointerForRange(u32 a,size_t n){auto p=pointer(a,n);if(!p)throw std::runtime_error("AX invalid RAM range");return p;}
  template<class T>void CopyFromEmuSwapped(T* dst,u32 a,size_t n){
    if(n%sizeof(T))throw std::runtime_error("AX unaligned transfer size");
    auto p=GetPointerForRange(a,n);auto d=reinterpret_cast<u8*>(dst);
    for(size_t i=0;i<n;i+=sizeof(T))for(size_t j=0;j<sizeof(T);++j)d[i+j]=p[i+sizeof(T)-1-j];
  }
  template<class T>void CopyToEmuSwapped(u32 a,const T* src,size_t n){
    if(n%sizeof(T))throw std::runtime_error("AX unaligned transfer size");
    auto p=GetPointerForRange(a,n);auto s=reinterpret_cast<const u8*>(src);
    for(size_t i=0;i<n;i+=sizeof(T))for(size_t j=0;j<sizeof(T);++j)p[i+j]=s[i+sizeof(T)-1-j];
  }
  void CopyToEmu(u32 a,const void* src,size_t n){std::memcpy(GetPointerForRange(a,n),src,n);}
};
}
namespace DSP {
class DSPManager {
  Memory::MemoryManager& memory;
public:
  explicit DSPManager(Memory::MemoryManager& m):memory(m){}
  // Wii accelerator addresses select MEM2 with bit28, otherwise MEM1.
  u8 ReadARAM(u32 a){return *memory.GetPointerForRange(a&((a&0x10000000)?0x13ffffff:0x01ffffff),1);}
  void WriteARAM(u8 v,u32 a){*memory.GetPointerForRange(a&((a&0x10000000)?0x13ffffff:0x01ffffff),1)=v;}
};
namespace HLE {
inline void NativeQuirk(const char* name){static std::unordered_set<std::string> seen;if(seen.insert(name).second)std::cerr<<"[AX] Dolphin fidelity limitation: "<<name<<"\n";}
struct MailSink {
  std::vector<u32> pending;
  void PushMail(u32 mail,bool){pending.push_back(mail);}
};
struct System {
  Memory::MemoryManager memory;
  DSPManager dsp;
  explicit System(decltype(memory.pointer) p):memory(std::move(p)),dsp(memory){}
  Memory::MemoryManager& GetMemory(){return memory;}
  DSPManager& GetDSP(){return dsp;}
};
struct DSPHLE {
  System system;
  MailSink mail;
  explicit DSPHLE(decltype(system.memory.pointer) p):system(std::move(p)){}
  System& GetSystem(){return system;}
};
constexpr u32 DSP_SYNC=0xdcd10004;
}
}
#include "structs.h"
#include "accelerator.h"
#include "accelerator.inc"
#include "base.h"
#include "wii.h"
#define AX_WII
#include "voice.h"
#include "wii.inc"
