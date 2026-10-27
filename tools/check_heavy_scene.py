"""Differential palette decoding and production CP/BP command handling, without a game."""
from pathlib import Path
import argparse,os,shutil,subprocess,tempfile
import fix_heavy_scene as stage
from port_paths import winlibs_bin
PORT=Path(__file__).resolve().parents[1]

def method(text,signature):
    a=text.index(signature);b=text.index('{',a);depth=1;n=b+1
    while depth:
        depth+=(text[n]=='{')-(text[n]=='}');n+=1
    return text[a:n]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--runtime',type=Path,required=True);a=ap.parse_args()
    original={};patched={}
    for name,fn in [('gx/fifo_parser.cpp',stage.parser),('gx/renderer_gl.cpp',stage.renderer),('texture_cache_gl.cpp',stage.texture)]:
        raw=(a.runtime/'src/hle'/name).read_text();plain=stage.restore(raw);new=fn(plain)
        assert stage.restore(new)==plain and fn(new)==new,name
        original[name]=plain;patched[name]=new
    texture=original['texture_cache_gl.cpp']
    helpers='\n'.join(method(texture,s) for s in ['static uint32_t get_tile_address(',
        'static inline uint8_t c5to8(','static inline uint8_t c6to8(','static inline uint8_t c4to8(',
        'static inline uint8_t c3to8(','static inline Color unpack_rgb565(','static inline Color unpack_rgb5a3('])
    oracle=method(texture,'void TextureCache::decode_paletted(')
    parser=patched['gx/fifo_parser.cpp'];renderer=patched['gx/renderer_gl.cpp']
    cp=method(parser,'static inline void EmitCp(')
    replay=method(parser,'if(depth==0 && e0.kind==uint8_t(GXCommandType::CPRegister))')
    cache_save=parser[parser.index('        s_dl_cache[key] = {sh, ch, local};'):parser.index('        native_gx_work::Add(native_gx_work::CommandsMoved,local.entries.size());')]
    bp=method(renderer,'static bool NativeRedundantBp(uint8_t kind,')
    resolve=method(renderer,'static uint32_t NativeBpRegisterValue(')
    init_a=renderer.index('        const bool duplicate=native_applied_bp[')
    init_b=renderer.index('        if(duplicate)continue;',init_a)+len('        if(duplicate)continue;')
    initialization=renderer[init_a:init_b]
    code=r'''
#include "runtime/gx/command_stream.h"
#include "runtime/native_gx_work_metrics.h"
#include "runtime/native_palette_decode.h"
#include "runtime/native_batch_boundaries.h"
#include <cassert>
#include <random>
#include <map>
#include <cstdio>
#include <chrono>
using nwii::runtime::gx::CommandStream;using nwii::runtime::gx::StreamEntry;
using nwii::runtime::gx::GXCommand;
using nwii::runtime::gx::GXCommandType;using nwii::runtime::gx::NativeDirectFifoEnabled;
struct Color{uint8_t r,g,b,a;};
#define MAGENTA Color{255,0,255,255}
struct CPUContext{struct{std::vector<uint8_t> data;uint8_t read8(uint32_t p){return data.at(p);}
 uint16_t read16(uint32_t p){return (unsigned(read8(p))<<8)|read8(p+1);}}mmu;};
namespace gx{struct TexStage{unsigned width,height,base_addr=0,format,tlut_offset,tlut_format;};}
struct{uint8_t tlutMem[32768];}g_state;
enum class TextureFormat{C4=8,C8=9,C14X2=10};
struct TextureCache{void decode_paletted(CPUContext&,const gx::TexStage&,Color*);};
uint32_t s_native_bp_shadow[256]{},s_native_bp_mask=0xffffff;
namespace native_state_epochs{inline bool ShaderBp(unsigned){return false;}inline void Invalidate(){}}
unsigned cp_state[256]{};void ParseCP(uint8_t reg,uint32_t v){cp_state[reg]=v;}
'''+helpers+'\n'+oracle+'\n'+cp+'\n'+bp+'\n'+resolve+r'''
int main(){
 std::mt19937 r(923713);CPUContext ctx;TextureCache reference;uint64_t pixels=0;
 for(unsigned trial=0;trial<2400;++trial){
  unsigned f=8+trial%3,w=1+r()%97,h=1+r()%83,tw=f==10?4:8,th=f==8?8:4;
  size_t bytes=((w+tw-1)/tw)*((h+th-1)/th)*32;
  ctx.mmu.data.resize(bytes);for(auto&v:ctx.mmu.data)v=r();for(auto&v:g_state.tlutMem)v=r();
  gx::TexStage s{w,h,0,f,trial%7?unsigned(r()%32768):32768u,unsigned(r()%4)};
  std::vector<Color> expected(w*h),actual(w*h+1,Color{13,17,19,23});
  reference.decode_paletted(ctx,s,expected.data());
  const size_t off=std::min(size_t(s.tlut_offset),sizeof(g_state.tlutMem));
  assert(native_palette_decode::Decode(ctx.mmu.data.data(),bytes,w,h,f,g_state.tlutMem+off,
      sizeof(g_state.tlutMem)-off,s.tlut_format,actual.data()));
  assert(!std::memcmp(actual.data(),expected.data(),w*h*sizeof(Color)));
  assert(actual.back().r==13 && actual.back().a==23);pixels+=w*h;
  assert(!native_palette_decode::Decode(ctx.mmu.data.data(),bytes-1,w,h,f,nullptr,0,0,actual.data()));
 }
 // No setup-only command may absorb a completion, palette load or texture validation boundary.
 for(uint8_t reg:{0x45,0x47,0x48,0x52,0x65,0x80,0x88,0x94,0x98})
  assert(!NativeRedundantBp(uint8_t(GXCommandType::BPRegister),reg,0));
 assert(!NativeRedundantBp(uint8_t(GXCommandType::CPRegister),0xfe,0));
 for(uint8_t reg:{0xfe,0x64,0x49,0x4a,0x4b,0x4d,0x4f,0x50})
  assert(NativeRedundantBp(uint8_t(GXCommandType::BPRegister),reg,0x123456));
 // The one-write mask is consumed even when an ordinary BP update is redundant.
 s_native_bp_shadow[0x40]=0xabcdef;s_native_bp_mask=0xff;
 assert(NativeRedundantBp(uint8_t(GXCommandType::BPRegister),0x40,0xef));
 assert(NativeBpRegisterValue(0x40,0xef)==0xabcdef && s_native_bp_mask==0xffffff);
 assert(!NativeRedundantBp(uint8_t(GXCommandType::BPRegister),0x40,0xee));
 // First zero writes must initialize derived state after startup/state load.
 bool native_applied_bp[256]{};unsigned initialized=0;
 const uint8_t native_kind=uint8_t(GXCommandType::BPRegister),native_reg=0x40;
 s_native_bp_shadow[0x40]=0;s_native_bp_mask=0xffffff;StreamEntry e{};
 for(unsigned i=0;i<2;++i){
'''+initialization+r'''
  ++initialized;
 }
 assert(initialized==1 && native_applied_bp[0x40]);
 native_gx_work::capture=true;native_gx_work::ConstructionBatch stats;
 CommandStream output,local;
 for(unsigned i=0;i<256;++i){EmitCp(output,uint8_t(i),i+41,stats,false);EmitCp(local,uint8_t(i),i+41,stats,true);}
 assert(output.empty() && local.entries.size()==256 && stats.values[1]==512);
 struct Cached{unsigned sh,ch;CommandStream cmds;};std::map<unsigned,Cached>s_dl_cache;
 unsigned key=1,sh=2,ch=3,depth=0;
'''+cache_save+r'''
 assert(local.empty() && s_dl_cache[1].cmds.entries.size()==256);
 for(const StreamEntry& e0:s_dl_cache[1].cmds.entries){
'''+replay+r'''
  assert(false);
 }
 for(unsigned i=0;i<256;++i)assert(cp_state[i]==i+41);
 // Nested lists retain the CP records needed by a containing cached list.
 depth=1;local=s_dl_cache[1].cmds;
'''+cache_save+r'''
 assert(local.entries.size()==256 && s_dl_cache[1].cmds.entries.size()==256);
 gx::TexStage b{64,64,0,9,0,2};ctx.mmu.data.resize(4096);
 std::vector<Color> image(4096);volatile uint64_t checksum=0;
 auto bench=[&](bool fast){auto start=std::chrono::steady_clock::now();
  for(unsigned i=0;i<4096;++i){ctx.mmu.data[i%4096]^=uint8_t(i);
   if(fast)native_palette_decode::Decode(ctx.mmu.data.data(),4096,64,64,9,g_state.tlutMem,32768,2,image.data());
   else reference.decode_paletted(ctx,b,image.data());
   checksum=checksum+image[i%4096].r;
  }return std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();};
 double old_ms=bench(false),new_ms=bench(true);
 std::printf("C8 decode benchmark reference/fast %.2f/%.2f ms (%.2fx); checksum %llu\n",old_ms,new_ms,old_ms/new_ms,(unsigned long long)checksum);
 std::printf("Heavy scene: %llu oracle pixels matched; truncated palettes, partial tiles, output bounds, BP masks/completions and CP replay passed.\n",(unsigned long long)pixels);
}
'''
    with tempfile.TemporaryDirectory(prefix='heavy-scene-') as directory:
        work=Path(directory);cpp=work/'check.cpp';cpp.write_text(code);exe=work/'check.exe'
        (work/'runtime').mkdir()
        for name in ('native_palette_decode.h','native_batch_boundaries.h'):
            shutil.copy2(PORT/'runtime-overlays'/name,work/'runtime'/name)
        compiler=shutil.which('g++') or str(winlibs_bin()/'g++.exe')
        subprocess.run([compiler,'-std=c++20','-O2','-I',str(work),
            '-I',str(a.runtime/'include'),str(cpp),'-o',str(exe)],check=True)
        for mode in ('0','1'):
            env=os.environ.copy();env['NWII_DIRECT_FIFO']=mode;env.pop('NWII_NO_DIRECT_FIFO',None)
            subprocess.run([str(exe)],env=env,check=True,timeout=30)
    print('Heavy-scene staging is reversible and idempotent.')

if __name__=='__main__':main()
