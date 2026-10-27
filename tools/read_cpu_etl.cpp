// Offline kernel ETL reader. Layouts follow Microsoft PerfView KernelTraceEventParser.
// SampledProfile opcode46, CSwitch opcode36 (version>=2); no live session operations.
#include <windows.h>
#include <evntrace.h>
#include <evntcons.h>
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <string>
#include <tuple>
#include <vector>
static const GUID stackg={0xdef2fe46,0x7bd6,0x4b80,{0xbd,0x94,0xf5,0x7f,0xe2,0x0d,0x0c,0xe3}};
static std::set<std::pair<uint32_t,uint64_t>> sample_keys;
static std::map<std::pair<uint32_t,uint64_t>,std::vector<uint64_t>> stacks;
static const GUID perf={0xce1dbfb4,0x137e,0x4da6,{0x87,0xb0,0x3f,0x59,0xaa,0x10,0x2c,0xbc}};
static const GUID threadg={0x3d6fa8d1,0xfe05,0x11d0,{0x9d,0xda,0x00,0xc0,0x4f,0xd7,0xba,0x7c}};
static const GUID imageg={0x2cb15d1d,0x5fc1,0x11d2,{0xab,0xe1,0x00,0xa0,0xc9,0x11,0xf5,0x18}};
static std::set<std::tuple<uint32_t,uint64_t,uint64_t,std::string>> modules;
static std::set<uint32_t> tids;
static uint64_t lo,hi,events=0,samples=0,switches=0,bad=0;
static std::map<std::pair<uint32_t,uint64_t>,uint64_t> hits;
struct State { uint64_t running_since=0,off_since=0,running=0,off=0; unsigned state=0,reason=0; };
static std::map<uint32_t,State> states;
static std::map<std::tuple<uint32_t,unsigned,unsigned>,uint64_t> off_by_reason;
static std::vector<std::tuple<uint32_t,uint64_t,uint64_t,unsigned,unsigned>> intervals;
template<class T> T get(const unsigned char*p,int at){T v;memcpy(&v,p+at,sizeof(v));return v;}
static void WINAPI event_cb(EVENT_RECORD* e){
 ++events;auto t=uint64_t(e->EventHeader.TimeStamp.QuadPart);
 auto p=static_cast<const unsigned char*>(e->UserData);auto len=e->UserDataLength;
 auto op=e->EventHeader.EventDescriptor.Opcode;
 if(IsEqualGUID(e->EventHeader.ProviderId,stackg)&&op==32&&len>=16){
  auto stamp=get<uint64_t>(p,0);auto tid=get<uint32_t>(p,12);
  if(stamp>=lo&&stamp<=hi&&tids.count(tid)){
   int ptr=(e->EventHeader.Flags&EVENT_HEADER_FLAG_32_BIT_HEADER)?4:8;
   auto& dest=stacks[{tid,stamp}];for(int i=16;i+ptr<=len;i+=ptr)dest.push_back(ptr==8?get<uint64_t>(p,i):get<uint32_t>(p,i));
  }
 }
 if(IsEqualGUID(e->EventHeader.ProviderId,imageg)&&(op==10||op==3||op==4)){
  int ptr=(e->EventHeader.Flags&EVENT_HEADER_FLAG_32_BIT_HEADER)?4:8;
  int v=e->EventHeader.EventDescriptor.Version;int at=v>=2?44+3*(ptr-4):12+2*(ptr-4);
  if(v>=1&&len>at){auto pid=get<uint32_t>(p,2*ptr);auto base=ptr==8?get<uint64_t>(p,0):get<uint32_t>(p,0);auto size=ptr==8?get<uint64_t>(p,ptr):get<uint32_t>(p,ptr);
   std::wstring name;for(int i=at;i+1<len;i+=2){auto c=get<uint16_t>(p,i);if(!c)break;name.push_back(c);}
   int n=WideCharToMultiByte(CP_UTF8,0,name.data(),int(name.size()),nullptr,0,nullptr,nullptr);std::string s(n,'\0');WideCharToMultiByte(CP_UTF8,0,name.data(),int(name.size()),s.data(),n,nullptr,nullptr);modules.insert({pid,base,size,s});}
 }
 if(t<lo || t>hi)return;
 if(IsEqualGUID(e->EventHeader.ProviderId,perf)&&op==46){
  int ptr=(e->EventHeader.Flags&EVENT_HEADER_FLAG_32_BIT_HEADER)?4:8;
  if(len<ptr+8){++bad;return;}
  auto tid=get<uint32_t>(p,ptr);if(!tids.count(tid))return;
  auto ip=ptr==8?get<uint64_t>(p,0):get<uint32_t>(p,0);
  ++hits[{tid,ip}];++samples;sample_keys.insert({tid,t});
 }else if(IsEqualGUID(e->EventHeader.ProviderId,threadg)&&op==36){
  if(e->EventHeader.EventDescriptor.Version<2||len<24){++bad;return;}
  auto n=get<uint32_t>(p,0),o=get<uint32_t>(p,4);
  if(tids.count(o)) {auto& s=states[o]; if(s.running_since && t>=s.running_since)s.running+=t-s.running_since;
   s.running_since=0;s.off_since=t;s.state=p[14];s.reason=p[12];++switches;}
  if(tids.count(n)){auto& s=states[n];if(s.off_since && t>=s.off_since){s.off+=t-s.off_since;off_by_reason[{n,s.state,s.reason}]+=t-s.off_since;intervals.emplace_back(n,s.off_since,t,s.state,s.reason);}
   s.off_since=0;s.running_since=t;}
 }
}
int main(int argc,char**argv){
 if(argc<6){std::cerr<<"etl outprefix qpc_start qpc_stop tid...\n";return 2;}
 lo=std::stoull(argv[3]);hi=std::stoull(argv[4]);for(int i=5;i<argc;++i)tids.insert(std::stoul(argv[i]));
 int n=MultiByteToWideChar(CP_UTF8,0,argv[1],-1,nullptr,0);std::wstring file(n,L'\0');MultiByteToWideChar(CP_UTF8,0,argv[1],-1,file.data(),n);
 EVENT_TRACE_LOGFILEW log{};log.LogFileName=file.data();log.ProcessTraceMode=PROCESS_TRACE_MODE_EVENT_RECORD|PROCESS_TRACE_MODE_RAW_TIMESTAMP;log.EventRecordCallback=event_cb;
 auto h=OpenTraceW(&log);if(h==INVALID_PROCESSTRACE_HANDLE){std::cerr<<GetLastError();return 3;}
 auto result=ProcessTrace(&h,1,nullptr,nullptr);CloseTrace(h);
 for(auto& [tid,s]:states){if(s.running_since)s.running+=hi-s.running_since;if(s.off_since){s.off+=hi-s.off_since;off_by_reason[{tid,s.state,s.reason}]+=hi-s.off_since;}}
 std::string prefix=argv[2];std::ofstream f(prefix+".samples.csv");f<<"tid,ip,samples\n";for(auto [k,v]:hits)f<<k.first<<",0x"<<std::hex<<k.second<<std::dec<<","<<v<<"\n";
 std::ofstream s(prefix+".schedule.csv");s<<"tid,running_qpc,off_qpc\n";for(auto [tid,v]:states)s<<tid<<","<<v.running<<","<<v.off<<"\n";
 std::ofstream w(prefix+".off.csv");w<<"tid,old_state,wait_reason,qpc\n";for(auto [k,v]:off_by_reason)w<<std::get<0>(k)<<","<<std::get<1>(k)<<","<<std::get<2>(k)<<","<<v<<"\n";
 std::ofstream gaps(prefix+".off-intervals.csv");gaps<<"tid,start_qpc,end_qpc,old_state,wait_reason\n";for(auto [tid,start,end,state,reason]:intervals)gaps<<tid<<","<<start<<","<<end<<","<<state<<","<<reason<<"\n";
 std::ofstream waitstacks(prefix+".off-stacks.csv");waitstacks<<"tid,qpc,ips\n";for(auto [tid,start,end,state,reason]:intervals){auto found=stacks.find({tid,start});if(found==stacks.end())continue;waitstacks<<tid<<","<<start<<",";for(auto ip:found->second)waitstacks<<"0x"<<std::hex<<ip<<std::dec<<" ";waitstacks<<"\n";}
 std::ofstream mods(prefix+".modules.csv");mods<<"pid,base,size,path\n";for(auto [pid,base,size,path]:modules)mods<<pid<<","<<base<<","<<size<<","<<path<<"\n";
 std::ofstream stk(prefix+".stacks.csv");stk<<"tid,qpc,ips\n";for(auto k:sample_keys){auto i=stacks.find(k);if(i==stacks.end())continue;stk<<k.first<<","<<k.second<<",";for(auto ip:i->second)stk<<"0x"<<std::hex<<ip<<std::dec<<" ";stk<<"\n";}
 std::ofstream m(prefix+".meta.json");m<<"{\"events\":"<<events<<",\"target_samples\":"<<samples<<",\"switch_outs\":"<<switches<<",\"unsupported_layouts\":"<<bad<<",\"events_lost\":"<<log.EventsLost<<",\"buffers_lost\":"<<log.LogfileHeader.BuffersLost<<",\"frequency\":"<<log.LogfileHeader.PerfFreq.QuadPart<<",\"result\":"<<result<<"}\n";
 std::cout<<"events="<<events<<" samples="<<samples<<" result="<<result<<"\n";return result?4:0;
}
