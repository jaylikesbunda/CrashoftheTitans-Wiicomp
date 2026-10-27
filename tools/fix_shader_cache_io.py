"""Separate driver binary extraction and move bounded filesystem work off the renderer."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
h=rt/'include/runtime/native_shader_cache_io.h';value=(p/'runtime-overlays/native_shader_cache_io.h').read_text()
if not h.exists() or h.read_text()!=value:h.write_text(value)
f=rt/'src/hle/gx/renderer_gl.cpp';s=f.read_text()
if '#include "runtime/native_shader_cache_io.h"' not in s:s='#include "runtime/native_shader_cache_io.h"\n'+s
if 'NATIVE_SHADER_CACHE_IO_V1' not in s:
 s=s.replace('std::string salt;uint64_t hits=0,stores=0,rejected=0;', 'std::string salt;uint64_t hits=0,rejected=0;std::atomic<uint64_t> stores{0};native_shader_cache::Writer writer; // NATIVE_SHADER_CACHE_IO_V1')
 a=s.index('  void store(uint64_t value,GLuint program) {');b=s.index('\n  }\n};',a)+5
 s=s[:a]+'''  void flush(){writer.flush();}
  void store(uint64_t value,GLuint program) {
    GLint linked=0,length=0;
    {native_trace::Scope status("gx.shader_binary_status");glGetProgramiv(program,GL_LINK_STATUS,&linked);if(!linked)return;
     glGetProgramiv(program,GL_PROGRAM_BINARY_LENGTH,&length);}
    if(length<=0 || length>(64<<20))return;
    if(!writer.can_accept(size_t(length))){++writer.skipped;return;}
    std::vector<char> data(static_cast<size_t>(length));GLsizei written=0;GLenum format=0;
    {native_trace::Scope extract("gx.shader_binary_extract");get_binary(program,length,&written,&format,data.data());}
    if(written<=0 || glGetError()!=GL_NO_ERROR)return;
    Header header{};std::memcpy(header.magic,"NWPBC001",8);header.key=value;header.format=uint32_t(format);header.length=uint32_t(written);
    static_assert(sizeof(Header)==24);data.resize(size_t(written));
    {native_trace::Scope enqueue("gx.shader_cache_enqueue");writer.enqueue(path(value),&header,std::move(data),stores);}
  }'''+s[b:]
assert 'NATIVE_SHADER_CACHE_IO_V1' in s
if s!=f.read_text():f.write_text(s)
print('Driver binary status/extraction timed separately; bounded filesystem jobs run off the GL owner and drain on shutdown.')
