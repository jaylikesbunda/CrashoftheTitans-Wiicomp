#pragma once
#include <glad/glad.h>
#include <array>
#include <cstdlib>
#include <cstdint>
#include <unordered_set>
// NATIVE_GL_TEXTURE_SHADOW_V1: single GL context owner, all native callers routed.
namespace native_texture_shadow {
inline const bool enabled=std::getenv("NWII_NO_TEXTURE_BINDING_SHADOW")==nullptr;
struct Slot {GLuint texture=0,sampler=0;bool texture_known=false,sampler_known=false;};
inline std::array<Slot,8> slots{};
struct Names {
 std::array<uint64_t,1024> low{};std::unordered_set<GLuint> high;
 bool count(GLuint n)const{return n<65536?bool(low[n>>6]&(uint64_t(1)<<(n&63))):high.count(n)!=0;}
 void insert(GLuint n){if(n<65536)low[n>>6]|=uint64_t(1)<<(n&63);else high.insert(n);}
 void erase(GLuint n){if(n<65536)low[n>>6]&=~(uint64_t(1)<<(n&63));else high.erase(n);}
 void clear(){low.fill(0);high.clear();}
};
inline Names owned_textures,owned_samplers;
inline bool active_known=false;
inline unsigned active=0;
struct Stats {uint64_t active_requests=0,active_skipped=0,texture_requests=0,texture_skipped=0,sampler_requests=0,sampler_skipped=0;};
inline Stats counts{},baseline{};
inline Stats GetStats(){return {counts.active_requests-baseline.active_requests,counts.active_skipped-baseline.active_skipped,
 counts.texture_requests-baseline.texture_requests,counts.texture_skipped-baseline.texture_skipped,
 counts.sampler_requests-baseline.sampler_requests,counts.sampler_skipped-baseline.sampler_skipped};}
inline void ResetBaseline(){baseline=counts;}
inline void invalidate(){active_known=false;for(auto& s:slots)s={};owned_textures.clear();owned_samplers.clear();}
inline void invalidate_textures(){for(auto& s:slots)s.texture_known=false;}
}
inline void NativeTextureActive(GLenum unit){
 using namespace native_texture_shadow;
 ++counts.active_requests;
 if(enabled && unit>=GL_TEXTURE0 && unit<GL_TEXTURE0+8){
  const unsigned i=unsigned(unit-GL_TEXTURE0);
  if(active_known && active==i){++counts.active_skipped;return;}
  active=i;active_known=true;
 }else active_known=false;
 glActiveTexture(unit);
}
inline void NativeTextureBind(GLenum target,GLuint texture){
 using namespace native_texture_shadow;
 ++counts.texture_requests;
 if(enabled && target==GL_TEXTURE_2D && active_known){
  auto& s=slots[active];
  if(s.texture_known && s.texture==texture){++counts.texture_skipped;return;}
  s.texture_known=texture==0 || owned_textures.count(texture);
  s.texture=texture;
 }else {invalidate_textures();if(target!=GL_TEXTURE_2D)owned_textures.erase(texture);}
 glBindTexture(target,texture);
}
inline void NativeTextureBindSampler(GLuint unit,GLuint sampler){
 using namespace native_texture_shadow;
 ++counts.sampler_requests;
 if(enabled && unit<slots.size()){
  auto& s=slots[unit];
  if(s.sampler_known && s.sampler==sampler){++counts.sampler_skipped;return;}
  s.sampler=sampler;s.sampler_known=sampler==0 || owned_samplers.count(sampler);
 }else if(unit<slots.size())slots[unit].sampler_known=false;
 glBindSampler(unit,sampler);
}
inline void NativeTextureDelete(GLsizei count,const GLuint* textures){
 // Deletion implicitly unbinds names and may allow future glGenTextures name reuse.
 native_texture_shadow::invalidate_textures();
 for(GLsizei i=0;i<count;++i)native_texture_shadow::owned_textures.erase(textures[i]);
 glDeleteTextures(count,textures);
}
inline void NativeTextureGenerate(GLsizei count,GLuint* textures){
 glGenTextures(count,textures);
 for(GLsizei i=0;i<count;++i)if(textures[i])native_texture_shadow::owned_textures.insert(textures[i]);
}
inline void NativeTextureGenerateSamplers(GLsizei count,GLuint* samplers){
 glGenSamplers(count,samplers);
 for(GLsizei i=0;i<count;++i)if(samplers[i])native_texture_shadow::owned_samplers.insert(samplers[i]);
}
inline void NativeTextureDeleteSamplers(GLsizei count,const GLuint* samplers){
 for(auto& s:native_texture_shadow::slots)s.sampler_known=false;
 for(GLsizei i=0;i<count;++i)native_texture_shadow::owned_samplers.erase(samplers[i]);
 glDeleteSamplers(count,samplers);
}
