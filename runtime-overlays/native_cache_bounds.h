#pragma once
// NATIVE_CACHE_BOUNDS_V1: bounded renderer caches and their occupancy counters.
// Everything here is GL-free so the selection and invalidation rules can be tested without a context.
#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace native_cache_stats {
// Render-thread writers, relaxed loads from the F3/F4 export.
struct Counters {
 std::atomic<uint64_t> shader_programs{0},shader_aliases{0},shader_sources{0},uniform_entries{0},projection_entries{0},
  uniform_clears{0},shader_trims{0},shader_program_evictions{0},shader_alias_evictions{0},
  texture_entries{0},texture_bytes{0},texture_hot_keys{0},texture_evictions{0},texture_eviction_passes{0},
  texture_eviction_blocked{0},texture_hot_prunes{0};
};
inline Counters c;
inline void Set(std::atomic<uint64_t>& value,uint64_t v){value.store(v,std::memory_order_relaxed);}
inline void Add(std::atomic<uint64_t>& value,uint64_t v=1){value.store(value.load(std::memory_order_relaxed)+v,std::memory_order_relaxed);}
inline uint64_t Get(const std::atomic<uint64_t>& value){return value.load(std::memory_order_relaxed);}
inline std::string Json(){
 std::ostringstream s;
 s<<"{\"shader_programs\":"<<Get(c.shader_programs)<<",\"shader_aliases\":"<<Get(c.shader_aliases)<<",\"shader_sources\":"<<Get(c.shader_sources)
  <<",\"uniform_entries\":"<<Get(c.uniform_entries)<<",\"projection_entries\":"<<Get(c.projection_entries)<<",\"uniform_clears\":"<<Get(c.uniform_clears)
  <<",\"shader_trims\":"<<Get(c.shader_trims)<<",\"shader_program_evictions\":"<<Get(c.shader_program_evictions)<<",\"shader_alias_evictions\":"<<Get(c.shader_alias_evictions)
  <<",\"texture_cache_entries\":"<<Get(c.texture_entries)<<",\"texture_cache_bytes\":"<<Get(c.texture_bytes)<<",\"texture_hot_keys\":"<<Get(c.texture_hot_keys)
  <<",\"texture_evictions\":"<<Get(c.texture_evictions)<<",\"texture_eviction_passes\":"<<Get(c.texture_eviction_passes)
  <<",\"texture_eviction_blocked\":"<<Get(c.texture_eviction_blocked)<<",\"texture_hot_prunes\":"<<Get(c.texture_hot_prunes)<<'}';
 return s.str();
}
inline std::string Text(){
 std::ostringstream s;
 s<<"Cache occupancy (live; mirrors refresh when the cache changes):\n"
  <<"  shader programs/aliases/sources: "<<Get(c.shader_programs)<<'/'<<Get(c.shader_aliases)<<'/'<<Get(c.shader_sources)
  <<"; trims "<<Get(c.shader_trims)<<", programs evicted "<<Get(c.shader_program_evictions)<<", aliases evicted "<<Get(c.shader_alias_evictions)<<'\n'
  <<"  uniform entries: "<<Get(c.uniform_entries)<<" (cap clears "<<Get(c.uniform_clears)<<"); projection entries: "<<Get(c.projection_entries)<<'\n'
  <<"  texture cache entries/bytes/hot keys: "<<Get(c.texture_entries)<<'/'<<Get(c.texture_bytes)<<'/'<<Get(c.texture_hot_keys)
  <<"; evicted "<<Get(c.texture_evictions)<<" in "<<Get(c.texture_eviction_passes)<<" passes ("<<Get(c.texture_eviction_blocked)<<" blocked), hot-key prunes "<<Get(c.texture_hot_prunes)<<'\n';
 return s.str();
}
}

namespace native_cache_bounds {
// Shader caches: high triggers a trim, low is where the trim stops. The gap makes every trim cover many inserts.
inline constexpr size_t kShaderAliasHigh=4096,kShaderAliasLow=3072;
inline constexpr size_t kShaderProgramHigh=1536,kShaderProgramLow=1152;
inline constexpr uint64_t kShaderProtectUses=64;       // aliases used within this many shader lookups are never trimmed
inline constexpr size_t kSamplerHistoryMax=65536;
inline constexpr size_t kUniformEntriesMax=65536;      // must match the literal inside NativeUniformChanged
inline constexpr uint64_t kTextureProtectTicks=64;      // existing in-use window of TextureCache::evict_old
inline constexpr size_t kTextureHotSlack=4096;
// One pass evicts at most this many textures; the rest carry on from the next miss (evict_pending) so no frame absorbs a
// whole sweep. Past the hard limits a pass is not capped, so memory stays bounded even when the budget falls behind.
inline constexpr size_t kTextureEvictPerPass=256;
inline constexpr size_t kTextureHardCount=8400;         // 2x the low-water count the passes aim for
inline constexpr size_t kTextureHardBytes=size_t(640)<<20;

// Evict candidates (stamp, key) oldest first without sorting all of them: nth_element peels off
// the next chunk of the oldest entries, and only that chunk is sorted. Stops as soon as done() holds.
template<class Key,class Done,class Evict>
size_t EvictOldest(std::vector<std::pair<uint64_t,Key>>& candidates,size_t first_chunk,Done done,Evict evict){
 const auto older=[](const std::pair<uint64_t,Key>& a,const std::pair<uint64_t,Key>& b){return a.first<b.first;};
 size_t evicted=0,begin=0,chunk=std::max<size_t>(first_chunk,64);
 while(begin<candidates.size() && !done()){
  const size_t end=std::min(candidates.size(),begin+chunk);
  if(end<candidates.size())std::nth_element(candidates.begin()+begin,candidates.begin()+end,candidates.end(),older);
  std::sort(candidates.begin()+begin,candidates.begin()+end,older);
  for(size_t i=begin;i<end && !done();++i){evict(candidates[i].second);++evicted;}
  begin=end;chunk*=2;
 }
 return evicted;
}

struct TrimResult {size_t aliases=0,programs=0;};
using SamplerHistory=std::unordered_map<uint64_t,std::array<int32_t,8>>;

// Trim the shader-state alias map and the exact-source map together.
//   * an alias is a hash -> program entry; a program dies with its last alias
//   * `keep_a`/`keep_b` (the program bound for the open batch and the one GL has bound) never die
//   * recently used aliases are skipped, so the working set of the current frames survives
//   * the logical sampler history of an evicted alias is parked so a rebuilt alias starts where it stopped
// Shader needs: id, native_last_used, sampler_values[8].
template<class Shader,class OnDeleteProgram>
TrimResult TrimShaders(std::unordered_map<uint64_t,Shader>& aliases,std::unordered_map<std::string,Shader>& sources,
 SamplerHistory& history,size_t live_programs,uint32_t keep_a,uint32_t keep_b,OnDeleteProgram on_delete,
 size_t alias_high=kShaderAliasHigh,size_t alias_low=kShaderAliasLow,size_t program_high=kShaderProgramHigh,size_t program_low=kShaderProgramLow){
 TrimResult result;
 if(aliases.size()<=alias_high && live_programs<=program_high)return result;
 std::unordered_map<uint32_t,size_t> remaining;
 uint64_t newest=0;
 for(const auto& pair:aliases){++remaining[uint32_t(pair.second.id)];newest=std::max<uint64_t>(newest,pair.second.native_last_used);}
 for(const auto& pair:sources)remaining.emplace(uint32_t(pair.second.id),0);
 size_t programs=remaining.size(),alias_count=aliases.size();
 std::vector<std::pair<uint64_t,uint64_t>> candidates;candidates.reserve(aliases.size());
 for(const auto& pair:aliases){
  const uint32_t id=uint32_t(pair.second.id);
  if(id==keep_a || id==keep_b || pair.second.native_last_used+kShaderProtectUses>newest)continue;
  candidates.emplace_back(pair.second.native_last_used,pair.first);
 }
 std::vector<uint32_t> dead;
 result.aliases=EvictOldest(candidates,alias_count>alias_low?alias_count-alias_low:0,
  [&]{return alias_count<=alias_low && programs<=program_low;},
  [&](uint64_t hash){
   auto it=aliases.find(hash);if(it==aliases.end())return;
   bool parked=false;for(int32_t v:it->second.sampler_values)parked|=v!=0;
   if(parked){if(history.size()>=kSamplerHistoryMax)history.clear();std::array<int32_t,8> values{};for(int i=0;i<8;++i)values[i]=it->second.sampler_values[i];history[hash]=values;}
   const uint32_t id=uint32_t(it->second.id);aliases.erase(it);--alias_count;
   auto left=remaining.find(id);if(left!=remaining.end() && left->second && --left->second==0){--programs;dead.push_back(id);}
  });
 // A source-only program (no alias left) is unreachable through the alias map; sources reach it only through a new alias,
 // which would hand out a deleted id, so it dies with its last alias.
 if(!dead.empty()){
  const std::unordered_set<uint32_t> gone(dead.begin(),dead.end());
  for(auto it=sources.begin();it!=sources.end();){if(gone.count(uint32_t(it->second.id)))it=sources.erase(it);else ++it;}
  for(uint32_t id:dead)on_delete(id);
 }
 result.programs=dead.size();
 return result;
}

// Drop one program's entries from the (program<<32|location)-keyed uniform cache and clear any hot slot that names it.
template<class Map,class Hot,size_t N>
size_t ForgetUniformProgram(Map& cache,Hot (&hot)[N],uint32_t program){
 size_t removed=0;
 for(auto it=cache.begin();it!=cache.end();){if(uint32_t(it->first>>32)==program){it=cache.erase(it);++removed;}else ++it;}
 for(auto& slot:hot)if(uint32_t(slot.key>>32)==program){slot.key=0;slot.value=nullptr;}
 return removed;
}
}
