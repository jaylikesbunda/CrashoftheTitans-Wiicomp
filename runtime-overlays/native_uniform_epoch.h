#pragma once
#include <array>
#include <cstdint>
#include <cstdlib>
// Render-context owner only. Arrays persist independently of GL programs;
// per-program NativeUniformChanged still decides every upload.
namespace native_uniform_epoch {
enum Group:unsigned{Material,Channel,Lights,Tev,Count};
inline const bool enabled=!std::getenv("NWII_NO_UNIFORM_CACHE_EPOCH");
inline std::array<uint64_t,Count> epoch{1,1,1,1},built{},builds{},skips{};
inline void Invalidate(){for(auto& value:epoch)++value;}
inline void DirtyXf(uint32_t start,size_t count){
 const uint64_t end=uint64_t(start)+count;
 if(start<0x100e && end>0x100a)++epoch[Material];
 if(start<0x1012 && end>0x100e)++epoch[Channel];
 if(start<0x680 && end>0x600)++epoch[Lights];
}
inline void DirtyTev(unsigned reg){if(reg>=0xe0 && reg<=0xe7)++epoch[Tev];}
inline bool Build(Group group){
 if(enabled && built[group]==epoch[group]){++skips[group];return false;}
 built[group]=epoch[group];++builds[group];return true;
}
}
