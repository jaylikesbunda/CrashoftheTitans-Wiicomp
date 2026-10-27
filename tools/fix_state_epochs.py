"""Cache shader keys and refine XF invalidation from exact renderer dependencies."""
from pathlib import Path
import argparse, base64, re, hashlib

MARK='NATIVE_STATE_EPOCHS_V1'
BLOCK=re.compile(r'/\* '+MARK+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARK+r' \*/',re.S)
DECL='''// NATIVE_STATE_EPOCHS_DECL
namespace native_state_epochs {
struct Stats {uint64_t shader_requests=0,shader_skips=0,xf_requests=0,xf_unchanged=0;};
inline Stats counts{},baseline{};
inline Stats GetStats(){return {counts.shader_requests-baseline.shader_requests,counts.shader_skips-baseline.shader_skips,counts.xf_requests-baseline.xf_requests,counts.xf_unchanged-baseline.xf_unchanged};}
inline void ResetBaseline(){baseline=counts;}
inline bool Enabled(){static const bool enabled=std::getenv("NWII_NO_STATE_EPOCHS")==nullptr;return enabled;}
inline bool Debug(){static const bool debug=std::getenv("NWII_STATE_EPOCH_DEBUG")!=nullptr;return debug;}
static uint64_t epoch=0;
static void Invalidate(){++epoch;}
// Union of GXState::GetShaderHash and NativeTevShaderHash inputs.
static bool ShaderBp(unsigned reg){return reg==0 || (reg>=0x40 && reg<=0x43) ||
 (reg>=0xc0 && reg<=0xdf) || (reg>=6 && reg<=0x2f) ||
 (reg>=0xe9 && reg<=0xf3) || (reg>=0xf6 && reg<=0xfd);}
static bool ShaderXf(unsigned reg){return reg==0x103f || (reg>=0x1040 && reg<=0x1047);}
}
'''
CACHE='''
static uint64_t NativeTevShaderHash(const GXState &state,uint8_t primitive){
 using namespace native_state_epochs;
 ++counts.shader_requests;
 const int native_debug=native_hud::ind_debug.load(std::memory_order_relaxed);
 if(!Enabled())return NativeTevShaderHashOriginal(state,primitive,native_debug);
 struct Key {uint64_t epoch;uint32_t cp50,cp60;int debug;uint8_t primitive,formats[16];};
 // Explicit scalar/array comparison avoids padding and catches parser-owned CP
 // and texture formats; all other key words have renderer-local invalidations.
 Key key{};key.epoch=epoch;key.cp50=state.cp[0x50];key.cp60=state.cp[0x60];
 key.debug=native_debug;key.primitive=primitive;
 for(unsigned i=0;i<8;++i){key.formats[2*i]=state.texStages[i].format;key.formats[2*i+1]=state.texStages[i].tlut_format;}
 static Key previous{};static uint64_t hash=0;static bool have=false;
 if(have && previous.epoch==key.epoch && previous.cp50==key.cp50 && previous.cp60==key.cp60 &&
    previous.debug==key.debug && previous.primitive==key.primitive && !std::memcmp(previous.formats,key.formats,16)){
   ++counts.shader_skips;
   if(Debug() && hash!=NativeTevShaderHashOriginal(state,primitive,native_debug))std::abort();
   return hash;
 }
 hash=NativeTevShaderHashOriginal(state,primitive,native_debug);previous=key;have=true;return hash;
}
// Read-set of NativeGpuXfBlock construction, excluding snapshot-owned banks.
static bool NativeXfBlockChanged(const GXCommand& cmd){
 using namespace native_state_epochs;
 ++counts.xf_requests;
 if(!Enabled())return true;
 for(size_t i=0;i<cmd.payload.size();++i){
  const size_t a=size_t(cmd.reg)+i;const void* value=nullptr;
  if(a<=0xff)value=&g_state.posMatrices[a];
  else if(a>=0x500 && a<0x600)value=&g_state.postMatrices[a-0x500];
  else if(a==0x1012 || (a>=0x1040 && a<=0x1047) || (a>=0x1050 && a<=0x1057))value=&g_state.xf[a-0x1000];
  if(value && std::memcmp(value,&cmd.payload[i],4))return true;
 }
 ++counts.xf_unchanged;return false;
}
'''

def method(s,signature):
    a=s.index(signature);p=s.index('{',a);z=p+1;depth=1
    while depth:
        depth+=(s[z]=='{')-(s[z]=='}');z+=1
    return a,z,s[a:z]

def restore(s):
    s=BLOCK.sub(lambda m:base64.b64decode(m[1]).decode(),s)
    if MARK in s or 'NATIVE_STATE_EPOCHS_DECL' in s:raise ValueError('Partial state epochs stage')
    return s

def replace(s,old,new):
    if s.count(old)!=1:raise ValueError(('State epoch anchor',old[:100],s.count(old)))
    return s.replace(old,'/* '+MARK+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+MARK+' */')

def apply(s):
    original=s;s=restore(s)
    # Fail closed if either read-set changes in future stages.
    _,_,body=method(s,'static uint64_t NativeTevShaderHash(')
    expected='''static uint64_t NativeTevShaderHash(const GXState &state, uint8_t primitive) {
  uint64_t hash = state.GetShaderHash(primitive);
  if (const int ind_debug = native_hud::ind_debug.load(std::memory_order_relaxed)) hash = (hash ^ (0x9e3779b97f4a7c15ull * uint64_t(ind_debug))) * 1099511628211ull; // NATIVE_IND_DEBUG_V1
  for(unsigned t=0;t<8;++t)
    hash=(hash ^ ((state.xf[0x40+t]>>4)&7))*1099511628211ull;
  for(unsigned reg=0xee;reg<=0xf2;++reg)
    for(unsigned shift=0;shift<32;shift+=8)
      hash=(hash ^ ((state.bp[reg]>>shift)&255))*1099511628211ull;
  for(unsigned reg=6;reg<=0x27;++reg)
    for(unsigned shift=0;shift<32;shift+=8)
      hash=(hash ^ ((state.bp[reg]>>shift)&255))*1099511628211ull;
  for (unsigned reg = 0xf6; reg <= 0xfd; ++reg)
    for (unsigned shift = 0; shift < 32; shift += 8)
      hash = (hash ^ ((state.bp[reg] >> shift) & 0xff)) * 1099511628211ull;
  return hash;
}'''
    canonical=expected.replace('state.GetShaderHash(primitive);',
        'state.GetShaderHash(0x90); // vertex/fragment source is topology-independent')
    if body not in (expected,canonical):raise ValueError('Shader hash read-set changed')
    _,_,xfbody=method(s,'static void NativeUploadGpuXf(){')
    for line in ('std::memcpy(block.tex,g_state.posMatrices,1024);std::memcpy(block.post,g_state.postMatrices,1024);',
                 'block.info[t][0]=int(g_state.xf[0x40+t]);block.info[t][1]=int(g_state.xf[0x50+t]);',
                 'block.control[1]=g_state.xf[0x12]&1;'):
        if line not in xfbody:raise ValueError('GPU XF read-set changed')
    chunk=xfbody[xfbody.index(' NativeGpuXfBlock block{};'):xfbody.index(' static GLint alignment=0;')]
    if hashlib.sha256(chunk.encode()).hexdigest()!=XF_BLOCK_SHA:raise ValueError('GPU XF construction changed')
    s=replace(s,'static uint32_t NativeBpRegisterValue(uint8_t reg, uint32_t val) {',DECL+'static uint32_t NativeBpRegisterValue(uint8_t reg, uint32_t val) {')
    s=replace(s,'  s_native_bp_shadow[reg] = resolved & 0xffffff;',
        '  if(native_state_epochs::ShaderBp(reg) && s_native_bp_shadow[reg]!=(resolved&0xffffff))native_state_epochs::Invalidate();\n  s_native_bp_shadow[reg] = resolved & 0xffffff;')
    s=replace(s,'static void NativeRestoreBpSnapshot() {','static void NativeRestoreBpSnapshot() {\n  native_state_epochs::Invalidate(); // Parser/replay owns CP and decoded texture state between drains.')
    s=replace(s,body,body.replace('NativeTevShaderHash(','NativeTevShaderHashOriginal(',1).replace('uint8_t primitive)', 'uint8_t primitive,int native_debug)').replace('native_hud::ind_debug.load(std::memory_order_relaxed)','native_debug')+CACHE)
    s=replace(s,'   native_perf_census::RenderAdd(native_perf_census::XfCached);return;',
        '   if(native_state_epochs::Debug()){\n'+chunk+
        '     if(std::memcmp(&previous,&block,sizeof(block)))std::abort();\n   }\n'
        '   native_perf_census::RenderAdd(native_perf_census::XfCached);return;')
    s=replace(s,'        ++s_native_gpu_xf_generation; // Any XF command invalidates all dependent words.',
        '        if(NativeXfBlockChanged(cmd))++s_native_gpu_xf_generation; // Exact block-input change only.')
    s=replace(s,'          int current_addr = addr + (int)i;',
        '          int current_addr = addr + (int)i;\n          if(native_state_epochs::ShaderXf(unsigned(current_addr)) &&\n             std::memcmp(&g_state.xf[current_addr-0x1000],&cmd.payload[i],4))native_state_epochs::Invalidate();')
    if MARK in original and s!=original:raise ValueError('Noncanonical state epoch stage')
    return s

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--restore',action='store_true');args=ap.parse_args()
    rt=args.port/'build/nwii-full-source/nWiiRuntime'
    if not args.restore:
        _,_,body=method((rt/'include/runtime/gx_state.h').read_text(),'uint64_t GetShaderHash(')
        if hashlib.sha256(body.encode()).hexdigest()!=GET_HASH_SHA:raise ValueError('GXState shader read-set changed')
    path=rt/'src/hle/gx/renderer_gl.cpp';before=path.read_text();after=restore(before) if args.restore else apply(before)
    if before!=after:path.write_text(after)
    print('State epochs restored.' if args.restore else 'Shader keys and exact XF input changes cached.')

XF_BLOCK_SHA='05372f91f6ef92278064e14ac22629f04f113d36cc06be27a1c42c6ad60ada99'
GET_HASH_SHA='a9ff3351d13f5c00046bf52bae35a045bc019fd916839e2ce73c70a35b262390'
if __name__=='__main__':main()
