"""Reversible post-V13 scheduling, geometry, texture and presentation improvements."""
from pathlib import Path
import argparse,base64,re,shutil
MARK='NATIVE_AUDIT_SPEEDUPS_V1'
BLOCK=re.compile(r'/\* '+MARK+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARK+r' \*/',re.S)
ROOT=Path(__file__).resolve().parents[1]
def restore(s):return BLOCK.sub(lambda m:base64.b64decode(m[1]).decode(),s)
def edit(s,old,new):
    assert s.count(old)==1,('audit anchor',old[:110],s.count(old))
    return s.replace(old,'/* '+MARK+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+MARK+' */',1)
def method(s,anchor):
    a=s.index(anchor);b=s.index('{',a);n=b+1;depth=1
    while depth:
        depth+=(s[n]=='{')-(s[n]=='}');n+=1
    return s[a:n]
def gx(s):
    s=restore(s)
    s=edit(s,'#include <thread>','#include <thread>\n#include "runtime/native_tlut_epoch.h"')
    s=edit(s,'    s.pod(gx::g_state);','    s.pod(gx::g_state);if(s.loading)native_tlut::Changed();')
    a=s.index('    void work() {');b=s.index('    void loop()',a)
    old=s[a:b]
    body=old[old.index('            const auto& job='):old.index('\n        }')]
    new='    void execute(size_t i) {\n'+body+'\n    }\n    void work() {\n        for(;;){const size_t i=next.fetch_add(1,std::memory_order_relaxed);if(i>=count)break;execute(i);}\n    }\n'
    s=edit(s,old,new)
    old=method(s,'    void run(nwii::runtime::gx::NativeDecodeTask* list, size_t n)')
    new='''    void run(nwii::runtime::gx::NativeDecodeTask* list, size_t n) {
        if(!n)return;
        // Reserve job zero before notifying workers. Every lane starts useful
        // work immediately; no yield loop waits for another lane's first claim.
        {std::lock_guard<std::mutex> lock(mutex);
         jobs=list;count=n;next.store(1,std::memory_order_relaxed);
         outstanding=helpers;++generation;}
        wake.notify_all();execute(0);work();
        std::unique_lock<std::mutex> lock(mutex);
        native_trace::Scope decode_wait("gx.decode_wait");
        finished.wait(lock,[&]{return outstanding==0;});
    }'''
    s=edit(s,old,new)
    old='for(size_t first=0;first<count;first+=4096){size_t n=std::min(size_t(4096),count-first);'
    new='const size_t lanes=size_t(NativeGetDecodePool()->helpers)+1;const size_t chunk=std::clamp((count+lanes-1)/lanes,size_t(1024),size_t(4096));\n            for(size_t first=0;first<count;first+=chunk){size_t n=std::min(chunk,count-first);'
    return edit(s,old,new)
def renderer(s):
    s=restore(s)
    s=edit(s,'#include "runtime/native_batch_boundaries.h"','#include "runtime/native_batch_boundaries.h"\n#include "runtime/native_tlut_epoch.h"')
    # Signature index uses vector offsets. Rebuild only on the rare eviction;
    # every hit still verifies stride/count and all immutable source identities.
    old='static std::vector<NativeRetainedGpu> s_geometry_gpu;'
    s=edit(s,old,old+'\nstatic std::unordered_multimap<uint64_t,size_t> s_geometry_gpu_index;')
    old='for(auto& entry:s_geometry_gpu)if(entry.signature==signature && entry.stride==stride && entry.count==vertices && same_sources(entry)){'
    new='const auto matches=s_geometry_gpu_index.equal_range(signature);\n for(auto match=matches.first;match!=matches.second;++match){auto& entry=s_geometry_gpu[match->second];if(!(entry.stride==stride && entry.count==vertices && same_sources(entry)))continue;'
    s=edit(s,old,new)
    old='s_geometry_gpu.erase(it);'
    s=edit(s,old,old+'\n  s_geometry_gpu_index.clear();for(size_t i=0;i<s_geometry_gpu.size();++i)s_geometry_gpu_index.emplace(s_geometry_gpu[i].signature,i);')
    old='s_geometry_gpu.push_back(std::move(entry));'
    s=edit(s,old,'s_geometry_gpu_index.emplace(signature,s_geometry_gpu.size());'+old)
    # Bound both stream and retained copies even if a mixed layout is supplied.
    for prefix in ('std::memcpy(cursor,c.native_packed.data()+i*c.native_packed_stride,stride);',
                   'std::memcpy(destination,command.native_packed.data()+i*command.native_packed_stride,stride);'):
        dest='cursor' if 'cursor' in prefix else 'destination'
        cmd='c' if dest=='cursor' else 'command'
        s=edit(s,prefix,'{const size_t copied=std::min(stride,size_t('+cmd+'.native_packed_stride)*sizeof(float));std::memcpy('+dest+','+cmd+'.native_packed.data()+i*'+cmd+'.native_packed_stride,copied);if(copied<stride)std::memset('+dest+'+copied,0,stride-copied);}')
    # Track the actual GL allocation; texture IDs can be recycled after clear.
    old='  { native_trace::GPUScope budget_operation("gpu.texture_transfer_us",1); glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,width,height,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr); }'
    new='''  static std::unordered_map<GLuint,std::pair<int,int>> dimensions;
  auto& allocated=dimensions[texture];
  // register_efb_texture owns deletion; its serial changes on cache clear.
  static uint64_t cache_serial=0;
  const auto serial=native_tlut::efb_serial.load(std::memory_order_relaxed);
  if(cache_serial!=serial){dimensions.clear();cache_serial=serial;}
  auto& current=dimensions[texture];
  if(current.first!=width || current.second!=height){
    glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,width,height,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);current={width,height};
  }'''
    s=edit(s,old,new.replace('  auto& allocated=dimensions[texture];\n',''))
    old='          if (native_tlut_cmd && !native_tlut_cmd->native_tlut.empty()) std::memcpy(&g_state.tlutMem[dst], native_tlut_cmd->native_tlut.data(), native_tlut_cmd->native_tlut.size());'
    s=edit(s,old,old+'\n          native_tlut::Changed();')
    return s
def parser(s):
    s=restore(s)
    s=edit(s,'#include "runtime/cpu_context.h"','#include "runtime/cpu_context.h"\n#include "runtime/native_tlut_epoch.h"')
    # A key borrows the live immutable encoded stream during lookup; only cache
    # admission takes an owning copy. Exact bytes still resolve hash collisions.
    old='struct NativeRetainedDraw {'
    view='''struct NativeGeometryKeyView {
 std::vector<uint8_t> header;const NativeDrawBytes* borrowed=nullptr;NativeDrawBytes owned;
 const NativeDrawBytes& bytes()const{return borrowed?*borrowed:owned;}
 void freeze(){if(borrowed){owned=*borrowed;borrowed=nullptr;}}
 size_t capacity()const{return header.capacity()+owned.allocated_bytes();}
 bool operator==(const NativeGeometryKeyView& b)const{return header==b.header && bytes()==b.bytes();}
};
'''
    s=edit(s,old,view+old)
    # Wrap each contiguous declaration/signature, not arbitrary generated bytes.
    for old,new in (
      ('std::vector<uint8_t> key;NativePackedBuffer packed;', 'NativeGeometryKeyView key;NativePackedBuffer packed;'),
      ('std::vector<uint8_t> key;uint64_t hash=0;', 'NativeGeometryKeyView key;uint64_t hash=0;'),
      ('std::vector<uint8_t> geometry_key;', 'NativeGeometryKeyView geometry_key;'),
      ('unsigned inputs,std::vector<uint8_t>& key)', 'unsigned inputs,NativeGeometryKeyView& key)'),
      ('const std::vector<uint8_t>& key,uint64_t hash,NativePackedBuffer& out', 'const NativeGeometryKeyView& key,uint64_t hash,NativePackedBuffer& out'),
      ('std::vector<uint8_t>&& key,uint64_t hash,const NativePackedBuffer& packed', 'NativeGeometryKeyView&& key,uint64_t hash,const NativePackedBuffer& packed'),
      ('key.clear();key.reserve(192+r.bytes.size());','key.header.clear();key.header.reserve(192);key.borrowed=&r.bytes;'),
      ('key.push_back(uint8_t(n));','key.header.push_back(uint8_t(n));'),
      ('key.insert(key.end(),r.bytes.begin(),r.bytes.end());','// Encoded bytes are borrowed until admission; no per-hit key copy.'),
      ('fetch_add(key.size(),std::memory_order_relaxed);','fetch_add(key.header.size()+r.bytes.size(),std::memory_order_relaxed);'),
      ('entry.key=std::move(key);entry.packed=packed;', 'key.freeze();entry.key=std::move(key);entry.packed=packed;')):
        s=edit(s,old,new)
    old=method(s,'static uint64_t NativeGeometryHash(')
    new='''static uint64_t NativeGeometryHash(const NativeGeometryKeyView& key){
 uint64_t h=14695981039346656037ull;
 auto append=[&](const auto& bytes){size_t i=0;
  for(;i+8<=bytes.size();i+=8){uint64_t w;std::memcpy(&w,bytes.data()+i,8);h=(h^w)*1099511628211ull;h^=h>>29;}
  for(;i<bytes.size();++i)h=(h^bytes[i])*1099511628211ull;};
 append(key.header);append(key.bytes());return h;
}'''
    s=edit(s,old,new)
    # ApplyBPRegisterImpl is the sole ordinary TLUT writer. Early snapshots
    # write command-owned bytes only; the renderer's restore is covered above.
    old='nwii::runtime::g_mmu->read8(g_state.tlutSrcAddr + i);\n                }\n            }'
    s=edit(s,old,old+'\n            native_tlut::Changed();')
    # Avoid snapshot allocation/copies when every matrix value is already exact.
    old='                    const bool native_refresh_banks=!c.raw->snap_pos_bank.empty();'
    new='''                    const uint32_t native_pb=uint32_t(c.raw->defPosMtxIdx)*4,native_nb=3u*(c.raw->defPosMtxIdx&31);
                    const bool native_same_banks=c.raw->snap_pos_bank.empty() ||
                      (c.raw->snap_pos_bank.size()==256 && c.raw->snap_nrm_bank.size()==96 &&
                       !std::memcmp(c.raw->snap_pos_bank.data(),g_parse_posmtx,256*sizeof(float)) &&
                       !std::memcmp(c.raw->snap_nrm_bank.data(),g_parse_nrmmtx,96*sizeof(float)));
                    if(native_same_banks && native_pb+12<=256 && native_nb+9<=96 &&
                       !std::memcmp(c.raw->snap_pos_mtx,g_parse_posmtx+native_pb,12*sizeof(float)) &&
                       !std::memcmp(c.raw->snap_nrm_mtx,g_parse_nrmmtx+native_nb,9*sizeof(float)))continue;
'''+old
    return edit(s,old,new)
def texture_header(s):
    s=restore(s)
    old='uint64_t verified_gen=0; /* NATIVE_RAM_WRITE_WATCH_V1 */'
    s=edit(s,old,old+' uint64_t palette_gen=0;')
    old='struct EfbGuest { uint32_t hash; uint32_t bytes; std::vector<uint8_t> source; };'
    return edit(s,old,old.replace(' source;',' source; uint64_t verified_gen=0;'))
def texture(s):
    s=restore(s)
    s=edit(s,'#include "runtime/native_prompt_textures.h"','#include "runtime/native_prompt_textures.h"\n#include "runtime/native_tlut_epoch.h"')
    old='void TextureCache::clear() {'
    s=edit(s,old,old+'\n    native_tlut::Changed();native_tlut::efb_serial.fetch_add(1,std::memory_order_relaxed);')
    old='            if(exact && span)entry.source.assign(p,p+bytes);'
    new='''            if(exact && span){
              // Establish the write observation BEFORE taking the snapshot.
              const uint8_t* bank=physical<mmu.mem1.size()?mmu.mem1.data():mmu.mem2.data();
              const size_t bank_bytes=physical<mmu.mem1.size()?mmu.mem1.size():mmu.mem2.size();
              const size_t offset=physical<mmu.mem1.size()?physical:physical-0x10000000u;
              if(native_ram_newest_write(bank,bank_bytes,offset,bytes)!=UINT64_MAX)entry.verified_gen=native_ram_generation();
              entry.source.assign(p,p+bytes);
            }'''
    s=edit(s,old,new)
    old='    if(!it->second.source.empty())return std::memcmp(p,it->second.source.data(),it->second.bytes)==0;'
    new='''    if(!it->second.source.empty()){
      auto& entry=it->second;const uint32_t physical=addr&0x3fffffffu;auto& mmu=ctx.mmu;
      const bool mem1=physical<mmu.mem1.size();const size_t offset=mem1?physical:physical-0x10000000u;
      const uint8_t* bank=mem1?mmu.mem1.data():mmu.mem2.data();
      const size_t bank_bytes=mem1?mmu.mem1.size():mmu.mem2.size();
      static const bool watch=!std::getenv("NWII_NO_WRITE_WATCH") && !std::getenv("NWII_NO_EFB_WATCH");
      uint64_t before=0;
      if(watch && offset<bank_bytes && entry.bytes<=bank_bytes-offset){
        const uint64_t newest=native_ram_newest_write(bank,bank_bytes,offset,entry.bytes);
        if(newest!=UINT64_MAX){if(entry.verified_gen && newest<=entry.verified_gen)return true;before=native_ram_generation();}
      }
      if(std::memcmp(p,entry.source.data(),entry.bytes))return false;
      entry.verified_gen=before;return true;
    }'''
    s=edit(s,old,new)
    old='       if(palette_bytes && std::memcmp(palette,entry.palette.data(),palette_bytes)!=0)return false;'
    new='''       if(palette_bytes){const uint64_t before=native_tlut::Read();
         static const bool epochs=!std::getenv("NWII_NO_PALETTE_EPOCH");
         if(!epochs || entry.palette_gen!=before){
           if(std::memcmp(palette,entry.palette.data(),palette_bytes))return false;
           entry.palette_gen=before;
         }
       }'''
    s=edit(s,old,new)
    old='    Image img = decode_texture(ctx, stage);'
    s=edit(s,old,'    const uint64_t palette_before=native_tlut::Read();\n'+old)
    old='entry.verified_gen=generation_before;'
    s=edit(s,old,old+'entry.palette_gen=palette_before;')
    old='const TextureKey metadata={stage.base_addr,stage.width,stage.height,stage.format,stage.tlut_offset,stage.tlut_format,0};'
    s=edit(s,old,'const bool paletted=stage.format>=8 && stage.format<=10;\n    const TextureKey metadata={stage.base_addr,stage.width,stage.height,stage.format,paletted?stage.tlut_offset:0u,paletted?uint32_t(stage.tlut_format):0u,0};')
    old='stage.format, stage.tlut_offset, stage.tlut_format, hash};'
    return edit(s,old,'stage.format, paletted?stage.tlut_offset:0u, paletted?uint32_t(stage.tlut_format):0u, hash};')
def main_source(s):
    s=restore(s)
    old='''      { // Bounded spin before taking the drain mutex; the existing park/recheck handles late publications.'''
    s=edit(s,old,'      native_gx_batching::Park native_spin_park; // Use the parked publication threshold during spin too.\n      {std::lock_guard<std::mutex> lock(nwii::runtime::g_gx_drain_mutex);nwii::runtime::NativeArmGxParkLocked();}\n'+old)
    old='native_handoff::budget("NWII_HANDOFF_SPIN_US",250)'
    s=edit(s,old,'native_handoff::budget("NWII_HANDOFF_SPIN_US",50)')
    # Keep both Park objects safe: Park is a boolean, so remove the nested one.
    s=edit(s,'      native_gx_batching::Park native_park;','      // native_spin_park remains active through the wait.')
    old='    bool quit = false;'
    s=edit(s,old,old+'\n    bool native_force_present=true;uint64_t native_shown_generation=0;uint32_t native_shown_addr=0;int native_shown_w=0,native_shown_h=0;')
    old='          if (e.type == SDL_QUIT) quit = true;'
    s=edit(s,old,'          if(e.type==SDL_WINDOWEVENT || e.type==SDL_KEYDOWN)native_force_present=true;\n'+old)
    old='        const bool have_present=nwii::runtime::gx::NativeGetXfbFrame(xfb_addr,&present_fbo,&present_w,&present_h);'
    new=old+'''
        const uint64_t native_generation=have_present?nwii::runtime::gx::NativeXfbGeneration(xfb_addr):0;
        bool native_thumbnail=false;{std::lock_guard<std::mutex> lock(native_ss::thumb.mutex);native_thumbnail=native_ss::thumb.want;}
        static const bool native_repeat=std::getenv("NWII_REPEAT_PRESENT")!=nullptr;
        const bool native_do_present=native_repeat || native_force_present || !have_present ||
          native_generation!=native_shown_generation || xfb_addr!=native_shown_addr || draw_w!=native_shown_w || draw_h!=native_shown_h ||
          native_hud::on.load(std::memory_order_relaxed) || native_thumbnail || std::getenv("NWII_SCREENSHOT");'''
    s=edit(s,old,new)
    s=edit(s,'        if(!present_offscreen) {','        if(!present_offscreen && native_do_present) {')
    old='        if(std::getenv("NWII_OFFSCREEN")==nullptr) { native_trace::Scope trace_present("host.present"); SDL_GL_SwapWindow(window); }'
    s=edit(s,old,'        if(!present_offscreen && native_do_present) {native_trace::Scope trace_present("host.present"); SDL_GL_SwapWindow(window);\n          native_shown_generation=native_generation;native_shown_addr=xfb_addr;native_shown_w=draw_w;native_shown_h=draw_h;native_force_present=false;}')
    old='          ++pm_presents; pm_new+=fresh?1:0; native_hud::present(fresh);'
    return edit(s,old,'          if(native_do_present){++pm_presents; pm_new+=fresh?1:0; native_hud::present(fresh);}')
def ios(s):
    s=restore(s)
    # Reduce call overhead without changing event predicates or poll credit.
    # The event-mask configuration remains an independent opt-in A/B.
    for old in ('static inline bool native_process_callbacks_poll(CPUContext&);',
                'static inline bool native_process_callbacks_poll(CPUContext &ctx) {'):
        if old in s:s=edit(s,old,old.replace('static inline','static __attribute__((always_inline)) inline'))
    return s
def apply(port,undo=False):
    rt=port/'build/nwii-full-source/nWiiRuntime'
    changes=[]
    for rel,fn in [('src/hle/gx.cpp',gx),('src/hle/gx/fifo_parser.cpp',parser),('src/hle/gx/renderer_gl.cpp',renderer),
                   ('src/hle/texture_cache_gl.cpp',texture),('include/runtime/texture_cache_gl.h',texture_header),('src/core/main.cpp',main_source),('src/hle/ios.cpp',ios)]:
        path=rt/rel;old=path.read_text();new=restore(old) if undo else fn(old);changes.append((path,old,new))
    if not undo:
        shutil.copy2(ROOT/'runtime-overlays/native_tlut_epoch.h',rt/'include/runtime/native_tlut_epoch.h')
    for path,old,new in changes:
        if old!=new:path.write_text(new)
    print('Audit speedups '+('restored' if undo else 'staged')+'.')
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,default=ROOT);ap.add_argument('--restore',action='store_true');a=ap.parse_args();apply(a.port,a.restore)
