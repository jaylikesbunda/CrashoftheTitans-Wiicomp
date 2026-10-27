#pragma once
#include <array>
#include <cstdlib>
// NATIVE_GL_RASTER_SHADOW_V1: CPU-thread/context-owner only; initial state unknown.
namespace native_raster {
inline const bool enabled=std::getenv("NWII_NO_RASTER_SHADOW")==nullptr;
inline unsigned epoch=1;
inline void invalidate(){++epoch;}
inline bool blend_factor(GLenum value,bool source){switch(value){case GL_ZERO:case GL_ONE:case GL_SRC_COLOR:case GL_ONE_MINUS_SRC_COLOR:case GL_DST_COLOR:case GL_ONE_MINUS_DST_COLOR:case GL_SRC_ALPHA:case GL_ONE_MINUS_SRC_ALPHA:case GL_DST_ALPHA:case GL_ONE_MINUS_DST_ALPHA:case GL_CONSTANT_COLOR:case GL_ONE_MINUS_CONSTANT_COLOR:case GL_CONSTANT_ALPHA:case GL_ONE_MINUS_CONSTANT_ALPHA:return true;case GL_SRC_ALPHA_SATURATE:return source;default:return false;}}
inline int capability(GLenum value){switch(value){case GL_DEPTH_TEST:return 0;case GL_BLEND:return 1;case GL_COLOR_LOGIC_OP:return 2;case GL_CULL_FACE:return 3;case GL_SCISSOR_TEST:return 4;default:return -1;}}
inline std::array<unsigned,5> cap_epoch{};inline std::array<bool,5> cap_value{};
inline void set_cap(GLenum cap,bool on){int i=capability(cap);
 if(i>=0 && enabled){if(cap_epoch[i]==epoch && cap_value[i]==on)return;cap_epoch[i]=epoch;cap_value[i]=on;}
 if(on)glEnable(cap);else glDisable(cap);
}
}
inline void NativeRasterEnable(GLenum cap){native_raster::set_cap(cap,true);}
inline void NativeRasterDisable(GLenum cap){native_raster::set_cap(cap,false);}
inline void NativeRasterDepthFunc(GLenum mode){
 static unsigned seen=0;static std::array<unsigned,1> last{};const std::array<unsigned,1> value={unsigned(mode)};
 if(native_raster::enabled && (mode>=GL_NEVER && mode<=GL_ALWAYS)){if(seen==native_raster::epoch && value==last)return;seen=native_raster::epoch;last=value;}
 glDepthFunc(mode);
}
inline void NativeRasterDepthMask(GLboolean flag){
 static unsigned seen=0;static std::array<unsigned,1> last{};const std::array<unsigned,1> value={unsigned(flag!=0)};
 if(native_raster::enabled && (true)){if(seen==native_raster::epoch && value==last)return;seen=native_raster::epoch;last=value;}
 glDepthMask(flag);
}
inline void NativeRasterColorMask(GLboolean r,GLboolean g,GLboolean b,GLboolean a){
 static unsigned seen=0;static std::array<unsigned,4> last{};const std::array<unsigned,4> value={unsigned(r!=0),unsigned(g!=0),unsigned(b!=0),unsigned(a!=0)};
 if(native_raster::enabled && (true)){if(seen==native_raster::epoch && value==last)return;seen=native_raster::epoch;last=value;}
 glColorMask(r,g,b,a);
}
inline void NativeRasterBlendFunc(GLenum src,GLenum dst){
 static unsigned seen=0;static std::array<unsigned,2> last{};const std::array<unsigned,2> value={unsigned(src),unsigned(dst)};
 if(native_raster::enabled && (native_raster::blend_factor(src,true) && native_raster::blend_factor(dst,false))){if(seen==native_raster::epoch && value==last)return;seen=native_raster::epoch;last=value;}
 glBlendFunc(src,dst);
}
inline void NativeRasterBlendEquation(GLenum mode){
 static unsigned seen=0;static std::array<unsigned,1> last{};const std::array<unsigned,1> value={unsigned(mode)};
 if(native_raster::enabled && (mode==GL_FUNC_ADD || mode==GL_FUNC_SUBTRACT || mode==GL_FUNC_REVERSE_SUBTRACT || mode==GL_MIN || mode==GL_MAX)){if(seen==native_raster::epoch && value==last)return;seen=native_raster::epoch;last=value;}
 glBlendEquation(mode);
}
inline void NativeRasterLogicOp(GLenum mode){
 static unsigned seen=0;static std::array<unsigned,1> last{};const std::array<unsigned,1> value={unsigned(mode)};
 if(native_raster::enabled && (mode>=GL_CLEAR && mode<=GL_SET)){if(seen==native_raster::epoch && value==last)return;seen=native_raster::epoch;last=value;}
 glLogicOp(mode);
}
inline void NativeRasterCullFace(GLenum mode){
 static unsigned seen=0;static std::array<unsigned,1> last{};const std::array<unsigned,1> value={unsigned(mode)};
 if(native_raster::enabled && (mode==GL_FRONT || mode==GL_BACK || mode==GL_FRONT_AND_BACK)){if(seen==native_raster::epoch && value==last)return;seen=native_raster::epoch;last=value;}
 glCullFace(mode);
}
inline void NativeRasterFrontFace(GLenum mode){
 static unsigned seen=0;static std::array<unsigned,1> last{};const std::array<unsigned,1> value={unsigned(mode)};
 if(native_raster::enabled && (mode==GL_CW || mode==GL_CCW)){if(seen==native_raster::epoch && value==last)return;seen=native_raster::epoch;last=value;}
 glFrontFace(mode);
}
