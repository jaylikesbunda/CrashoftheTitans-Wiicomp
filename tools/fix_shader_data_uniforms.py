"""Changing fog/alpha data does not need a new shader program; retain literal A/B mode."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
header=rt/'include/runtime/native_shader_data.h';value=(p/'runtime-overlays/native_shader_data.h').read_text()
if not header.exists() or header.read_text()!=value:header.write_text(value)
def edit(file,old,new):
 f=rt/file;s=f.read_text()
 if new in s:return
 assert s.count(old)==1,(file,old[:100],s.count(old));f.write_text(s.replace(old,new,1))
edit('src/hle/gx/tev_shader_gen.cpp','    fs << "uniform vec4 uTevKColor[4];\\n";',
 '    fs << "uniform vec4 uTevKColor[4];\\n";\n    if(NativeShaderDataUniformsEnabled())fs << "uniform uvec4 uNativeFogData;\\nuniform vec3 uNativeFogColor;\\nuniform vec2 uNativeAlphaRef;\\n"; // NATIVE_SHADER_DATA_UNIFORMS_V1')
edit('src/hle/gx/tev_shader_gen.cpp','auto cmp = [&](uint8_t comp, uint8_t ref) -> std::string {',
 'auto cmp = [&](uint8_t comp, uint8_t ref, unsigned channel) -> std::string {')
f=rt/'src/hle/gx/tev_shader_gen.cpp';s=f.read_text()
previous='            std::string r=NativeShaderDataUniformsEnabled()?std::string(channel?"uNativeAlphaRef.y":"uNativeAlphaRef.x"):std::string(rb);'
if previous in s:f.write_text(s.replace(previous,'            std::string r(rb);',1))
edit('src/hle/gx/tev_shader_gen.cpp','            std::string r(rb);',
 '            std::string r=NativeShaderDataUniformsEnabled() && ref!=0 && ref!=255?std::string(channel?"uNativeAlphaRef.y":"uNativeAlphaRef.x"):std::string(rb); // Preserve endpoint specialization.')
edit('src/hle/gx/tev_shader_gen.cpp','cmp(at.comp0, at.ref0);','cmp(at.comp0, at.ref0, 0);')
edit('src/hle/gx/tev_shader_gen.cpp','cmp(at.comp1, at.ref1);','cmp(at.comp1, at.ref1, 1);')
edit('src/hle/gx/renderer_gl.cpp','#include "runtime/gx/tev_shader_gen.h"',
 '#include "runtime/gx/tev_shader_gen.h"\n#include "runtime/native_shader_data.h"')
edit('src/hle/gx/renderer_gl.cpp','  GLint uTevKColor;',
 '  GLint uNativeFogData=-1,uNativeFogColor=-1,uNativeAlphaRef=-1; // NATIVE_SHADER_DATA_UNIFORMS_V1\n  GLint uTevKColor;')
edit('src/hle/gx/renderer_gl.cpp','    sh.uProjMtx = glGetUniformLocation(sh.id, "mvp");',
 '    sh.uNativeFogData=glGetUniformLocation(sh.id,"uNativeFogData");\n    sh.uNativeFogColor=glGetUniformLocation(sh.id,"uNativeFogColor");\n    sh.uNativeAlphaRef=glGetUniformLocation(sh.id,"uNativeAlphaRef");\n    sh.uProjMtx = glGetUniformLocation(sh.id, "mvp");')
edit('src/hle/gx/renderer_gl.cpp','  NativeApplyShaderSamplerAliases(hash);',
 '''  NativeApplyShaderSamplerAliases(hash);
  if(NativeShaderDataUniformsEnabled()){ // NATIVE_SHADER_DATA_UPLOAD_V1: exact integer fields, original GPU math.
    const GLuint fog[4]={(g_state.bp[0xee]&0xfffffu)<<12,(g_state.bp[0xf1]&0xfffffu)<<12,g_state.bp[0xef],g_state.bp[0xf0]&31u};
    const uint32_t color=g_state.bp[0xf2];const float rgb[3]={float((color>>16)&255),float((color>>8)&255),float(color&255)};
    const float refs[2]={float(g_state.alphaTest.ref0),float(g_state.alphaTest.ref1)};
    if(s_active_shader.uNativeFogData>=0 && NativeUniformChanged(s_active_shader.id,s_active_shader.uNativeFogData,fog,sizeof fog))glUniform4uiv(s_active_shader.uNativeFogData,1,fog);
    if(s_active_shader.uNativeFogColor>=0 && NativeUniformChanged(s_active_shader.id,s_active_shader.uNativeFogColor,rgb,sizeof rgb))glUniform3fv(s_active_shader.uNativeFogColor,1,rgb);
    if(s_active_shader.uNativeAlphaRef>=0 && NativeUniformChanged(s_active_shader.id,s_active_shader.uNativeAlphaRef,refs,sizeof refs))glUniform2fv(s_active_shader.uNativeAlphaRef,1,refs);
  }''')
print('Fog values and integer alpha thresholds are uniforms; logic modes remain specialized. NWII_LITERAL_SHADER_DATA restores literals.')
