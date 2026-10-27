#pragma once
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <algorithm>
#include <iomanip>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace native_trace {
inline uint64_t now_us() {
  return uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count());
}
inline const std::string &prefix() {
  static const std::string p=[](){const char* e=std::getenv("NWII_FRAME_TRACE");return e?std::string(e):std::string();}();
  return p;
}
inline bool enabled(){return !prefix().empty();}
inline std::atomic<bool> active{false};
inline std::atomic<unsigned> frame{0};
inline std::atomic<unsigned> phase{0}; // ready, armed, capturing, draining, saved
inline std::atomic<unsigned> gpu_outstanding{0},gpu_missing{0};
inline uint64_t start_us=0,stop_us=0,last_frame_us=0;
inline uint64_t armed_us=0;
inline unsigned target=120;
inline const char* stop_reason="complete";
struct Event {
  const char* name=nullptr;
  uint64_t ts=0,dur=0,value=0,aux=0;
  unsigned frame=0,weight=1;
  char kind='X';
};
constexpr unsigned Capacity=1048576,CriticalCapacity=1024,MaxThreads=8;
struct Buffer {
  std::unique_ptr<Event[]> events;
  std::atomic<unsigned> committed{0},dropped{0},open{0};
  std::atomic<unsigned> critical_committed{0};
  unsigned tid=0;
  Buffer():events(new Event[Capacity+CriticalCapacity]) {
#ifdef _WIN32
    tid=GetCurrentThreadId();
#endif
  }
};
inline std::unique_ptr<Buffer> buffers[MaxThreads];
inline std::atomic<unsigned> buffer_count{0};
inline std::mutex registration;
inline Buffer* local_buffer() {
  static thread_local Buffer* local=nullptr;
  if(!local) {
    std::lock_guard<std::mutex> lock(registration);
    const unsigned i=buffer_count.load();
    if(i>=MaxThreads)return nullptr;
    buffers[i]=std::make_unique<Buffer>();local=buffers[i].get();
    buffer_count.store(i+1,std::memory_order_release);
  }
  return local;
}
inline void record(Event e) {
  auto* b=local_buffer();if(!b)return;
  // Completed frame records must survive a flood of optional detail events.
  if(e.name && (std::string_view(e.name)=="completed.frame" || std::string_view(e.name)=="host.cpu_us")) {
    const unsigned n=b->critical_committed.load(std::memory_order_relaxed);
    if(n<CriticalCapacity){b->events[Capacity+n]=e;b->critical_committed.store(n+1,std::memory_order_release);return;}
    b->dropped.fetch_add(1,std::memory_order_relaxed);return;
  }
  const unsigned i=b->committed.load(std::memory_order_relaxed);
  if(i>=Capacity){b->dropped.fetch_add(1,std::memory_order_relaxed);return;}
  b->events[i]=e;b->committed.store(i+1,std::memory_order_release);
}
inline void mark(const char* name,uint64_t value=0,uint64_t aux=0) {
  if(active.load(std::memory_order_relaxed))record({name,now_us(),0,value,aux,frame.load(),1,'i'});
}
struct Scope {
  const char* name;uint64_t start=0;unsigned id=0,weight=1;Buffer* owner=nullptr;
  explicit Scope(const char* n,unsigned w=1):name(n),weight(w) {
    if(n && *n && active.load(std::memory_order_relaxed)){
      if(n[0]=='g'&&n[1]=='x'&&n[2]=='.'&&n[3]=='p'&&std::strcmp(n,"gx.publish")==0){ // NATIVE_TRACE_SCOPE_FAST_V1
        static std::atomic<unsigned> publish_phase{0};
        if((publish_phase.fetch_add(1,std::memory_order_relaxed)&63u)!=0)return;
        weight*=64;
      }
      owner=local_buffer();if(owner)owner->open.fetch_add(1);id=frame.load();start=now_us();
    }
  }
  void close(){if(start)record({name,start,now_us()-start,0,0,id,weight,'X'});if(owner)owner->open.fetch_sub(1);start=0;owner=nullptr;}
  ~Scope(){close();}
};
inline unsigned open_scopes() {
  unsigned count=0;for(unsigned i=0;i<buffer_count.load(std::memory_order_acquire);++i)count+=buffers[i]->open.load();return count;
}
struct CallbackSample {
  Scope scope;
  static inline thread_local Scope* current=nullptr;
  // Sampling does not require a per-thread phase. MinGW emulated TLS here
  // otherwise takes pthread locks on EVERY active guest poll (ETW: ~14%).
  // Relaxed atomic preserves race safety if a future second CPU polls too.
  static inline std::atomic<unsigned> counter{0};
  static const char* choose(uint32_t pc,uint32_t lr,uint64_t instructions) {
    if(!active.load(std::memory_order_relaxed))return "";
    // Hash the poll number: a power-of-two stride otherwise samples only the
    // every-16th scheduler/clock path and overstates average callback cost.
    uint32_t pick=(counter.fetch_add(1,std::memory_order_relaxed)+1u)*0x9e3779b9u;pick^=pick>>16;
    if(pick&4095u)return "";
    record({"guest.pc",now_us(),0,pc,lr,frame.load(),4096,'i'});
    mark("guest.instructions",instructions);
    return "callback.sample";
  }
  explicit CallbackSample(const char* selected_name):scope(selected_name,4096) {
    if(scope.start)current=&scope;
    // Empty names must not take clocks on unsampled polls.
  }
  CallbackSample(uint32_t pc,uint32_t lr,uint64_t inst):CallbackSample(choose(pc,lr,inst)) {}
  ~CallbackSample(){if(current==&scope)current=nullptr;}
};
inline void close_callback_sample(){if(CallbackSample::current){CallbackSample::current->close();CallbackSample::current=nullptr;}}
#ifdef _WIN32
inline std::atomic<HANDLE> guest_handle{nullptr};
inline std::atomic<unsigned> guest_tid{0};
inline uint64_t guest_previous=0,host_previous=0;
inline uint64_t cpu_us(HANDLE h) {
  FILETIME c,e,k,u;if(!h || !GetThreadTimes(h,&c,&e,&k,&u))return 0;
  auto ticks=[](FILETIME f){return (uint64_t(f.dwHighDateTime)<<32)|f.dwLowDateTime;};
  return (ticks(k)+ticks(u))/10;
}
#endif
inline void guest_thread() {
  if(!enabled())return;
  local_buffer();
#ifdef _WIN32
  guest_handle.store(OpenThread(THREAD_QUERY_LIMITED_INFORMATION,FALSE,GetCurrentThreadId()));
  guest_tid.store(GetCurrentThreadId());
#endif
}
inline void finish(const char* reason) {
  if(phase.load()!=2 && phase.load()!=1)return;
  stop_us=now_us();stop_reason=reason;
  active.store(false,std::memory_order_release);phase.store(3);
  std::printf("[FrameTrace] captured %u frames; draining queries\n",frame.load()>0?frame.load()-1:0);
  std::fflush(stdout);
}
inline void boundary(uint64_t copies) {
  if(!enabled())return;
  if(phase.load()==1) {
    start_us=last_frame_us=now_us();frame.store(1);
#ifdef _WIN32
    guest_previous=cpu_us(guest_handle.load());
    host_previous=cpu_us(GetCurrentThread());
#endif
    phase.store(2);active.store(true,std::memory_order_release);
    mark("capture.begin",copies);return;
  }
  if(phase.load()!=2)return;
  const uint64_t t=now_us();const unsigned f=frame.load();
  uint64_t guest=0;
#ifdef _WIN32
  const uint64_t cpu=cpu_us(guest_handle.load());guest=cpu>=guest_previous?cpu-guest_previous:0;guest_previous=cpu;
  const uint64_t host=cpu_us(GetCurrentThread());
  record({"host.cpu_us",t,0,host>=host_previous?host-host_previous:0,0,f,1,'i'});host_previous=host;
#endif
  record({"completed.frame",last_frame_us,t-last_frame_us,guest,copies,f,1,'F'});
  last_frame_us=t;frame.store(f+1);
  if(f>=target)finish("complete");
}
inline void dump() {
  std::ofstream out(prefix()+".json");
  out<<"{\"schema\":1,\"start_us\":"<<start_us<<",\"stop_us\":"<<stop_us
     <<",\"requested_frames\":"<<target<<",\"reason\":\""<<stop_reason<<"\",\"traceEvents\":[";
  bool first=true;uint64_t dropped=0;
  for(unsigned i=0;i<buffer_count.load(std::memory_order_acquire);++i) {
    auto& b=*buffers[i];const unsigned count=b.committed.load(std::memory_order_acquire);
    dropped+=b.dropped.load();
    const unsigned critical=b.critical_committed.load(std::memory_order_acquire);
    for(unsigned j=0;j<count+critical;++j) {
      const auto&e=b.events[j<count?j:Capacity+j-count];if(!e.name || !*e.name)continue;
      if(!first)out<<',';first=false;
      // GPU durations are counters anchored to submission time, not falsely clock-aligned spans.
      const bool span=e.kind=='X'||e.kind=='F';
      out<<"{\"name\":\""<<e.name<<"\",\"cat\":\""<<(e.kind=='G'?"gpu":"cpu")
         <<"\",\"ph\":\""<<(span?'X':e.kind=='G'?'C':'i')<<"\",\"pid\":1,\"tid\":"<<b.tid
         <<",\"ts\":"<<e.ts;
      if(span)out<<",\"dur\":"<<e.dur;
      if(e.kind=='i')out<<",\"s\":\"t\"";
      out<<",\"args\":{\"frame\":"<<e.frame<<",\"weight\":"<<e.weight
         <<",\"value\":"<<e.value<<",\"aux\":"<<e.aux<<"}}";
    }
  }
  out<<"],\"dropped_events\":"<<dropped<<",\"missing_gpu_queries\":"<<gpu_outstanding.load()+gpu_missing.load()<<",\"open_cpu_scopes\":"<<open_scopes();
#ifdef _WIN32
  out<<",\"guest_cpu_available\":"<<(guest_handle.load()?"true":"false");
  out<<",\"guest_tid\":"<<guest_tid.load();
#endif
  out<<"}";out.close();
  std::ofstream done(prefix()+".done");done<<(out?"saved":"write_failed");
  phase.store(4);std::printf("[FrameTrace] saved %s.json\n",prefix().c_str());std::fflush(stdout);
}
inline void poll(bool gpu_drained=true) {
  if(!enabled())return;
  static uint64_t checked=0;const auto now=now_us();
  if(now-checked<100000)return;checked=now;
  if(phase.load()==0 && std::filesystem::exists(prefix()+".arm")) {
    std::ifstream arm(prefix()+".arm");unsigned n=120;arm>>n;arm.close();target=std::clamp(n,1u,240u);
    std::filesystem::remove(prefix()+".arm");local_buffer();armed_us=now;phase.store(1);
    std::printf("[FrameTrace] armed for %u completed frames\n",target);std::fflush(stdout);
  }
  if((phase.load()==1 || phase.load()==2) && (std::filesystem::exists(prefix()+".stop") || now-armed_us>60000000))
    finish(now-armed_us>60000000?"timeout":"stop_requested");
  if(phase.load()==3 && now-stop_us>=250000 && ((gpu_drained && open_scopes()==0) || now-stop_us>2000000))dump();
}
}
