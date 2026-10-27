// Fixtures for NATIVE_CACHE_BOUNDS_V1. check_cache_bounds.py pastes the patched production functions
// into the marked sections below (textures, shaders/uniforms) and compiles this file; no GL context is used.
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <random>
#include <set>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

typedef unsigned GLuint;
typedef int GLint;
#include "runtime/native_cache_bounds.h"

#define REQUIRE(cond) do{if(!(cond)){std::fprintf(stderr,"FAIL %s:%d %s\n",__FILE__,__LINE__,#cond);std::exit(1);}}while(0)

// ------------------------------------------------------------------ texture cache stubs
static unsigned g_texture_deleted=0;
inline void NativeTextureDelete(int n,GLuint*){g_texture_deleted+=unsigned(n);}
struct TextureKey {
 uint32_t addr,width,height,format,tlut_offset,tlut_format,data_hash;
 bool operator==(const TextureKey& o) const {return !std::memcmp(this,&o,sizeof *this);}
};
struct TextureKeyHash {size_t operator()(const TextureKey& k) const {return size_t(k.addr)*2654435761u^k.data_hash;}};
#define CACHE_CLASS(NAME) class NAME { public: \
 struct Entry {GLuint tex;uint32_t bytes;uint64_t stamp;}; \
 std::unordered_map<TextureKey,Entry,TextureKeyHash> cache; std::unordered_map<TextureKey,TextureKey,TextureKeyHash> hot_keys; \
 uint64_t tick=0;size_t total_bytes=0;std::unordered_set<GLuint> mip_ready; \
 uint64_t evict_retry_tick=0;bool evict_pending=false;std::vector<std::pair<uint64_t,TextureKey>> evict_scratch; \
 void evict_old();void prune_hot_keys();void note_cache_size(); \
 void insert(const TextureKey& key,uint32_t bytes,uint64_t stamp){cache[key]=Entry{GLuint(cache.size()+1),bytes,stamp};total_bytes+=bytes;} };
CACHE_CLASS(RefCache)
CACHE_CLASS(NewCache)
//@@TEXTURE_FUNCTIONS@@

static TextureKey key_of(uint32_t i){return TextureKey{0x80000000u+i*64u,32,32,6,0,0,i*2246822519u};}

static void test_evict_oldest_core(){
 std::mt19937_64 rng(7);
 for(unsigned trial=0;trial<300;++trial){
  const size_t n=1+rng()%5000;std::vector<std::pair<uint64_t,uint32_t>> items;
  for(uint32_t i=0;i<n;++i)items.emplace_back(rng()%(trial%3?n:50)+1,i); // trial%3==0 forces many equal stamps
  const size_t need=rng()%(n+1);
  auto full=items;std::sort(full.begin(),full.end(),[](auto&a,auto&b){return a.first<b.first;});
  std::vector<uint64_t> expect_stamps;for(size_t i=0;i<need;++i)expect_stamps.push_back(full[i].first);
  auto work=items;size_t done=0;std::vector<uint64_t> got_stamps;std::unordered_set<uint32_t> seen;
  const size_t evicted=native_cache_bounds::EvictOldest(work,rng()%300,[&]{return done>=need;},[&](uint32_t id){REQUIRE(seen.insert(id).second);++done;
   got_stamps.push_back(items[id].first);});
  REQUIRE(evicted==need);
  std::sort(got_stamps.begin(),got_stamps.end());REQUIRE(got_stamps==expect_stamps); // the `need` oldest stamps, ties aside
 }
 std::puts("CORE_EVICT_OLDEST ok");
}

static void fill_pair(RefCache& ref,NewCache& next,std::mt19937_64& rng,size_t n,uint64_t tick,uint32_t byte_lo,uint32_t byte_hi,bool unique){
 std::vector<uint64_t> stamps(n);for(size_t i=0;i<n;++i)stamps[i]=i+1;std::shuffle(stamps.begin(),stamps.end(),rng);
 ref.tick=next.tick=tick;
 for(uint32_t i=0;i<n;++i){const uint32_t bytes=byte_lo+uint32_t(rng()%(byte_hi-byte_lo+1));const uint64_t stamp=unique?stamps[i]:rng()%(tick+1);
  ref.insert(key_of(i),bytes,stamp);next.insert(key_of(i),bytes,stamp);}
 for(uint32_t i=0;i<n;++i)if(rng()&1){const TextureKey meta{uint32_t(0x90000000u+i),64,64,6,0,0,0};ref.hot_keys[meta]=key_of(i);next.hot_keys[meta]=key_of(i);}
 for(uint32_t i=0;i<20;++i){const TextureKey meta{0xA0000000u+i,8,8,6,0,0,0};const TextureKey gone=key_of(uint32_t(n)+100+i); // dangling hot key
  ref.hot_keys[meta]=gone;next.hot_keys[meta]=gone;}
}

static void test_texture_equivalence(){
 std::mt19937_64 rng(11);unsigned trials=0,evicting=0;
 for(unsigned trial=0;trial<160;++trial){
  const bool bytes_case=trial%2;const size_t n=bytes_case?4300+rng()%1700:4300+rng()%5000;
  RefCache ref;NewCache next;
  fill_pair(ref,next,rng,n,n+rng()%100,bytes_case?90000:256,bytes_case?200000:4096,true);
  std::set<uint32_t> had;for(uint32_t i=0;i<n;++i)if(next.hot_keys.count(TextureKey{uint32_t(0x90000000u+i),64,64,6,0,0,0}))had.insert(i);
  g_texture_deleted=0;ref.evict_old();const unsigned ref_deleted=g_texture_deleted;
  const bool hard=next.cache.size()>native_cache_bounds::kTextureHardCount||next.total_bytes>native_cache_bounds::kTextureHardBytes;
  g_texture_deleted=0;next.evict_old();const unsigned new_deleted=g_texture_deleted;
  if(!hard && ref_deleted>native_cache_bounds::kTextureEvictPerPass){ // the budget binds: one pass takes the oldest budget, the rest stays pending
   REQUIRE(new_deleted==native_cache_bounds::kTextureEvictPerPass);REQUIRE(next.evict_pending);REQUIRE(next.cache.size()==n-new_deleted);
  } else {
   REQUIRE(ref_deleted==new_deleted);REQUIRE(ref.total_bytes==next.total_bytes);REQUIRE(ref.cache.size()==next.cache.size());
   for(const auto& pair:ref.cache)REQUIRE(next.cache.count(pair.first));
   REQUIRE(!next.evict_pending);
  }
  if(new_deleted){
   for(const auto& pair:next.hot_keys)REQUIRE(next.cache.count(pair.second));     // never points at an evicted entry
   REQUIRE(ref.hot_keys.empty());                                                 // the old pass dropped them all
   for(uint32_t i=0;i<n;++i){const bool alive=next.cache.count(key_of(i))!=0;
    REQUIRE(next.hot_keys.count(TextureKey{uint32_t(0x90000000u+i),64,64,6,0,0,0})==(alive&&had.count(i)?1u:0u));} // survivors keep their hot key
  }
  ++trials;evicting+=ref_deleted?1:0;
 }
 REQUIRE(evicting>100);
 std::printf("TEXTURE_EQUIVALENCE %u/%u (evicting %u)\n",trials,trials,evicting);
}

static void test_texture_amortised(){
 std::mt19937_64 rng(13);NewCache cache;cache.tick=1;size_t max_size=0;uint64_t before=native_cache_stats::Get(native_cache_stats::c.texture_eviction_passes);
 const uint32_t misses=60000;
 size_t max_drop=0;
 for(uint32_t i=0;i<misses;++i){
  ++cache.tick;const TextureKey key=key_of(i);cache.insert(key,1024,cache.tick);
  cache.hot_keys[TextureKey{i,1,1,6,0,0,0}]=key;
  if(cache.cache.size()>6000 || cache.evict_pending){const size_t before_pass=cache.cache.size();cache.evict_old();max_drop=std::max(max_drop,before_pass-cache.cache.size());}
  max_size=std::max(max_size,cache.cache.size());
  cache.note_cache_size();
 }
 const uint64_t passes=native_cache_stats::Get(native_cache_stats::c.texture_eviction_passes)-before;
 REQUIRE(max_size<=6001);
 REQUIRE(max_drop<=native_cache_bounds::kTextureEvictPerPass);       // no single pass removes more than the budget
 REQUIRE(passes<=misses/200);                                         // a sweep is spread over several misses, never one per miss
 REQUIRE(cache.hot_keys.size()<=cache.cache.size()*2+native_cache_bounds::kTextureHotSlack);
 REQUIRE(native_cache_stats::Get(native_cache_stats::c.texture_entries)==cache.cache.size());
 std::printf("TEXTURE_AMORTISED passes=%llu misses=%u max_size=%zu hot_keys=%zu\n",(unsigned long long)passes,misses,max_size,cache.hot_keys.size());
}

static void test_texture_blocked_backoff(){
 NewCache cache;cache.tick=1000;
 for(uint32_t i=0;i<7000;++i)cache.insert(key_of(i),512,1000-(i%8)); // everything is inside the in-use window
 const uint64_t passes=native_cache_stats::Get(native_cache_stats::c.texture_eviction_passes);
 cache.evict_old();REQUIRE(cache.cache.size()==7000);REQUIRE(cache.evict_retry_tick==1064);
 const uint64_t first=native_cache_stats::Get(native_cache_stats::c.texture_eviction_passes);REQUIRE(first==passes+1);
 for(unsigned step=1;step<64;++step){cache.tick=1000+step;cache.evict_old();}
 REQUIRE(native_cache_stats::Get(native_cache_stats::c.texture_eviction_passes)==first); // no rescans while the window is blocked
 cache.tick=1064+1000;cache.evict_old();                                                   // once entries age out the pass runs and takes one budget
 REQUIRE(native_cache_stats::Get(native_cache_stats::c.texture_eviction_passes)==first+1);REQUIRE(cache.cache.size()==7000-native_cache_bounds::kTextureEvictPerPass);
 REQUIRE(cache.evict_pending);REQUIRE(cache.evict_retry_tick==0);
 while(cache.evict_pending)cache.evict_old();                                              // the following misses carry on to the low-water mark
 REQUIRE(cache.cache.size()<=4200);REQUIRE(cache.evict_retry_tick==0);
 std::puts("TEXTURE_BLOCKED_BACKOFF ok");
}

static void test_texture_hard_limit(){
 NewCache over_hard;over_hard.tick=100000;                                    // past the hard count: no budget, one full pass
 for(uint32_t i=0;i<9000;++i)over_hard.insert(key_of(i),512,1);
 over_hard.evict_old();REQUIRE(over_hard.cache.size()<=4200);REQUIRE(!over_hard.evict_pending);
 NewCache budgeted;budgeted.tick=100000;                                      // under the hard limits: one budget, pending
 for(uint32_t i=0;i<7000;++i)budgeted.insert(key_of(i),512,1);
 budgeted.evict_old();REQUIRE(budgeted.cache.size()==7000-native_cache_bounds::kTextureEvictPerPass);REQUIRE(budgeted.evict_pending);
 std::puts("TEXTURE_BUDGET_HARD_LIMIT ok");
}

// ------------------------------------------------------------------ shader / uniform caches
namespace native_trace {struct Scope {explicit Scope(const char*){}};}
static std::set<GLuint> g_dead_programs;
static std::map<std::pair<GLuint,GLint>,std::vector<uint8_t>> g_gl_uniforms; // what the "driver" holds per program/location
static unsigned g_program_deletes=0;
static std::vector<GLuint> g_free_ids;
inline void glDeleteProgram(GLuint id){g_dead_programs.insert(id);++g_program_deletes;g_free_ids.push_back(id);
 for(auto it=g_gl_uniforms.begin();it!=g_gl_uniforms.end();){if(it->first.first==id)it=g_gl_uniforms.erase(it);else ++it;}}
//@@SHADER_STRUCT@@
static GLShader s_active_shader={};
static std::unordered_map<GLuint,std::vector<uint8_t>> s_native_projection_cache;
//@@UNIFORM_FUNCTION@@
//@@TRIM_BLOCK@@

static uint64_t g_next_program=1;
static GLuint create_program(){GLuint id;if(!g_free_ids.empty()){id=g_free_ids.back();g_free_ids.pop_back();}else id=GLuint(g_next_program++);g_dead_programs.erase(id);return id;} // ids come back, like GL's

struct DrawResult {std::string source;std::array<GLint,8> uniforms;bool operator==(const DrawResult& o) const {return source==o.source&&uniforms==o.uniforms;}};
// Mirror of the SetupDrawState lookup (hit / source reuse / compile) with the production statements from the patch.
static DrawResult draw_bounded(uint64_t hash,const std::string& source_key,unsigned texture_mask){
 auto it=s_shader_cache.find(hash);
 if(it!=s_shader_cache.end()){it->second.native_last_used=++s_native_shader_tick;s_active_shader=it->second;}
 else{
  const auto source_hit=s_shader_sources.find(source_key);
  if(source_hit!=s_shader_sources.end()){
   s_active_shader=source_hit->second;s_active_shader.native_last_used=++s_native_shader_tick;
   NativeRestoreSamplerHistory(hash,s_active_shader);s_shader_cache[hash]=s_active_shader;NativeTrimShaderCaches();
  }else{
   GLShader sh={};sh.id=create_program();for(int i=0;i<8;++i)sh.uTex[i]=i;
   ++s_native_live_programs;sh.native_last_used=++s_native_shader_tick;
   s_shader_sources[source_key]=sh;NativeRestoreSamplerHistory(hash,sh);s_shader_cache[hash]=sh;s_active_shader=sh;NativeTrimShaderCaches();
  }
 }
 s_native_bound_program=s_active_shader.id;
 REQUIRE(!g_dead_programs.count(s_active_shader.id));
 auto alias=s_shader_cache.find(hash);REQUIRE(alias!=s_shader_cache.end()); // NativeApplyShaderSamplerAliases uses at(hash)
 for(int i=0;i<8;++i)if(alias->second.uTex[i]>=0){
  if(texture_mask>>i&1)alias->second.sampler_values[i]=i;
  const GLint value=alias->second.sampler_values[i];
  if(NativeUniformChanged(alias->second.id,alias->second.uTex[i],&value,sizeof value)){auto& slot=g_gl_uniforms[{alias->second.id,alias->second.uTex[i]}];slot.resize(sizeof value);std::memcpy(slot.data(),&value,sizeof value);}
 }
 s_active_shader=alias->second;
 DrawResult result;result.source=source_key;
 for(int i=0;i<8;++i){const auto found=g_gl_uniforms.find({s_active_shader.id,s_active_shader.uTex[i]});GLint v=0;if(found!=g_gl_uniforms.end())std::memcpy(&v,found->second.data(),sizeof v);result.uniforms[i]=v;}
 return result;
}
struct Unbounded {std::unordered_map<uint64_t,std::array<GLint,8>> history;
 DrawResult draw(uint64_t hash,const std::string& source_key,unsigned texture_mask){auto& values=history[hash];for(int i=0;i<8;++i)if(texture_mask>>i&1)values[i]=i;DrawResult r;r.source=source_key;r.uniforms=values;return r;}};

static void test_shader_cache_bounded(){
 std::mt19937_64 rng(17);Unbounded model;
 const unsigned hashes=9000,sources=5000;size_t max_aliases=0,max_programs=0;uint64_t draws=400000;
 auto source_of=[&](uint64_t h){return std::string("src")+std::to_string((h*2654435761ull)%sources)+std::string(64,'x');};
 for(uint64_t n=0;n<draws;++n){
  const uint64_t phase=(n/20000)*1500;const uint64_t hash=1+((rng()%3==0)?rng()%hashes:(phase+rng()%2500)%hashes); // moving working set + a cold tail
  const unsigned mask=(rng()%4==0)?unsigned(rng()%256):0;
  const auto want=model.draw(hash,source_of(hash),mask);
  const auto got=draw_bounded(hash,source_of(hash),mask);
  REQUIRE(want==got);
  max_aliases=std::max(max_aliases,s_shader_cache.size());max_programs=std::max<size_t>(max_programs,size_t(s_native_live_programs));
  if((n&1023)==0){
   std::set<GLuint> live;for(const auto& p:s_shader_cache){REQUIRE(!g_dead_programs.count(p.second.id));live.insert(p.second.id);}
   for(const auto& p:s_shader_sources)REQUIRE(!g_dead_programs.count(p.second.id));
   REQUIRE(live.size()<=s_native_live_programs);
   for(const auto& p:s_native_uniform_cache)REQUIRE(!g_dead_programs.count(GLuint(p.first>>32)));
   for(const auto& p:s_native_projection_cache)REQUIRE(!g_dead_programs.count(p.first));
  }
 }
 REQUIRE(max_aliases<=native_cache_bounds::kShaderAliasHigh);REQUIRE(max_programs<=native_cache_bounds::kShaderProgramHigh);
 REQUIRE(g_program_deletes>0);REQUIRE(native_cache_stats::Get(native_cache_stats::c.shader_trims)>0);
 REQUIRE(native_cache_stats::Get(native_cache_stats::c.shader_programs)==s_native_live_programs);
 std::printf("SHADER_BOUNDED draws=%llu identical_to_unbounded=%llu max_aliases=%zu max_programs=%zu trims=%llu program_deletes=%u\n",(unsigned long long)draws,(unsigned long long)draws,
  max_aliases,max_programs,(unsigned long long)native_cache_stats::Get(native_cache_stats::c.shader_trims),g_program_deletes);
}

static void test_trim_never_deletes_bound(){
 // Tiny caps through the template itself: the two kept ids survive however old they are.
 std::unordered_map<uint64_t,GLShader> aliases;std::unordered_map<std::string,GLShader> sources;native_cache_bounds::SamplerHistory history;std::vector<uint32_t> dead;
 for(uint32_t i=1;i<=200;++i){GLShader s={};s.id=i;s.native_last_used=i;aliases[i]=s;sources["s"+std::to_string(i)]=s;}
 const auto result=native_cache_bounds::TrimShaders(aliases,sources,history,200,/*keep_a*/1,/*keep_b*/2,[&](uint32_t id){dead.push_back(id);},128,96,128,96); // low marks sit above the 64-use protection window
 REQUIRE(aliases.count(1)&&aliases.count(2));REQUIRE(aliases.size()<=96);REQUIRE(result.programs==dead.size());REQUIRE(sources.size()==aliases.size());
 for(uint32_t id:dead){REQUIRE(id!=1&&id!=2);REQUIRE(!aliases.count(id));}
 for(uint32_t i=190;i<=200;++i)REQUIRE(aliases.count(i)); // newest survive
 std::puts("TRIM_KEEPS_BOUND_PROGRAMS ok");
}

static void test_uniform_cache(){
 std::mt19937_64 rng(19);g_gl_uniforms.clear();
 // property: whenever NativeUniformChanged says "unchanged", the driver already holds the value; so every draw sees the requested data.
 unsigned uploads=0,calls=0;
 auto call=[&](GLuint prog,GLint loc,uint32_t value){++calls;uint32_t v=value;
  if(NativeUniformChanged(prog,loc,&v,sizeof v)){auto& slot=g_gl_uniforms[{prog,loc}];slot.resize(4);std::memcpy(slot.data(),&v,4);++uploads;}
  auto found=g_gl_uniforms.find({prog,loc});uint32_t held=0;if(found!=g_gl_uniforms.end())std::memcpy(&held,found->second.data(),4);
  REQUIRE(found!=g_gl_uniforms.end() && held==value);};
 for(unsigned i=0;i<200000;++i)call(1+rng()%40,GLint(rng()%24),uint32_t(1+rng()%4));
 const size_t small=s_native_uniform_cache.size();REQUIRE(small<=40*24);
 // distinct keys beyond the hard bound force the clear; correctness must hold across it
 const uint64_t clears_before=s_native_uniform_clears;
 for(unsigned i=0;i<150000;++i)call(1000+i/64,GLint(i%64),uint32_t(1+(i&3)));
 for(unsigned i=0;i<100000;++i)call(1+rng()%40,GLint(rng()%24),uint32_t(1+rng()%4));
 REQUIRE(s_native_uniform_clears>clears_before);REQUIRE(s_native_uniform_cache.size()<=native_cache_bounds::kUniformEntriesMax);
 // a deleted program's entries go, and a reused id starts empty even though the new program holds zeroes
 g_gl_uniforms.clear();s_native_uniform_cache.clear();for(auto& h:s_native_uniform_hot)h=NativeUniformHot{};
 call(77,3,5);REQUIRE(native_cache_bounds::ForgetUniformProgram(s_native_uniform_cache,s_native_uniform_hot,77)==1);g_gl_uniforms.erase({77,3});
 uint32_t five=5;REQUIRE(NativeUniformChanged(77,3,&five,4)); // same id, same value, but the program is new: must upload
 std::printf("UNIFORM_CACHE calls=%u uploads=%u clears=%llu entries=%zu\n",calls,uploads,(unsigned long long)s_native_uniform_clears,s_native_uniform_cache.size());
}

int main(){
 test_evict_oldest_core();
 test_texture_equivalence();
 test_texture_amortised();
 test_texture_blocked_backoff();
 test_texture_hard_limit();
 test_trim_never_deletes_bound();
 test_uniform_cache();
 test_shader_cache_bounded();
 std::puts("CACHE_BOUNDS_ALL ok");
}
