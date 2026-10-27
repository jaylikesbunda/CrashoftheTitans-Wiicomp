"""Exercise staged production scheduling, key ownership and exact texture validation."""
from pathlib import Path
import argparse,os,subprocess,tempfile
import fix_audit_speedups as stage
from port_paths import winlibs_bin
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,required=True);args=ap.parse_args()
    rt=args.port/'build/nwii-full-source/nWiiRuntime'
    owners=[('src/hle/gx.cpp',stage.gx),('src/hle/gx/fifo_parser.cpp',stage.parser),('src/hle/gx/renderer_gl.cpp',stage.renderer),
            ('src/hle/texture_cache_gl.cpp',stage.texture),('include/runtime/texture_cache_gl.h',stage.texture_header),('src/core/main.cpp',stage.main_source),('src/hle/ios.cpp',stage.ios)]
    for rel,fn in owners:
        raw=(rt/rel).read_text();plain=stage.restore(raw);patched=fn(plain)
        assert patched==raw and fn(patched)==patched and stage.restore(patched)==plain,rel
    gx=(rt/'src/hle/gx.cpp').read_text();parser=(rt/'src/hle/gx/fifo_parser.cpp').read_text();texture=(rt/'src/hle/texture_cache_gl.cpp').read_text()
    pool=stage.method(gx,'struct NativeDecodePool')+';'
    key=stage.method(parser,'struct NativeGeometryKeyView')+';'
    digest=stage.method(parser,'static uint64_t NativeGeometryHash(')
    efb=stage.method(texture,'bool TextureCache::efb_guest_unchanged(')
    unchanged=stage.method(texture,'auto unchanged=[&](Entry& entry)')+';'
    code=r'''
#include <algorithm>
#include <array>
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <mutex>
#include <thread>
#include <unordered_map>
#include <vector>
#include "runtime/native_tlut_epoch.h"
#include "runtime/native_gx_batching.h"
namespace native_trace{struct Scope{Scope(const char*){}};inline bool enabled(){return false;}inline void local_buffer(){}inline void mark(const char*){}}
namespace native_hud{inline std::atomic<bool> on{false};inline uint64_t now_us(){return 0;}}
namespace native_pipeline{inline unsigned largest_vertices=0,largest_job_us=0;template<class A,class B>void max(A&,B){}}
namespace nwii::runtime::gx {
struct Command{std::atomic<unsigned> calls{0};};
struct NativeDecodeTask{Command* command;void* chunks=nullptr;size_t first=0,count=0;};
struct FifoParser{static void DecodeDraw(Command& c){c.calls.fetch_add(1);}};
inline void NativeRunDecodeChunk(void*,size_t,size_t){std::abort();}
}
'''+pool+r'''
class NativeDrawBytes {
 std::shared_ptr<std::vector<uint8_t>> p=std::make_shared<std::vector<uint8_t>>();
public:
 size_t size()const{return p->size();}size_t allocated_bytes()const{return p->capacity();}
 const uint8_t* data()const{return p->data();}const uint8_t& operator[](size_t i)const{return (*p)[i];}
 void fill(size_t n,uint8_t v){p=std::make_shared<std::vector<uint8_t>>(n,v);}
 bool operator==(const NativeDrawBytes& b)const{return *p==*b.p;}
};
'''+key+'\n'+digest+r'''
template<size_t N>struct Bank:std::vector<uint8_t>{Bank():std::vector<uint8_t>(N){}};
struct CPUContext{struct MMU{Bank<1024> mem1;Bank<128> mem2;const uint8_t* get_ptr(uint32_t a){return a<mem1.size()?mem1.data()+a:nullptr;}}mmu;};
uint64_t generation=1,newest=1;bool tracking=true;
uint64_t native_ram_generation(){return generation;}
uint64_t native_ram_newest_write(const uint8_t*,size_t,size_t,size_t){return tracking?newest:UINT64_MAX;}
unsigned NativeGuestHash(const uint8_t*,unsigned){return 0;}
struct TextureCache{
 struct EfbGuest{unsigned hash=0,bytes=0;std::vector<uint8_t> source;uint64_t verified_gen=0;};
 std::unordered_map<unsigned,EfbGuest> efb_guest;
 bool efb_guest_unchanged(CPUContext&,uint32_t);
};
'''+efb+r'''
struct Entry{std::vector<uint8_t> source,palette;uint64_t verified_gen=0,palette_gen=0;};
void palette_tests(){
 bool span=true,write_watch=false;size_t size=0,palette_bytes=512;std::array<uint8_t,512> bytes{};
 const uint8_t *palette=bytes.data(),*source=nullptr,*ram_base=nullptr;size_t ram_bank_bytes=0,ram_offset=0;
 '''+unchanged+r'''
 Entry entry;entry.palette.assign(bytes.begin(),bytes.end());assert(unchanged(entry));
 bytes[511]=7;native_tlut::Changed();assert(!unchanged(entry));
 bytes[511]=0;assert(unchanged(entry));native_tlut::Changed();bytes[0]=9;assert(!unchanged(entry));
}
int main(){
 auto* pool=new NativeDecodePool;pool->helpers=3;
 for(unsigned i=0;i<3;++i)std::thread([pool]{pool->loop();}).detach();
 for(unsigned pass=0;pass<4;++pass)for(unsigned n=1;n<=64;++n){
  auto commands=std::make_unique<nwii::runtime::gx::Command[]>(n);std::vector<nwii::runtime::gx::NativeDecodeTask> jobs;
  for(unsigned i=0;i<n;++i)jobs.push_back({&commands[i]});pool->run(jobs.data(),n);
  for(unsigned i=0;i<n;++i)assert(commands[i].calls.load()==1);
 }
 NativeDrawBytes bytes;bytes.fill(10000,5);NativeGeometryKeyView view;view.header={1,2,3};view.borrowed=&bytes;
 const auto hash=NativeGeometryHash(view);view.freeze();bytes.fill(10000,7);assert(view.bytes()[9999]==5);
 NativeGeometryKeyView equal=view;assert(equal==view && NativeGeometryHash(equal)==hash);equal.header[0]=9;assert(!(equal==view));
 CPUContext ctx;TextureCache cache;auto& e=cache.efb_guest[16];e.bytes=512;e.source.assign(512,0);e.verified_gen=1;
 assert(cache.efb_guest_unchanged(ctx,16));ctx.mmu.mem1[16+511]=1;newest=++generation;assert(!cache.efb_guest_unchanged(ctx,16));
 ctx.mmu.mem1[16+511]=0;assert(cache.efb_guest_unchanged(ctx,16));tracking=false;ctx.mmu.mem1[16]=1;assert(!cache.efb_guest_unchanged(ctx,16));
 palette_tests();
 // Existing publish-before-park protocol must retain its mode-specific threshold.
 const auto mode=native_gx_batching::Selected();{native_gx_batching::Park p;
  if(native_gx_batching::Adaptive(mode))assert(native_gx_batching::ShouldRequest(native_gx_batching::Threshold(mode,true)));
 }
 std::cout<<"Audit checks passed: 256 decode batches exactly once; key ownership/collision equality; EFB dirty/fallback; palette epoch; parked threshold.\n";
}
'''
    with tempfile.TemporaryDirectory(prefix='audit-speedups-') as tmp:
        tmp=Path(tmp);cpp=tmp/'checks.cpp';exe=tmp/'checks.exe';cpp.write_text(code)
        env=os.environ.copy();env['PATH']=str(winlibs_bin())+os.pathsep+env['PATH']
        command=[str(winlibs_bin()/'g++.exe'),'-std=c++20','-O2','-pthread','-I',str(rt/'include'),str(cpp),'-o',str(exe)]
        result=subprocess.run(command,capture_output=True,text=True,env=env);print(result.stdout+result.stderr)
        assert result.returncode==0
        for mode in ('legacy','adaptive4','adaptive8','fixed4','fixed8'):
            for fallback in (False,True):
                env['NWII_GX_BATCHING']=mode
                if fallback:env['NWII_NO_WRITE_WATCH']='1';env['NWII_NO_PALETTE_EPOCH']='1'
                else:env.pop('NWII_NO_WRITE_WATCH',None);env.pop('NWII_NO_PALETTE_EPOCH',None)
                result=subprocess.run([str(exe)],capture_output=True,text=True,env=env,timeout=60)
                assert result.returncode==0,(mode,fallback,result.stdout,result.stderr)
        print(result.stdout.strip());print('All five batching modes and tracking/epoch fallbacks passed; staging reversible and idempotent.')
if __name__=='__main__':main()
