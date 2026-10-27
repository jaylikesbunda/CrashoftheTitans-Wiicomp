// Read-only draw archive for isolated host replay. Call before clearing s_batch.
// This captures an isolated draw, not the preceding framebuffer or a guest save.
static void NativeDrawArchive(const char *prefix, unsigned sample, GLuint program) {
  static const bool enabled = std::getenv("NWII_DRAW_ARCHIVE") != nullptr;
  if (!enabled || s_batch.empty() || sample > 50) return;
  const std::string path = std::string(prefix) + "_draw_" + std::to_string(sample) + ".bin";
  FILE *file = std::fopen(path.c_str(), "wb");
  if (!file) return;
  auto word = [&](uint32_t value){std::fwrite(&value,4,1,file);};
  auto string = [&](const std::string &value){word(uint32_t(value.size()));std::fwrite(value.data(),1,value.size(),file);};
  word(0x44524132); // DRA2, 40-float vertex; readers also support DRA1
  word(uint32_t(s_batch.size()));
  static_assert(sizeof(BatchVertex)==160,"Archive layout changed");
  std::fwrite(s_batch.data(),sizeof(BatchVertex),s_batch.size(),file);
  GLuint shaders[8]={};GLsizei shader_count=0;
  glGetAttachedShaders(program,8,&shader_count,shaders);
  std::string vertex,fragment;
  for(int i=0;i<shader_count;++i) {
    GLint size=0,type=0;glGetShaderiv(shaders[i],GL_SHADER_SOURCE_LENGTH,&size);
    glGetShaderiv(shaders[i],GL_SHADER_TYPE,&type);
    std::vector<char> source(size_t(size)+1);GLsizei length=0;
    glGetShaderSource(shaders[i],size,&length,source.data());
    if(type==GL_VERTEX_SHADER)vertex.assign(source.data(),length);
    if(type==GL_FRAGMENT_SHADER)fragment.assign(source.data(),length);
  }
  string(vertex);string(fragment);
  GLint uniforms=0;glGetProgramiv(program,GL_ACTIVE_UNIFORMS,&uniforms);
  struct Entry {std::string name;uint32_t type,count;float f[16]={};int32_t i[16]={};};
  std::vector<Entry> entries;
  for(int u=0;u<uniforms;++u) {
    char name[256]={};GLsizei length=0;GLint size=0;GLenum type=0;
    glGetActiveUniform(program,u,256,&length,&size,&type,name);
    uint32_t count=0;
    switch(type){case GL_FLOAT:case GL_INT:case GL_SAMPLER_2D:count=1;break;
      case GL_FLOAT_VEC2:count=2;break;case GL_FLOAT_VEC3:count=3;break;
      case GL_FLOAT_VEC4:count=4;break;case GL_FLOAT_MAT4:count=16;break;}
    if(!count)continue;
    std::string base(name);size_t bracket=base.find("[0]");
    for(int index=0;index<size;++index) {
      Entry entry;entry.name=base;
      if(bracket!=std::string::npos)entry.name.replace(bracket,3,"["+std::to_string(index)+"]");
      GLint loc=glGetUniformLocation(program,entry.name.c_str());if(loc<0)continue;
      entry.type=type;entry.count=count;
      if(type==GL_INT||type==GL_SAMPLER_2D)glGetUniformiv(program,loc,entry.i);
      else glGetUniformfv(program,loc,entry.f);
      entries.push_back(entry);
    }
  }
  word(uint32_t(entries.size()));
  for(const auto &entry:entries) {
    string(entry.name);word(entry.type);word(entry.count);
    std::fwrite(entry.type==GL_INT||entry.type==GL_SAMPLER_2D?
      static_cast<const void*>(entry.i):static_cast<const void*>(entry.f),4,entry.count,file);
  }
  GLint active=0;glGetIntegerv(GL_ACTIVE_TEXTURE,&active);word(8);
  for(int unit=0;unit<8;++unit) {
    glActiveTexture(GL_TEXTURE0+unit);GLint texture=0,w=0,h=0;
    glGetIntegerv(GL_TEXTURE_BINDING_2D,&texture);
    if(texture){glGetTexLevelParameteriv(GL_TEXTURE_2D,0,GL_TEXTURE_WIDTH,&w);glGetTexLevelParameteriv(GL_TEXTURE_2D,0,GL_TEXTURE_HEIGHT,&h);}
    if(w<=0||h<=0||w>1024||h>1024)w=h=0;
    word(w);word(h);
    for(GLenum param:{GL_TEXTURE_WRAP_S,GL_TEXTURE_WRAP_T,GL_TEXTURE_MIN_FILTER,GL_TEXTURE_MAG_FILTER}) {
      GLint value=0;if(texture)glGetTexParameteriv(GL_TEXTURE_2D,param,&value);word(value);
    }
    if(w&&h){std::vector<unsigned char> rgba(size_t(w)*h*4);glGetTexImage(GL_TEXTURE_2D,0,GL_RGBA,GL_UNSIGNED_BYTE,rgba.data());std::fwrite(rgba.data(),1,rgba.size(),file);}
  }
  glActiveTexture(active);
  // Raw guest state allows investigation of the uploaded lighting/state history.
  std::fwrite(g_state.bp,4,256,file);
  std::fwrite(g_state.lights,4,8*16,file);
  // Optional trailing DRA1 extension; older archives end at the light bank.
  std::fwrite(g_state.normalMatrices,4,96,file);
  std::fwrite(g_state.posMatrices,4,256,file);
  std::fwrite(g_state.cp,4,256,file);
  std::fwrite(g_state.xf,4,256,file);
  std::fclose(file);
}
