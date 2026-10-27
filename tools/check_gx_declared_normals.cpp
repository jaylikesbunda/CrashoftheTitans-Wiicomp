#include "runtime/gx/fifo_parser.h"
#include "runtime/cpu_context.h"
#include <cmath>
#include <cstring>
#include <iostream>
using namespace nwii::runtime::gx;
namespace nwii::runtime { MMU *g_mmu=nullptr;uint32_t g_watch_addr=0;
 uint32_t HW_Reg_Read32(uint32_t){std::abort();}
 void watch_hit(uint32_t,uint32_t,int){std::abort();}
 bool GX_DL_Fetch(uint32_t,uint32_t,std::vector<uint8_t>&){std::abort();}
}
namespace nwii::runtime::hw {void pe_signal_finish(){std::abort();}void pe_signal_token(uint32_t,bool){std::abort();}}
namespace nwii::runtime::gx { GXState g_state; }
int main(){
 int tests=0,failures=0;auto check=[&](bool ok){++tests;if(!ok)++failures;};
 for(bool lit:{false,true})for(bool snapshot:{false,true})for(bool nbt:{false,true}) {
  // Renderer state is deliberately unrelated to the pending draw's lighting.
  g_state.xf[14]=lit?2:0;g_state.xf[15]=0;
  GXCommand command{};command.type=GXCommandType::DrawPrimitive;
  command.raw=std::make_shared<DrawRaw>();auto&r=*command.raw;
  r.need_normal=false;r.count=3;r.vat.posMask=VtxAttrMask::Direct;r.vat.posType=VtxAttrType::F32;r.vat.posElements=1;
  r.vat.nrmMask=VtxAttrMask::Direct;r.vat.nrmType=VtxAttrType::F32;r.vat.nrmElements=nbt;
  auto append=[&](float v){uint32_t bits;std::memcpy(&bits,&v,4);for(int shift=24;shift>=0;shift-=8)r.bytes.push_back(uint8_t(bits>>shift));};
  for(int i=0;i<3;++i){append(float(i));append(float(i+1));append(-3);for(int j=0;j<(nbt?9:3);++j)append(j%3==i?1.f:0.f);}
  if(snapshot){r.have_snap_mtx=true;float pos[]={1,0,0,0,0,1,0,0,0,0,1,0};float normal[]={0,-1,0,1,0,0,0,0,1};std::memcpy(r.snap_pos_mtx,pos,48);std::memcpy(r.snap_nrm_mtx,normal,36);}
  FifoParser::DecodeDraw(command);check(command.vertices.size()==3);
  for(int i=0;i<3&&i<int(command.vertices.size());++i){auto&v=command.vertices[i];check(v.has_norm);float expected[3]={};expected[i]=1;if(snapshot){float x=expected[0];expected[0]=-expected[1];expected[1]=x;}
    for(int j=0;j<3;++j)check(std::fabs(v.norm[j]-expected[j])<1e-6);
    check(v.pos[0]==i&&v.pos[1]==i+1&&v.pos[2]==-3);
  }
  auto size=command.vertices.size();FifoParser::DecodeDraw(command);check(command.vertices.size()==size);
 }
 std::cout<<"RESULT "<<tests-failures<<"/"<<tests<<" passed\n";return failures?1:0;
}
