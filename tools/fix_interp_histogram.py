"""Sampled histogram of guest PCs executed by the interpreter, printed with each [DIAG] line.

Capture frame-cost-20261006-102126-770700: interpret_step is 1.81 ms/frame inclusive (218 guest-thread
stacks, all from run_game) and interpret_one another 1.13 ms. The only PC evidence is the 200-line
[Interp] announce at startup (0x80266138, 0x8026e438, 0x80267384 ... all just past Ghidra function ends).
Counts one in sixteen interpreted steps; read-only, no guest-visible effect.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime/src/hle'
marker='NATIVE_INTERP_HISTOGRAM_V1'
f=rt/'interpreter.cpp';s=f.read_text()
if marker not in s:
    old='void interpret_step(CPUContext& ctx) {\n'
    assert s.count(old)==1
    new='''// NATIVE_INTERP_HISTOGRAM_V1
static uint64_t g_interp_steps=0;
static std::unordered_map<uint64_t,uint64_t> g_interp_histogram;
void native_interp_report() {
  if(g_interp_histogram.empty())return;
  std::vector<std::pair<uint64_t,uint64_t>> ranked(g_interp_histogram.begin(),g_interp_histogram.end());
  std::sort(ranked.begin(),ranked.end(),[](const auto&a,const auto&b){return a.second>b.second;});
  std::cout<<"[INTERP] steps="<<std::dec<<g_interp_steps<<" top pc/lr x16:";
  for(size_t i=0;i<ranked.size()&&i<14;++i)
    std::cout<<' '<<std::hex<<(ranked[i].first>>32)<<'/'<<uint32_t(ranked[i].first)<<'='<<std::dec<<ranked[i].second;
  std::cout<<std::endl;g_interp_histogram.clear();
}
void interpret_step(CPUContext& ctx) {
  if((++g_interp_steps&15u)==0)++g_interp_histogram[(uint64_t(ctx.pc)<<32)|ctx.lr];
'''
    s=s.replace(old,new,1)
    for inc in ('#include <unordered_map>','#include <vector>','#include <algorithm>','#include <utility>'):
        if inc not in s:s=s.replace('#include <cmath>\n','#include <cmath>\n'+inc+'\n',1)
    f.write_text(s)
f=rt/'ios.cpp';s=f.read_text()
if marker not in s:
    old='<< " cbq=" << ctx.pending_cb_count.load() << std::endl;\n'
    assert s.count(old)==1
    s=s.replace(old,old+'        native_interp_report(); // NATIVE_INTERP_HISTOGRAM_V1\n',1)
    old='void native_sched_stall_check(CPUContext &c); // NATIVE_SCHED_RECORDER_V1\n'
    assert s.count(old)==1
    s=s.replace(old,old+'void native_interp_report(); // NATIVE_INTERP_HISTOGRAM_V1\n',1)
    f.write_text(s)
print('Interpreter PC histogram installed (read-only diagnostic).')
