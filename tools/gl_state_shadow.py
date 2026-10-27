"""Shared raster-state shadow; all writers and context creation must participate."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]
specs=[('DepthFunc',['GLenum'],['mode'],'mode>=GL_NEVER && mode<=GL_ALWAYS'),('DepthMask',['GLboolean'],['flag'],'true'),('ColorMask',['GLboolean']*4,['r','g','b','a'],'true'),('BlendFunc',['GLenum']*2,['src','dst'],'native_raster::blend_factor(src,true) && native_raster::blend_factor(dst,false)'),('BlendEquation',['GLenum'],['mode'],'mode==GL_FUNC_ADD || mode==GL_FUNC_SUBTRACT || mode==GL_FUNC_REVERSE_SUBTRACT || mode==GL_MIN || mode==GL_MAX'),('LogicOp',['GLenum'],['mode'],'mode>=GL_CLEAR && mode<=GL_SET'),('CullFace',['GLenum'],['mode'],'mode==GL_FRONT || mode==GL_BACK || mode==GL_FRONT_AND_BACK'),('FrontFace',['GLenum'],['mode'],'mode==GL_CW || mode==GL_CCW')]
prefix='''#pragma once
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
'''
def header():
 s=prefix
 for name,types,args,valid in specs:
  declarations=','.join(t+' '+a for t,a in zip(types,args));values=','.join('unsigned('+a+'!=0)' if t=='GLboolean' else 'unsigned('+a+')' for t,a in zip(types,args))
  s+='inline void NativeRaster'+name+'('+declarations+'){\n'
  s+=' static unsigned seen=0;static std::array<unsigned,'+str(len(args))+'> last{};const std::array<unsigned,'+str(len(args))+'> value={'+values+'};\n'
  s+=' if(native_raster::enabled && ('+valid+')){if(seen==native_raster::epoch && value==last)return;seen=native_raster::epoch;last=value;}\n'
  s+=' gl'+name+'('+','.join(args)+');\n}\n'
 return s
