#pragma once
#include <array>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <iomanip>
#include <sstream>
#include <string>
namespace native_stutter {
struct WaitStats {uint64_t signal=0,timer=0,other=0,total_us=0,max_us=0,max_late_us=0;};
struct GuestThread {uint32_t address=0,state=0,queue=0,pc=0;};
struct GuestContext {uint64_t stamp=0;uint32_t pc=0,lr=0,irq=0,flags=0;unsigned count=0;std::array<GuestThread,16> threads{};};
struct Work {uint64_t vertices=0,packed=0,upload_bytes=0,draws=0,texture_checks=0,texture_misses=0,shader_compiles=0,shader_binary_hits=0,shader_source_hits=0;WaitStats guest_wait,field_wait;GuestContext guest_context;};
inline void wait_json(std::ostream& o,const WaitStats& w){o<<"{\"signal\":"<<w.signal<<",\"timer\":"<<w.timer<<",\"other\":"<<w.other<<",\"total_us\":"<<w.total_us<<",\"max_us\":"<<w.max_us<<",\"max_late_us\":"<<w.max_late_us<<'}';}
inline void wait_report(std::ostream& o,const Work& w){
 o<<"    guest waits event/timer/error "<<w.guest_wait.signal<<'/'<<w.guest_wait.timer<<'/'<<w.guest_wait.other<<"; total/max/late "<<w.guest_wait.total_us/1000.0<<'/'<<w.guest_wait.max_us/1000.0<<'/'<<w.guest_wait.max_late_us/1000.0<<" ms\n";
 o<<"    field wakes GX/poll/deadline "<<w.field_wait.signal<<'/'<<w.field_wait.timer<<'/'<<w.field_wait.other<<"; total/max/late "<<w.field_wait.total_us/1000.0<<'/'<<w.field_wait.max_us/1000.0<<'/'<<w.field_wait.max_late_us/1000.0<<" ms\n";
 const auto& c=w.guest_context;if(c.stamp){o<<"    sampled idle context: PC 0x"<<std::hex<<c.pc<<" LR 0x"<<c.lr<<" IRQ 0x"<<c.irq<<" observed flags 0x"<<c.flags<<std::dec<<" (flags: stop=1 VI=2 callback=4 IRQ=8)\n";
  for(unsigned i=0;i<c.count;++i){const auto& t=c.threads[i];o<<"      thread 0x"<<std::hex<<t.address<<" state 0x"<<t.state<<" wait queue 0x"<<t.queue<<" saved PC 0x"<<t.pc<<std::dec<<'\n';}}
}
inline void context_json(std::ostream& o,const GuestContext& c){o<<"{\"stamp_us\":"<<c.stamp<<",\"pc\":"<<c.pc<<",\"lr\":"<<c.lr<<",\"irq\":"<<c.irq<<",\"observed_flags\":"<<c.flags<<",\"threads\":[";for(unsigned i=0;i<c.count;++i){if(i)o<<',';auto& t=c.threads[i];o<<"{\"address\":"<<t.address<<",\"state\":"<<t.state<<",\"queue\":"<<t.queue<<",\"saved_pc\":"<<t.pc<<'}';}o<<"]}";}
struct Cost {const char* name=nullptr;char role='?';double ms=0,baseline=0;};
struct Sample {uint64_t stamp=0;double interval=0,guest_cpu=0,render_cpu=0;Work work;};
struct Incident {Sample sample;double baseline=0;Work usual;bool sustained=false;std::array<Cost,3> increases{};};
class History {
 std::array<Sample,240> frames{};std::array<Incident,16> events{};
 size_t frame_count=0,event_count=0;unsigned warm=0,drop_frames=0;uint64_t last_event=0;
 double usual_interval=0;std::array<double,9> usual_work{};
 static std::array<uint64_t,9> values(const Work& w){return {w.vertices,w.packed,w.upload_bytes,w.draws,w.texture_checks,w.texture_misses,w.shader_compiles,w.shader_binary_hits,w.shader_source_hits};}
 static Work work(const std::array<double,9>& w){return {uint64_t(w[0]),uint64_t(w[1]),uint64_t(w[2]),uint64_t(w[3]),uint64_t(w[4]),uint64_t(w[5]),uint64_t(w[6]),uint64_t(w[7]),uint64_t(w[8])};}
public:
 void reset(){*this=History{};}
 bool ready()const{return warm>=30;}
 double baseline()const{return usual_interval;}
 size_t incidents()const{return event_count;}
 const Incident* latest()const{return event_count?&events[(event_count-1)%events.size()]:nullptr;}
 const Sample* latest_frame()const{return frame_count?&frames[(frame_count-1)%frames.size()]:nullptr;}
 // No file IO and no allocations. Costs are elapsed exclusive scope deltas;
 // scopes that close across an XFB boundary can appear in an adjacent sample.
 bool observe(const Sample& s,const Cost* costs,size_t count){
  frames[frame_count++%frames.size()]=s;
  if(!s.stamp || s.interval<=0)return false;
  if(!warm){usual_interval=s.interval;auto v=values(s.work);for(unsigned i=0;i<v.size();++i)usual_work[i]=double(v[i]);}
  bool spike=ready() && s.interval>std::max(usual_interval*1.30,usual_interval+4.0);
  if(ready() && s.interval>usual_interval*1.20)++drop_frames;else drop_frames=0;
  bool sustained=ready() && drop_frames>=12;
  bool record=spike || (sustained && (!last_event || s.stamp-last_event>2000000));
  if(record){Incident e;e.sample=s;e.baseline=usual_interval;e.usual=work(usual_work);e.sustained=sustained;
   for(size_t i=0;i<count;++i){Cost c=costs[i];double delta=c.ms-c.baseline;
    if(delta<0.25)continue;
    for(unsigned j=0;j<3;++j)if(!e.increases[j].name || delta>e.increases[j].ms-e.increases[j].baseline){
     for(unsigned k=2;k>j;--k)e.increases[k]=e.increases[k-1];e.increases[j]=c;break;}}
   events[event_count++%events.size()]=e;last_event=s.stamp;
  }
  // Learn normal frames promptly while warming, slowly thereafter; spikes do
  // not teach the baseline to accept the very stall being diagnosed.
  if(!spike && !sustained && drop_frames<4){double alpha=warm<30?0.10:0.01;usual_interval+=(s.interval-usual_interval)*alpha;
   auto v=values(s.work);for(unsigned i=0;i<v.size();++i)usual_work[i]+=(double(v[i])-usual_work[i])*alpha;}
  ++warm;return record;
 }
 std::string summary()const {
  if(!ready())return "STUTTER WATCH: learning baseline...";
  const auto* e=latest();if(!e)return "STUTTER WATCH: no spikes recorded  |  F9 reset";
  char b[160];std::snprintf(b,sizeof b,"%s %.1f ms (+%.1f)  |  F9 reset",e->sustained?"FPS DROP":"SPIKE",e->sample.interval,e->sample.interval-e->baseline);return b;
 }
 std::string detail()const {
  const auto* e=latest();if(!e)return "F4 exports frame history and recorded cost increases";
  char b[180];const auto& c=e->increases[0];
  if(c.name)std::snprintf(b,sizeof b,"Largest increase: [%c] %s +%.2f ms",c.role,c.name,c.ms-c.baseline);
  else std::snprintf(b,sizeof b,"No completed scope explains the increase; capture CPU/GPU trace");
  return b;
 }
 std::string report()const {
  std::ostringstream o;o<<"\nStutter watch (recorded cost increases, not proven causality):\n"<<summary()<<'\n';
  if(const auto* current=latest_frame()){o<<"  latest completed frame:\n";wait_report(o,current->work);}
  const size_t n=std::min(event_count,events.size());
  for(size_t i=event_count-n;i<event_count;++i){const auto& e=events[i%events.size()];o<<"  "<<(e.sustained?"drop":"spike")<<" frame "<<e.sample.interval<<" ms; baseline "<<e.baseline<<" ms; guest/render CPU "<<e.sample.guest_cpu<<'/'<<e.sample.render_cpu<<" ms\n";
   o<<"    vertices "<<e.sample.work.vertices<<" vs "<<e.usual.vertices<<", upload bytes "<<e.sample.work.upload_bytes<<" vs "<<e.usual.upload_bytes<<", draws "<<e.sample.work.draws<<" vs "<<e.usual.draws<<", texture misses "<<e.sample.work.texture_misses<<" vs "<<e.usual.texture_misses<<'\n';
   o<<"    shaders compiled "<<e.sample.work.shader_compiles<<", binary hits "<<e.sample.work.shader_binary_hits<<", source reuse "<<e.sample.work.shader_source_hits<<'\n';
   wait_report(o,e.sample.work);
   for(const auto& c:e.increases)if(c.name)o<<"    ["<<c.role<<"] "<<c.name<<" "<<c.ms<<" ms vs "<<c.baseline<<" ms (+"<<c.ms-c.baseline<<")\n";}
  return o.str();
 }
 std::string json()const {
  std::ostringstream o;o<<"{\"baseline_ms\":"<<usual_interval<<",\"warm\":"<<(ready()?"true":"false")<<",\"events\":[";
  const size_t n=std::min(event_count,events.size());
  for(size_t i=event_count-n;i<event_count;++i){if(i!=event_count-n)o<<',';const auto& e=events[i%events.size()];
   o<<"{\"kind\":\""<<(e.sustained?"drop":"spike")<<"\",\"stamp_us\":"<<e.sample.stamp<<",\"frame_ms\":"<<e.sample.interval<<",\"baseline_ms\":"<<e.baseline<<",\"vertices\":"<<e.sample.work.vertices<<",\"baseline_vertices\":"<<e.usual.vertices<<",\"upload_bytes\":"<<e.sample.work.upload_bytes<<",\"baseline_upload_bytes\":"<<e.usual.upload_bytes<<",\"draws\":"<<e.sample.work.draws<<",\"guest_cpu_ms\":"<<e.sample.guest_cpu<<",\"render_cpu_ms\":"<<e.sample.render_cpu<<",\"shader_compiles\":"<<e.sample.work.shader_compiles<<",\"shader_binary_hits\":"<<e.sample.work.shader_binary_hits<<",\"shader_source_hits\":"<<e.sample.work.shader_source_hits<<",\"texture_misses\":"<<e.sample.work.texture_misses<<",\"increases\":[";
   bool comma=false;for(const auto& c:e.increases)if(c.name){if(comma)o<<',';comma=true;o<<"{\"scope\":"<<std::quoted(c.name)<<",\"role\":\""<<c.role<<"\",\"ms\":"<<c.ms<<",\"baseline_ms\":"<<c.baseline<<'}';}o<<"],\"guest_wait\":";wait_json(o,e.sample.work.guest_wait);o<<",\"field_wait\":";wait_json(o,e.sample.work.field_wait);o<<",\"guest_context\":";context_json(o,e.sample.work.guest_context);o<<'}';}
  o<<"],\"frames\":[";const size_t nf=std::min(frame_count,frames.size());
  for(size_t i=frame_count-nf;i<frame_count;++i){if(i!=frame_count-nf)o<<',';const auto& f=frames[i%frames.size()];o<<"{\"stamp_us\":"<<f.stamp<<",\"frame_ms\":"<<f.interval<<",\"guest_cpu_ms\":"<<f.guest_cpu<<",\"render_cpu_ms\":"<<f.render_cpu<<",\"vertices\":"<<f.work.vertices<<",\"packed_vertices\":"<<f.work.packed<<",\"upload_bytes\":"<<f.work.upload_bytes<<",\"draws\":"<<f.work.draws<<",\"texture_checks\":"<<f.work.texture_checks<<",\"texture_misses\":"<<f.work.texture_misses<<",\"shader_compiles\":"<<f.work.shader_compiles<<",\"shader_binary_hits\":"<<f.work.shader_binary_hits<<",\"shader_source_hits\":"<<f.work.shader_source_hits<<",\"guest_wait\":";wait_json(o,f.work.guest_wait);o<<",\"field_wait\":";wait_json(o,f.work.field_wait);o<<",\"guest_context\":";context_json(o,f.work.guest_context);o<<'}';}
  return o.str()+"]}";
 }
};
}
