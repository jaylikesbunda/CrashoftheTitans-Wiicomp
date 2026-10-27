#pragma once
#include "runtime/native_frame_trace.h"
#include <glad/glad.h>
#include "runtime/native_resolution.h"
#include <deque>
namespace native_trace {
struct PendingGPU {GLuint queries[2];uint64_t cpu,end_cpu=0;unsigned frame,weight=1;const char* name;};
inline std::deque<PendingGPU> pending_gpu;
struct GPUScope {
  PendingGPU item{};bool measuring=false;
  explicit GPUScope(const char* name,unsigned stride=1) {
    if(!active.load(std::memory_order_relaxed))return;
    mark(name,1,0); // Census of all operations, including unsampled ones.
    static unsigned draw_phase=0;
    if(stride>1) {
      unsigned pick=(++draw_phase)*0x9e3779b9u;pick^=pick>>16;
      if(pick%stride)return; // Avoid repeatedly selecting the same draw in a frame.
    }
    if(!glQueryCounter || !glGetQueryObjectui64v || pending_gpu.size()>=4096){++gpu_missing;return;}
    item.cpu=now_us();item.frame=frame.load();item.name=name;item.weight=stride;
    glGenQueries(2,item.queries);glQueryCounter(item.queries[0],GL_TIMESTAMP);measuring=true;
  }
  ~GPUScope(){if(measuring){glQueryCounter(item.queries[1],GL_TIMESTAMP);item.end_cpu=now_us();pending_gpu.push_back(item);gpu_outstanding.store(unsigned(pending_gpu.size()));}}
};
inline bool gpu_poll() {
  if(!enabled())return true;
  while(!pending_gpu.empty()) {
    auto item=pending_gpu.front();GLint ready=0;
    glGetQueryObjectiv(item.queries[1],GL_QUERY_RESULT_AVAILABLE,&ready);
    if(!ready)break;
    GLuint64 begin=0,end=0;glGetQueryObjectui64v(item.queries[0],GL_QUERY_RESULT,&begin);
    glGetQueryObjectui64v(item.queries[1],GL_QUERY_RESULT,&end);
    record({item.name,item.cpu,0,end>=begin?(end-begin)/1000:0,item.end_cpu-item.cpu,item.frame,item.weight,'G'});
    glDeleteQueries(2,item.queries);pending_gpu.pop_front();
    gpu_outstanding.store(unsigned(pending_gpu.size()));
  }
  return pending_gpu.empty();
}
inline void control() {
  if(!enabled())return;
  const bool drained=gpu_poll();
  if(phase.load()==3)record({"gpu.pending_queries",now_us(),0,pending_gpu.size(),gpu_missing,frame.load(),1,'i'});
  poll(drained);
  static bool info_written=false;
  if(phase.load()==1 && !info_written) {
    info_written=true;std::ofstream info(prefix()+".gpu.json");
    const auto renderer=glGetString(GL_RENDERER),version=glGetString(GL_VERSION);
    info<<"{\"renderer\":"<<std::quoted(renderer?reinterpret_cast<const char*>(renderer):"unavailable")
        <<",\"version\":"<<std::quoted(version?reinterpret_cast<const char*>(version):"unavailable")
        <<",\"efb_scale\":"<<NativeResolutionScale()<<",\"efb_width\":"<<NativeEfbWidth()
        <<",\"efb_height\":"<<NativeEfbHeight()<<"}";
  }
  if(phase.load()==4 && !pending_gpu.empty()) {
    gpu_missing+=unsigned(pending_gpu.size());
    for(auto q:pending_gpu)glDeleteQueries(2,q.queries);
    pending_gpu.clear();
    gpu_outstanding.store(0);
  }
}
}
