#pragma once
#include <cstdlib>
namespace nwii::runtime::gx {
inline bool NativeShaderDataUniformsEnabled(){
 static const bool enabled=std::getenv("NWII_LITERAL_SHADER_DATA")==nullptr;
 return enabled;
}
}
