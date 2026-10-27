"""Production-fragment parity for uniform epochs, EFB snapshots and viewport state."""
from pathlib import Path
import argparse,os,subprocess,tempfile,re
import fix_draw_followups as stage
from fix_audit_speedups import method
from port_paths import winlibs_bin
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,required=True);a=ap.parse_args();rt=a.port/'build/nwii-full-source/nWiiRuntime'
    for rel,fn in stage.OWNERS:
        raw=(rt/rel).read_text();plain=stage.restore(raw);patched=fn(plain)
        assert patched==raw and fn(patched)==patched and stage.restore(patched)==plain,rel
    s=(rt/'src/hle/gx/renderer_gl.cpp').read_text();t=(rt/'src/hle/texture_cache_gl.cpp').read_text()
    begin=s.index('  // XF channel state changes every frame');end=s.index('\n}\n',begin)
    uniforms=s[begin:end]
    tev='static uint32_t s_native_tev_banks[2][8]{};\n'+'\n'.join(method(s,anchor) for anchor in ('static void NativeTevRegisterWrite(','static float NativeTevDecode11(','static void NativeTevUniforms('))
    prefix=r'''
#include <array>
#include <vector>
#include <unordered_map>
#include <cstdint>
#include <cstring>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <iostream>
using GLuint=unsigned;using GLint=int;constexpr int GL_FALSE=0;
#include "runtime/native_uniform_epoch.h"
namespace native_gx_work{enum{MaterialBuildsSkipped,LightBuildsSkipped};inline void Add(int){}}
struct {uint32_t xf[256]{},bp[256]{};float lights[8][16]{};}g_state;
struct Shader{GLuint id=1;GLint uMatColor=1,uAmbColor=2,uChanCtrl=3,uLightCol=4,uLightPos=5,uLightDir=6,uLightCosAtt=7,uLightDistAtt=8,uTexMtx[8]{},uTevKColor=20,uTevColor=21;}s_active_shader;
uint64_t digest=14695981039346656037ull;
void record(const void* p,size_t n){auto* b=static_cast<const uint8_t*>(p);for(size_t i=0;i<n;++i)digest=(digest^b[i])*1099511628211ull;}
bool NativeUniformChanged(GLuint prog,GLint loc,const void* p,size_t n){record(&prog,sizeof prog);record(&loc,sizeof loc);record(p,n);return true;}
bool NativeDrawSubmissionReuseEnabled(){return true;}
void glUniform4fv(GLint,int,const float*){}void glUniform1iv(GLint,int,const int*){}void glUniformMatrix4fv(GLint,int,int,const float*){}
'''
    code=prefix+tev+'\nvoid setup(){\n'+uniforms+'\n}\n'+r'''
uint32_t rng=73;uint32_t next(){rng=rng*1664525u+1013904223u;return rng;}
int main(){for(auto& loc:s_active_shader.uTexMtx)loc=-1;
 for(unsigned step=0;step<600;++step){
  if(step%19==0)native_uniform_epoch::Invalidate();
  switch(step%5){
   case 0:{unsigned reg=0xa+next()%4;g_state.xf[reg]=next();native_uniform_epoch::DirtyXf(0x1000+reg,1);break;}
   case 1:{unsigned reg=0xe + next()%4;g_state.xf[reg]=next();native_uniform_epoch::DirtyXf(0x1000+reg,1);break;}
   case 2:{unsigned i=next()%128;g_state.lights[i/16][i%16]=float(int(next()%20000)-10000)/17;native_uniform_epoch::DirtyXf(0x600+i,1);break;}
   case 3:NativeTevRegisterWrite(uint8_t(0xe0+next()%8),next()&0xffffff);break;
   case 4:g_state.xf[0x40]=next();native_uniform_epoch::DirtyXf(0x1040,1);break;
  }
  for(unsigned draw=0;draw<5;++draw){s_active_shader.id=1+draw%3;setup();}
 }
 uint64_t builds=0,skips=0;for(unsigned i=0;i<native_uniform_epoch::Count;++i){builds+=native_uniform_epoch::builds[i];skips+=native_uniform_epoch::skips[i];}
 std::cout<<"UNIFORM_DIGEST "<<digest<<" builds="<<builds<<" skips="<<skips<<"\n";
 if(native_uniform_epoch::enabled)assert(skips>builds);else assert(skips==0);
}
'''
    register=method(t,'void TextureCache::register_efb_texture(')+'\n'+method(t,'bool TextureCache::efb_guest_unchanged(')
    snapshots=r'''
#include <vector>
#include <unordered_map>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <cassert>
#include <algorithm>
#include <iostream>
using GLuint=unsigned;
template<size_t N>struct Bank:std::vector<uint8_t>{Bank():std::vector<uint8_t>(N){}};
struct CPUContext{struct MMU{Bank<4096> mem1;Bank<8192> mem2;const uint8_t* get_ptr(unsigned a){if(a<mem1.size())return mem1.data()+a;if(a>=0x10000000 && a-0x10000000<mem2.size())return mem2.data()+a-0x10000000;return nullptr;}}mmu;};
CPUContext* g_ctx_ptr;namespace gx{struct{unsigned efbW=640,efbH=528;}g_state;}
unsigned footprint=512,g_native_efb_copy_value=0,g_native_efb_copy_stride=0,hashes=0;
unsigned NativeEfbCopyBytes(unsigned,unsigned,unsigned,unsigned){return footprint;}
uint64_t generation=1,newest=1;bool tracking=true;
uint64_t native_ram_generation(){return generation;}
uint64_t native_ram_newest_write(const uint8_t*,size_t,size_t,size_t){return tracking?newest:UINT64_MAX;}
unsigned NativeGuestHash(const uint8_t*,unsigned){return ++hashes;}
struct TextureCache{struct EfbGuest{unsigned hash=0,bytes=0;std::vector<uint8_t> source;uint64_t verified_gen=0;uint32_t physical_base=UINT32_MAX;};
 std::unordered_map<unsigned,GLuint> efb_textures;std::unordered_map<unsigned,unsigned> efb_extent;std::unordered_map<unsigned,EfbGuest> efb_guest;
 void register_efb_texture(unsigned,GLuint);bool efb_guest_unchanged(CPUContext&,uint32_t);};
'''+register+r'''
int main(){CPUContext ctx;g_ctx_ptr=&ctx;TextureCache cache;cache.register_efb_texture(16,1);assert(hashes==1);
 cache.register_efb_texture(16,2);assert(hashes==1 && cache.efb_textures[16]==2);
 ctx.mmu.mem1[16+511]=7;newest=++generation;cache.register_efb_texture(16,3);assert(hashes==2 && cache.efb_guest[16].source[511]==7);
 footprint=128;cache.register_efb_texture(16,4);assert(hashes==3 && cache.efb_guest[16].source.size()==128);
 ctx.mmu.mem2[16]=5;cache.register_efb_texture(0x10000010,5);assert(hashes==4 && cache.efb_guest[16].source[0]==5);
 assert(!cache.efb_guest_unchanged(ctx,16));assert(cache.efb_guest_unchanged(ctx,0x10000010));
 tracking=false;ctx.mmu.mem1[16]=9;cache.register_efb_texture(16,6);assert(hashes==5 && cache.efb_guest[16].source[0]==9);
 std::cout<<"EFB clean reuse, dirty copy, footprint/bank changes and unavailable-tracker fallback passed.\n";}
'''
    viewport=r'''
#include <cassert>
#include <limits>
#include <iostream>
#include "runtime/native_viewport_shadow.h"
unsigned views=0,depths=0;
void glViewport(GLint,GLint,GLsizei,GLsizei){++views;}void glDepthRange(GLdouble,GLdouble){++depths;}
int main(){NativeViewport(0,0,640,528);NativeViewport(0,0,640,528);assert(views==(native_viewport::enabled?1:2));
 NativeViewport(0,0,1920,1080);NativeViewport(0,0,640,528);assert(views==(native_viewport::enabled?3:4));
 native_viewport::Invalidate();NativeViewport(0,0,640,528);const auto before=views;
 NativeViewport(0,0,-1,528);NativeViewport(0,0,640,528);assert(views==before+2);
 NativeDepthRange(0,1);NativeDepthRange(0,1);assert(depths==(native_viewport::enabled?1:2));
 NativeDepthRange(1,0);native_viewport::Invalidate();NativeDepthRange(1,0);assert(depths==(native_viewport::enabled?3:4));
 const double nan=std::numeric_limits<double>::quiet_NaN();const auto d=depths;NativeDepthRange(nan,1);NativeDepthRange(nan,1);assert(depths==d+2);
 std::cout<<"Viewport/HUD restore, context invalidation, invalid size and reversed/NaN depth checks passed.\n";}
'''
    with tempfile.TemporaryDirectory(prefix='draw-followups-') as tmp:
        tmp=Path(tmp);(tmp/'glad').mkdir();(tmp/'glad/glad.h').write_text('#pragma once\nusing GLint=int;using GLsizei=int;using GLdouble=double;void glViewport(GLint,GLint,GLsizei,GLsizei);void glDepthRange(GLdouble,GLdouble);\n')
        env=os.environ.copy();env['PATH']=str(winlibs_bin())+os.pathsep+env['PATH']
        outputs=[]
        for name,source in [('uniforms',code),('snapshots',snapshots),('viewport',viewport)]:
            cpp=tmp/(name+'.cpp');exe=tmp/(name+'.exe');cpp.write_text(source)
            result=subprocess.run([str(winlibs_bin()/'g++.exe'),'-std=c++20','-O2','-I',str(tmp),'-I',str(rt/'include'),str(cpp),'-o',str(exe)],env=env,capture_output=True,text=True)
            assert result.returncode==0,result.stdout+result.stderr
            for off in (False,True) if name!='snapshots' else (False,):
                variable='NWII_NO_UNIFORM_CACHE_EPOCH' if name=='uniforms' else 'NWII_NO_VIEWPORT_SHADOW'
                if off:env[variable]='1'
                else:env.pop(variable,None)
                result=subprocess.run([str(exe)],env=env,capture_output=True,text=True,timeout=30)
                assert result.returncode==0,result.stdout+result.stderr
                print(result.stdout.strip())
                if name=='uniforms':outputs.append(re.search(r'UNIFORM_DIGEST (\d+)',result.stdout)[1])
        assert outputs[0]==outputs[1],outputs
    print('Draw followups: exact uniform payloads across programs, snapshot reuse, viewport state and reversible/idempotent staging passed.')
if __name__=='__main__':main()
