"""Keep exact uniform values; avoid repeated hash-map searches within draw setup."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]
f=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp';s=f.read_text()
if 'NATIVE_UNIFORM_LOOKUP_CACHE_V1' not in s:
    baseline=p/'runtime-overlays/uniform-lookup-before.cpp'
    if not baseline.exists():baseline.write_text(s)
    a=s.index('static bool NativeUniformChanged(');b=s.index('\n}\n',a)+2
    new='''static bool NativeUniformChanged(GLuint prog, GLint loc, const void *data, size_t bytes) {
  // NATIVE_UNIFORM_LOOKUP_CACHE_V1: unordered-map references survive rehash.
  static std::unordered_map<uint64_t, std::vector<uint8_t>> cache;
  struct Hot { uint64_t key=0; std::vector<uint8_t>* value=nullptr; };
  static Hot hot[128];
  const uint64_t key=(uint64_t(prog)<<32)|uint32_t(loc);
  auto& slot=hot[(uint32_t(prog)*33u ^ uint32_t(loc))&127u];
  if(slot.value==nullptr || slot.key!=key){slot.value=&cache[key];slot.key=key;}
  auto& entry=*slot.value;
  if (entry.size() == bytes && std::memcmp(entry.data(), data, bytes) == 0) return false;
  entry.assign(static_cast<const uint8_t *>(data), static_cast<const uint8_t *>(data) + bytes);
  return true;
}'''
    s=s[:a]+new+s[b:];f.write_text(s)
print('Exact uniform comparisons retain values while caching stable lookup references.')
