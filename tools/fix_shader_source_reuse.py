"""Reuse programs only when both generated shader sources match exactly."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp';s=f.read_text()
if 'NATIVE_EXACT_SHADER_SOURCE_REUSE_V1' not in s:
 baseline=p/'runtime-overlays/shader-source-before.cpp'
 if not baseline.exists():baseline.write_text(s)
 anchor='static std::unordered_map<uint64_t, GLShader> s_shader_cache;'
 support='''
// NATIVE_EXACT_SHADER_SOURCE_REUSE_V1: old state keys remain fast aliases.
static std::unordered_map<std::string,GLShader> s_shader_sources;
static std::string NativeShaderSourceKey(const std::string& vertex,const std::string& fragment){
    const uint64_t size=vertex.size();std::string key;key.reserve(vertex.size()+fragment.size()+sizeof(size));
    key.append(reinterpret_cast<const char*>(&size),sizeof(size));key.append(vertex);key.append(fragment);return key;
}
'''
 assert s.count(anchor)==1;s=s.replace(anchor,anchor+support,1)
 s=s.replace('g_stat_shaders = (uint64_t)s_shader_cache.size();','g_stat_shaders = (uint64_t)s_shader_sources.size();',1)
 old='''    ++s_compiles_this_frame;
    native_trace::Scope trace_shader("gx.shader_compile");
    auto src = GenerateTEVShader(g_state, cmd.prim_type);'''
 new='''    native_trace::Scope trace_source("gx.shader_source");
    auto src = GenerateTEVShader(g_state, cmd.prim_type);
    const auto source_key=NativeShaderSourceKey(src.vertex_source,src.fragment_source);
    static const bool reuse=std::getenv("NWII_NO_SHADER_SOURCE_REUSE")==nullptr;
    const auto source_hit=s_shader_sources.find(source_key);
    if(reuse && source_hit!=s_shader_sources.end()){
      s_active_shader=source_hit->second;s_shader_cache[hash]=s_active_shader;
      native_trace::mark("shader.source_reuse");
    }else{
    ++s_compiles_this_frame;
    native_trace::Scope trace_shader("gx.shader_compile");'''
 assert s.count(old)==1;s=s.replace(old,new,1)
 old='''    s_shader_cache[hash] = sh;
    s_active_shader = sh;'''
 assert s.count(old)==1;s=s.replace(old,'''    s_shader_sources[source_key]=sh;
'''+old,1)
 old='''             (unsigned long long)hash);
  }

  glUseProgram(s_active_shader.id);'''
 assert s.count(old)==1;s=s.replace(old,'''             (unsigned long long)hash);
    } // exact-source miss
  }

  glUseProgram(s_active_shader.id);''',1)
 f.write_text(s)
print('Byte-identical vertex/fragment sources share a program; original state keys remain aliases.')

# Uniform sampler values are private to each old state-key alias. In particular,
# zero-base stages retain their old alias's default/previous sampler assignment.
s=f.read_text()
oldkey='''    std::string key;key.reserve(vertex.size()+fragment.size()+1);
    key.append(vertex);key.push_back(char(0));key.append(fragment);return key;'''
newkey='''    const uint64_t size=vertex.size();std::string key;key.reserve(vertex.size()+fragment.size()+sizeof(size));
    key.append(reinterpret_cast<const char*>(&size),sizeof(size));key.append(vertex);key.append(fragment);return key;'''
if oldkey in s:s=s.replace(oldkey,newkey,1)
if 's_native_shader_program_count' not in s:
 s=s.replace('static std::unordered_map<std::string,GLShader> s_shader_sources;', 'static std::unordered_map<std::string,GLShader> s_shader_sources;\nstatic uint64_t s_native_shader_program_count=0;',1)
 s=s.replace('g_stat_shaders = (uint64_t)s_shader_sources.size();','g_stat_shaders = s_native_shader_program_count;',1)
 s=s.replace('    ++s_compiles_this_frame;','    ++s_compiles_this_frame;++s_native_shader_program_count;',1)
if 'NATIVE_SHADER_SAMPLER_ALIAS_V1' not in s:
 s=s.replace('struct GLShader {\n  GLuint id;', 'struct GLShader {\n  GLint sampler_values[8] = {};\n  GLuint id;',1)
 helper='''// NATIVE_SHADER_SAMPLER_ALIAS_V1: source reuse must not merge logical sampler history.
static void NativeApplyShaderSamplerAliases(uint64_t hash){
  auto& alias=s_shader_cache.at(hash);
  for(int i=0;i<8;++i)if(alias.uTex[i]>=0){
    if(g_state.texStages[i].base_addr!=0)alias.sampler_values[i]=i;
    const GLint value=alias.sampler_values[i];
    if(NativeUniformChanged(alias.id,alias.uTex[i],&value,sizeof(value)))glUniform1i(alias.uTex[i],value);
  }
  s_active_shader=alias;
}
'''
 anchor='static void SetupDrawState(const GXCommand &cmd) {'
 assert s.count(anchor)==1;s=s.replace(anchor,helper+anchor,1)
 old='''  for (int i = 0; i < 8; ++i) {
    if (g_state.texStages[i].base_addr != 0 && s_active_shader.uTex[i] >= 0) {
      if (NativeUniformChanged(s_active_shader.id, s_active_shader.uTex[i], &i, sizeof(i))) glUniform1i(s_active_shader.uTex[i], i);
    }
  }'''
 assert s.count(old)==1;s=s.replace(old,'  NativeApplyShaderSamplerAliases(hash);',1)
if f.read_text()!=s:f.write_text(s)
