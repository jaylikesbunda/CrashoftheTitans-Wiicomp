// Actual runtime callback polling cost, with no guest runnable work or IRQs.
#include "runtime/cpu_context.h"
#include <ctime>
#include <chrono>
#include <iostream>
namespace nwii::runtime { CPUContext *g_ctx_ptr=nullptr; MMU *g_mmu=nullptr; }
extern "C" void run_game(nwii::runtime::CPUContext &) { std::abort(); }
int main(int argc,char **argv){
  nwii::runtime::CPUContext ctx;
  ctx.pc=0x80005FFC;ctx.lr=0x81234560;ctx.msr=argc>1?0x8000:0;
  nwii::runtime::g_ctx_ptr=&ctx;
  nwii::runtime::g_mmu=&ctx.mmu;
  constexpr int count=1000000;
  auto start=std::chrono::steady_clock::now();auto cpu=std::clock();
  for(int i=0;i<count;++i){ctx.inst_count=i+1;if(nwii::runtime::process_pending_callbacks(ctx))return 1;}
  std::cout<<"CALLBACK_BENCH calls="<<count<<" wall_us="
    <<std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-start).count()
    <<" cpu_us="<<(std::clock()-cpu)*1000000.0/CLOCKS_PER_SEC<<" msr="<<ctx.msr<<std::endl;
  return ctx.pc!=0x80005FFC || ctx.lr!=0x81234560 || ctx.inst_count!=count ? 2:0;
}
