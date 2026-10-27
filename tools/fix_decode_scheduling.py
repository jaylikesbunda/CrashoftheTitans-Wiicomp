"""Cost-based draw scheduling and disjoint chunks for qualified large formats."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
for name in ('native_decode_tasks.h','native_pipeline_stats.h'):(rt/'include/runtime'/name).write_text((p/'runtime-overlays'/name).read_text())
f=rt/'src/hle/gx/fifo_parser.cpp';s=f.read_text()
if 'NATIVE_DECODE_CHUNKS_V1' not in s:
 
 for header in ('native_decode_tasks.h','native_pipeline_stats.h'):
  include='#include "runtime/'+header+'"'
  if include not in s:s=include+'\n'+s
 anchor='static bool NativeTryPackedDecode(GXCommand& c){';assert s.count(anchor)==1
 observed=(p/'runtime-overlays/observed_vertex_formats.inc').read_text()
 a=observed.index('template<int Mask,int UV,int Normal,bool Colour>');b=observed.index('template<int Mask,int UV,int Normal>')
 body=observed[a:b]
 body=body.replace('NativeObservedDecode(GXCommand& c,NativePackedBuffer& out,unsigned inputs,unsigned words,const float* defaults)', 'NativeObservedRange(const DrawRaw& r,float* storage,size_t first,size_t count,unsigned inputs,unsigned words,const float* defaults)')
 body=body.replace(' const DrawRaw& r=*c.raw;', ' ')
 body=body.replace(' out.resize(size_t(r.count)*words);float* storage=out.data();size_t offset=0;', ' size_t offset=first*VertexSize(v);')
 body=body.replace(' for(unsigned i=0;i<r.count;++i)', ' for(size_t i=first;i<first+count;++i)')
 colour='''template<int Mask,int UV,int Normal> static bool NativeObservedRangeColour(const DrawRaw& r,float* storage,size_t first,size_t count,unsigned inputs,unsigned words,const float* defaults){
 return r.vat.clrMask[0]==VtxAttrMask::None?NativeObservedRange<Mask,UV,Normal,false>(r,storage,first,count,inputs,words,defaults):NativeObservedRange<Mask,UV,Normal,true>(r,storage,first,count,inputs,words,defaults);
}
static bool NativeObservedRangeDispatch(unsigned key,const DrawRaw& r,float* storage,size_t first,size_t count,unsigned inputs,unsigned words,const float* defaults){switch((key-1)/2){
'''
 for mask in (1,2,3):
  for uv in (2,3,4):
   for ni,normal in enumerate((0,1,4)):
    key=(mask-1)*9+(uv-2)*3+ni
    colour+=f'case {key}:return NativeObservedRangeColour<{mask},{uv},{normal}>(r,storage,first,count,inputs,words,defaults);\n'
 colour+='}return false;}\n'
 code='// NATIVE_DECODE_CHUNKS_V1\n'+body+colour+(p/'runtime-overlays/chunked_decode.inc').read_text()+'\n'
 s=s.replace(anchor,code+anchor,1);f.write_text(s)
f=rt/'src/hle/gx.cpp';s=f.read_text()
if 'NATIVE_COST_DECODE_V1' not in s:
 
 for header in ('native_decode_tasks.h','native_pipeline_stats.h'):
  include='#include "runtime/'+header+'"'
  if include not in s:s=include+'\n'+s
 s=s.replace('nwii::runtime::gx::GXCommand** jobs = nullptr;', 'nwii::runtime::gx::NativeDecodeTask* jobs = nullptr;',1)
 s=s.replace('void run(nwii::runtime::gx::GXCommand** list, size_t n)', 'void run(nwii::runtime::gx::NativeDecodeTask* list, size_t n)',1)
 old='            nwii::runtime::gx::FifoParser::DecodeDraw(*jobs[i]);'
 new='''            const auto& job=jobs[i];const bool stats=native_hud::on.load(std::memory_order_relaxed);const uint64_t start=stats?native_hud::now_us():0;
            if(job.chunks)nwii::runtime::gx::NativeRunDecodeChunk(job.chunks,job.first,job.count);
            else nwii::runtime::gx::FifoParser::DecodeDraw(*job.command);
            if(stats){native_pipeline::max(native_pipeline::largest_vertices,job.count);native_pipeline::max(native_pipeline::largest_job_us,native_hud::now_us()-start);}'''
 assert s.count(old)==1;s=s.replace(old,new,1)
 a=s.index('        // NATIVE_PARALLEL_DECODE_V1: independent draws');b=s.index('\n    }',a)
 new='''        // NATIVE_PARALLEL_DECODE_V1 / NATIVE_COST_DECODE_V1: command order stays unchanged.
        using namespace nwii::runtime::gx;
        static thread_local std::vector<NativeDecodeTask> jobs;
        static thread_local std::vector<std::shared_ptr<NativeChunkedDraw>> plans;
        jobs.clear();plans.clear();size_t draws=0,vertices=0,work=0;
        static const bool legacy=std::getenv("NWII_LEGACY_DECODE_SCHEDULING")!=nullptr;
        for(auto& c:frame)if(c.type==GXCommandType::DrawPrimitive){
          ++draws;const size_t count=c.raw?c.raw->count:0;
          auto plan=(legacy || count<8192 || !NativeGetDecodePool())?std::shared_ptr<NativeChunkedDraw>{}:NativePrepareDecodeChunks(c);
          if(plan){plans.push_back(plan);if(!NativeChunksNeedWork(plan))continue;vertices+=count;
            for(size_t first=0;first<count;first+=4096){size_t n=std::min(size_t(4096),count-first);jobs.push_back({&c,plan,first,n,n*96});work+=n*96;}
          }else{vertices+=count;size_t cost=c.raw?c.raw->bytes.size()+count*76:1;jobs.push_back({&c,{},0,count,cost});work+=cost;}
        }
        g_stat_draws+=draws;
        const bool parallel=legacy?draws>=8:jobs.size()>=2 && work>=32768;
        NativeDecodePool* pool=parallel?NativeGetDecodePool():nullptr;
        if(!legacy && pool)std::stable_sort(jobs.begin(),jobs.end(),[](const auto& a,const auto& b){return a.weight>b.weight;});
        if(native_hud::on.load(std::memory_order_relaxed)){
          (pool?native_pipeline::pool_batches:native_pipeline::serial_batches).fetch_add(1,std::memory_order_relaxed);
          (pool?native_pipeline::pool_vertices:native_pipeline::serial_vertices).fetch_add(vertices,std::memory_order_relaxed);
          for(const auto& job:jobs)if(job.chunks)native_pipeline::chunk_jobs.fetch_add(1,std::memory_order_relaxed);
        }
        if(pool)pool->run(jobs.data(),jobs.size());
        else for(const auto& job:jobs){const bool stats=native_hud::on.load(std::memory_order_relaxed);const uint64_t start=stats?native_hud::now_us():0;if(job.chunks)NativeRunDecodeChunk(job.chunks,job.first,job.count);else FifoParser::DecodeDraw(*job.command);if(stats){native_pipeline::max(native_pipeline::largest_vertices,job.count);native_pipeline::max(native_pipeline::largest_job_us,native_hud::now_us()-start);}}
        for(const auto& plan:plans)NativeFinishDecodeChunks(plan);
        jobs.clear();plans.clear();'''
 s=s[:a]+new+s[b:];f.write_text(s)
print('Decode uses work cost and heavy-first jobs; qualified large non-dedup draws have disjoint chunks.')
