#pragma once
#include <glad/glad.h>
#include <array>
#include <cstdlib>
namespace native_viewport {
inline const bool enabled=!std::getenv("NWII_NO_VIEWPORT_SHADOW");
inline bool have_view=false,have_depth=false;
inline std::array<GLint,4> view{};
inline std::array<GLdouble,2> depth{};
inline void Invalidate(){have_view=have_depth=false;}
}
inline void NativeViewport(GLint x,GLint y,GLsizei w,GLsizei h){
 const std::array<GLint,4> value{x,y,w,h};
 if(w<0 || h<0){native_viewport::have_view=false;glViewport(x,y,w,h);return;}
 if(native_viewport::enabled && native_viewport::have_view && value==native_viewport::view)return;
 glViewport(x,y,w,h);native_viewport::view=value;native_viewport::have_view=true;
}
inline void NativeDepthRange(GLdouble near_value,GLdouble far_value){
 const std::array<GLdouble,2> value{near_value,far_value};
 if(native_viewport::enabled && native_viewport::have_depth && value==native_viewport::depth)return;
 glDepthRange(near_value,far_value);native_viewport::depth=value;native_viewport::have_depth=true;
}
