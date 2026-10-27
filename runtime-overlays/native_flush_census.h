#pragma once
#include <array>
#include <cstdint>

namespace native_flush_census {
enum Reason : unsigned {Opening,Topology,Mode,Matrix,Bp,Xf,Other,Drain,Count};
struct Stats {
 std::array<uint64_t,Count> requests{},empty{},submitted{},vertices{};
};
inline Stats counts{},baseline{}; // Renderer owner only; readers also use that owner.
inline void Request(bool active,Reason reason,bool empty){
 if(!active)return;
 ++counts.requests[reason];if(empty)++counts.empty[reason];
}
inline void Submit(bool active,Reason reason,uint64_t vertices){
 if(!active)return;
 ++counts.submitted[reason];counts.vertices[reason]+=vertices;
}
inline Stats GetStats(){
 Stats result;
 for(unsigned i=0;i<Count;++i){
  result.requests[i]=counts.requests[i]-baseline.requests[i];
  result.empty[i]=counts.empty[i]-baseline.empty[i];
  result.submitted[i]=counts.submitted[i]-baseline.submitted[i];
  result.vertices[i]=counts.vertices[i]-baseline.vertices[i];
 }
 return result;
}
inline void ResetBaseline(){baseline=counts;}
}
