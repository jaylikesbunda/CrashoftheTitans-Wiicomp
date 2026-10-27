"""Persist linked GL program binaries across runs and rebuilds (NWII_NO_PROGRAM_CACHE=1 disables).

Capture frame-cost-20261006-102126-770700: 11 of 120 frames (9%) contained a 100-106 ms
shader link (gx.shader_compile 9.709 ms/frame mean, p95 105 ms), 2,049 ms of the 10,168 ms
capture; the other 109 frames averaged 74.5 ms wall (13.4 FPS). The other compiles in the
same capture took 0-3 ms, so the hitches are first-time links, not steady cost. The driver's
GLCache holds one container per executable build (13.7 MB written at the end of that run),
so every new CrashNative.exe starts cold. This stores glGetProgramBinary output under
<exe dir>/program-cache keyed by an FNV-1a hash of GL vendor/renderer/version and both
GLSL sources. A rejected or missing binary falls back to the normal compile/link, and the
loaded program is checked with GL_LINK_STATUS and glGetError before use. The generated
source, uniform lookup, block binding and sampler history paths are unchanged.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp';s=f.read_text()
marker='NATIVE_PROGRAM_BINARY_CACHE_V1'
if marker not in s:
    old_include='#include <glad/glad.h>\n'
    assert s.count(old_include)==1
    s=s.replace(old_include,old_include+'#include <SDL.h>\n#include <filesystem>\n#include <fstream>\n',1)
    a=s.index('static GLuint CompileShader(const char *vs, const char *fs) {')
    b=s.index('\n}\n',a)+3
    original=s[a:b]
    assert 'glLinkProgram(p);' in original and 'glDeleteShader(f);' in original
    new=r'''// NATIVE_PROGRAM_BINARY_CACHE_V1: see tools/fix_program_binary_cache.py.
#ifndef GL_PROGRAM_BINARY_LENGTH
#define GL_PROGRAM_BINARY_LENGTH 0x8741
#endif
#ifndef GL_PROGRAM_BINARY_RETRIEVABLE_HINT
#define GL_PROGRAM_BINARY_RETRIEVABLE_HINT 0x8257
#endif
namespace {
struct NativeProgramCache {
  using GetBinary=void(APIENTRY*)(GLuint,GLsizei,GLsizei*,GLenum*,void*);
  using SetBinary=void(APIENTRY*)(GLuint,GLenum,const void*,GLsizei);
  using SetParameter=void(APIENTRY*)(GLuint,GLenum,GLint);
  struct Header {char magic[8];uint64_t key;uint32_t format;uint32_t length;};
  bool enabled=false;GetBinary get_binary=nullptr;SetBinary set_binary=nullptr;SetParameter set_parameter=nullptr;
  std::filesystem::path directory;std::string salt;uint64_t hits=0,stores=0,rejected=0;
  NativeProgramCache() {
    if(std::getenv("NWII_NO_PROGRAM_CACHE"))return;
    bool extension=false;GLint count=0;glGetIntegerv(GL_NUM_EXTENSIONS,&count);
    for(GLint i=0;i<count && !extension;++i){const char* name=reinterpret_cast<const char*>(glGetStringi(GL_EXTENSIONS,GLuint(i)));
      extension=name && !std::strcmp(name,"GL_ARB_get_program_binary");}
    if(!extension)return;
    get_binary=reinterpret_cast<GetBinary>(SDL_GL_GetProcAddress("glGetProgramBinary"));
    set_binary=reinterpret_cast<SetBinary>(SDL_GL_GetProcAddress("glProgramBinary"));
    set_parameter=reinterpret_cast<SetParameter>(SDL_GL_GetProcAddress("glProgramParameteri"));
    if(!get_binary||!set_binary||!set_parameter)return;
    if(const char* override_dir=std::getenv("NWII_PROGRAM_CACHE_DIR"))directory=override_dir;
    else {char* base=SDL_GetBasePath();directory=std::filesystem::path(base?base:"./")/"program-cache";if(base)SDL_free(base);}
    std::error_code error;std::filesystem::create_directories(directory,error);if(error)return;
    for(GLenum name:{GL_VENDOR,GL_RENDERER,GL_VERSION}){const char* text=reinterpret_cast<const char*>(glGetString(name));salt+=text?text:"?";salt+='\n';}
    enabled=true;
  }
  uint64_t key(const char* vs,const char* fs) const {
    uint64_t hash=1469598103934665603ull;
    auto add=[&](const char* text,size_t size){for(size_t i=0;i<size;++i)hash=(hash^uint8_t(text[i]))*1099511628211ull;hash=(hash^0xffu)*1099511628211ull;};
    add(salt.data(),salt.size());add(vs,std::strlen(vs));add(fs,std::strlen(fs));return hash;
  }
  std::filesystem::path path(uint64_t value) const {char name[32];std::snprintf(name,sizeof name,"%016llx.bin",(unsigned long long)value);return directory/name;}
  GLuint load(uint64_t value) {
    std::ifstream in(path(value),std::ios::binary);if(!in)return 0;
    Header header{};in.read(reinterpret_cast<char*>(&header),sizeof header);
    if(!in || std::memcmp(header.magic,"NWPBC001",8) || header.key!=value || !header.length || header.length>(64u<<20)){++rejected;return 0;}
    std::vector<char> data(header.length);in.read(data.data(),std::streamsize(data.size()));if(!in){++rejected;return 0;}
    while(glGetError()!=GL_NO_ERROR){}
    const GLuint program=glCreateProgram();
    set_binary(program,GLenum(header.format),data.data(),GLsizei(data.size()));
    GLint linked=0;glGetProgramiv(program,GL_LINK_STATUS,&linked);
    if(!linked || glGetError()!=GL_NO_ERROR){glDeleteProgram(program);++rejected;return 0;}
    ++hits;return program;
  }
  void store(uint64_t value,GLuint program) {
    GLint linked=0,length=0;glGetProgramiv(program,GL_LINK_STATUS,&linked);if(!linked)return;
    glGetProgramiv(program,GL_PROGRAM_BINARY_LENGTH,&length);if(length<=0 || length>(64<<20))return;
    std::vector<char> data(static_cast<size_t>(length));GLsizei written=0;GLenum format=0;
    get_binary(program,length,&written,&format,data.data());
    if(written<=0 || glGetError()!=GL_NO_ERROR)return;
    Header header{};std::memcpy(header.magic,"NWPBC001",8);header.key=value;header.format=uint32_t(format);header.length=uint32_t(written);
    const auto final_path=path(value);auto temporary=final_path;temporary+=".tmp";
    {std::ofstream out(temporary,std::ios::binary|std::ios::trunc);if(!out)return;
     out.write(reinterpret_cast<const char*>(&header),sizeof header);out.write(data.data(),written);if(!out)return;}
    std::error_code error;std::filesystem::rename(temporary,final_path,error);
    if(error){std::filesystem::remove(temporary,error);return;}
    ++stores;
  }
};
NativeProgramCache& NativeProgramBinaryCache() {static NativeProgramCache cache;return cache;}
}
static GLuint CompileShader(const char *vs, const char *fs) {
  auto& cache=NativeProgramBinaryCache();
  uint64_t cache_key=0;
  if(cache.enabled){
    cache_key=cache.key(vs,fs);
    if(const GLuint cached=cache.load(cache_key)){native_trace::mark("shader.binary_hit");return cached;}
  }
  GLuint v = glCreateShader(GL_VERTEX_SHADER);
  glShaderSource(v, 1, &vs, nullptr);
  glCompileShader(v);

  GLuint f = glCreateShader(GL_FRAGMENT_SHADER);
  glShaderSource(f, 1, &fs, nullptr);
  glCompileShader(f);

  GLuint p = glCreateProgram();
  if(cache.enabled)cache.set_parameter(p,GL_PROGRAM_BINARY_RETRIEVABLE_HINT,GL_TRUE);
  glAttachShader(p, v);
  glAttachShader(p, f);
  glLinkProgram(p);

  glDeleteShader(v);
  glDeleteShader(f);
  if(cache.enabled)cache.store(cache_key,p);
  return p;
}
'''
    s=s[:a]+new+s[b:]
    f.write_text(s)
print('Program binaries persist under <exe dir>/program-cache; compile/link fallback retained.')
