// NATIVE_CACHE_BOUNDS_V1: patched census export (top-N rows, cache occupancy block). Run with NWII_PERF_CENSUS=1 NWII_PERF_PC_HISTOGRAM=1.
#include "runtime/native_perf_census_export.h"
#include <cstdio>
#include <cstdlib>
#include <random>
#define REQUIRE(cond) do{if(!(cond)){std::fprintf(stderr,"FAIL %s:%d %s\n",__FILE__,__LINE__,#cond);std::exit(1);}}while(0)
int main(){
 REQUIRE(native_perf_census::enabled && native_perf_census::pc_histograms);
 std::mt19937_64 rng(5);
 for(auto* slots:{native_perf_census::entry_pcs,native_perf_census::switch_pcs}){
  const unsigned rows=slots==native_perf_census::entry_pcs?40000:9;
  for(unsigned i=0;i<rows;++i){slots[i].key.store(0x80000000ull+i*4+1);slots[i].calls.store(1+rng()%500);} // many equal counts
 }
 for(auto* slots:{native_perf_census::entry_pcs,native_perf_census::switch_pcs}){
  const auto all=native_perf_census::PcRows(slots);
  for(size_t i=1;i<all.size();++i)REQUIRE(all[i-1].calls>all[i].calls || (all[i-1].calls==all[i].calls && all[i-1].pc<all[i].pc));
  for(size_t limit:{size_t(0),size_t(1),size_t(16),size_t(17),size_t(8),all.size(),all.size()+5}){
   const auto top=native_perf_census::PcRows(slots,limit);
   REQUIRE(top.size()==std::min(limit,all.size()));
   for(size_t i=0;i<top.size();++i)REQUIRE(top[i].pc==all[i].pc && top[i].calls==all[i].calls);
  }
 }
 native_cache_stats::Set(native_cache_stats::c.texture_entries,1234);native_cache_stats::Set(native_cache_stats::c.shader_programs,77);
 const auto json=native_perf_census::Json();const auto text=native_perf_census::Text();
 REQUIRE(json.rfind("{\"cache_occupancy\":{",0)==0);
 REQUIRE(json.find("\"texture_cache_entries\":1234")!=std::string::npos && json.find("\"shader_programs\":77")!=std::string::npos);
 REQUIRE(json.find("\"entry_pcs\":[")!=std::string::npos);                                  // the export still lists every row
 size_t depth=0;for(char c:json){if(c=='{'||c=='[')++depth;if(c=='}'||c==']')--depth;}REQUIRE(depth==0);
 REQUIRE(text.find("Cache occupancy")!=std::string::npos && text.find("texture cache entries/bytes/hot keys: 1234/")!=std::string::npos);
 REQUIRE(text.find("Top routed entry PCs")!=std::string::npos);
 size_t lines=0;for(size_t at=text.find("    0x");at!=std::string::npos;at=text.find("    0x",at+1))++lines;REQUIRE(lines==16+9);
 std::puts("PC_ROWS_TOP_N ok");
}
