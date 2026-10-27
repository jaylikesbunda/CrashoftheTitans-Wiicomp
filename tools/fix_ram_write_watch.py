"""Dirty-page tracking for texture sources: skip the full-texture memcmp when the OS says no page of the texture was written.

Capture frame-cost-20261006-180351-850400, renderer thread: TextureCache::get_texture is 382 of 3,371 samples (11.3%) and
frame-trace texture.lookup_hash is 1.9 ms/frame. Every lookup compared the whole source texture (up to 4 MB) with a stored copy
to catch guest rewrites (NATIVE_EXACT_TEXTURE_HOT_V1). Guest RAM now comes from VirtualAlloc with MEM_WRITE_WATCH
(runtime-overlays/ram_write_watch.cpp): the OS records page writes with no cost on guest loads or stores. A lookup asks
GetWriteWatch (resetting the OS bits) for the texture's pages, stamps the dirty ones with a new global generation, and compares the newest
page generation in the range with the generation at which this entry was last verified:
  newest <= entry.verified_gen  -> no page of the source was written since verification: reuse (the palette is still compared);
  otherwise                     -> compare the bytes exactly as before; if equal, the entry is re-verified at the current generation.
Entries are verified at the generation sampled BEFORE their source is read, so a write that races with decoding is seen next time.
If tracking is unavailable, per call (native_ram_newest_write returns UINT64_MAX) or globally (NWII_NO_WRITE_WATCH=1), the original
full comparison runs, so correctness never depends on the tracker.

Requires fix_static_ram_base.py first (it rewrites the MMU member block this stage replaces). Idempotent.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
NL=chr(10)

f=rt/'include/runtime/cpu_context.h';s=f.read_text()
if 'NATIVE_RAM_WRITE_WATCH_V1' not in s:
    lines=s.split(NL)
    marker=next(i for i,l in enumerate(lines) if 'NATIVE_STATIC_RAM_V1: see tools/fix_static_ram_base.py' in l)
    els=next(i for i in range(marker,len(lines)) if lines[i].strip()=='#else')
    end=next(i for i in range(els,len(lines)) if lines[i].strip()=='#endif')
    want=['std::vector<uint8_t> mem1;','std::vector<uint8_t> mem2;','','MMU() {','mem1.resize(24 * 1024 * 1024 + 8, 0);','mem2.resize(64 * 1024 * 1024 + 8, 0);','}']
    assert [x.strip() for x in lines[els+1:els+1+len(want)]]==want,'MMU #else block changed: requalify'
    new=['  // NATIVE_RAM_WRITE_WATCH_V1: guest RAM with OS page-write tracking (see tools/fix_ram_write_watch.py).',
         '  NativeWatchedRam mem1;','  NativeWatchedRam mem2;','','  MMU() {','    mem1.allocate(24 * 1024 * 1024 + 8);','    mem2.allocate(64 * 1024 * 1024 + 8);','  }']
    lines[els+1:els+1+len(want)]=new
    s=NL.join(lines)
    helper='''// NATIVE_RAM_WRITE_WATCH_V1: see tools/fix_ram_write_watch.py and runtime-overlays/ram_write_watch.cpp.
// Header-only so every translation unit that uses the MMU (including stand-alone test fixtures) links without the overlay.
extern "C" {
__declspec(dllimport) void* __stdcall VirtualAlloc(void*, size_t, unsigned long, unsigned long);
__declspec(dllimport) int __stdcall VirtualFree(void*, size_t, unsigned long);
}
inline uint8_t* native_ram_allocate(size_t bytes, bool* write_watch) {
  void* memory = VirtualAlloc(nullptr, bytes, 0x1000 | 0x2000 | 0x200000, 0x04); // MEM_COMMIT | MEM_RESERVE | MEM_WRITE_WATCH, PAGE_READWRITE
  const bool watched = memory != nullptr;
  if (!memory) memory = VirtualAlloc(nullptr, bytes, 0x1000 | 0x2000, 0x04);
  if (!memory) std::abort();
  if (write_watch) *write_watch = watched;
  return static_cast<uint8_t*>(memory);
}
inline void native_ram_release(uint8_t* memory) { if (memory) VirtualFree(memory, 0, 0x8000); } // MEM_RELEASE
uint64_t native_ram_generation();
uint64_t native_ram_newest_write(const uint8_t* base, size_t bank_bytes, size_t offset, size_t bytes);
struct NativeWatchedRam {
  uint8_t* base_ = nullptr; size_t bytes_ = 0;
  NativeWatchedRam() = default;
  NativeWatchedRam(const NativeWatchedRam&) = delete;
  NativeWatchedRam& operator=(const NativeWatchedRam&) = delete;
  ~NativeWatchedRam() { native_ram_release(base_); }
  void allocate(size_t bytes) { bool watched = false; base_ = native_ram_allocate(bytes, &watched); bytes_ = bytes; }
  uint8_t* data() const { return base_; }
  size_t size() const { return bytes_; }
  uint8_t& operator[](size_t index) const { return base_[index]; }
  uint8_t* begin() const { return base_; }
  uint8_t* end() const { return base_ + bytes_; }
};
'''
    a=s.index('struct MMU {')
    s=s[:a]+helper+s[a:]
    f.write_text(s)

f=rt/'include/runtime/texture_cache_gl.h';s=f.read_text()
old='struct Entry { GLuint tex; uint32_t bytes; uint64_t stamp; std::vector<uint8_t> source,palette; };'
if 'verified_gen' not in s:
    assert s.count(old)==1
    f.write_text(s.replace(old,'struct Entry { GLuint tex; uint32_t bytes; uint64_t stamp; std::vector<uint8_t> source,palette; uint64_t verified_gen=0; /* NATIVE_RAM_WRITE_WATCH_V1 */ };'))

f=rt/'src/hle/texture_cache_gl.cpp';s=f.read_text()
if 'NATIVE_RAM_WRITE_WATCH_V1' not in s:
    old_lambda='''    auto unchanged=[&](const Entry& entry){return span && entry.source.size()==size && entry.palette.size()==palette_bytes &&
       (!size || std::memcmp(source,entry.source.data(),size)==0) && (!palette_bytes || std::memcmp(palette,entry.palette.data(),palette_bytes)==0);};'''
    assert s.count(old_lambda)==1
    new_lambda='''    // NATIVE_RAM_WRITE_WATCH_V1: OS page-write tracking replaces the per-lookup full compare when no page of the source was written.
    static const bool write_watch=std::getenv("NWII_NO_WRITE_WATCH")==nullptr;
    const uint8_t* ram_base=nullptr;size_t ram_bank_bytes=0,ram_offset=0;
    if(span){
        if(physical<ctx.mmu.mem1.size()){ram_base=ctx.mmu.mem1.data();ram_bank_bytes=ctx.mmu.mem1.size();ram_offset=physical;}
        else{ram_base=ctx.mmu.mem2.data();ram_bank_bytes=ctx.mmu.mem2.size();ram_offset=physical-0x10000000u;}
    }
    auto unchanged=[&](Entry& entry){
       if(!(span && entry.source.size()==size && entry.palette.size()==palette_bytes))return false;
       if(palette_bytes && std::memcmp(palette,entry.palette.data(),palette_bytes)!=0)return false;
       if(!size)return true;
       if(write_watch){
           const uint64_t newest=native_ram_newest_write(ram_base,ram_bank_bytes,ram_offset,size);
           if(newest!=UINT64_MAX){
               if(entry.verified_gen && newest<=entry.verified_gen){native_trace::mark("texture.write_watch_clean");return true;}
               const uint64_t generation=native_ram_generation(); // sampled before the bytes are read
               if(std::memcmp(source,entry.source.data(),size)!=0)return false;
               entry.verified_gen=generation;return true;
           }
       }
       return std::memcmp(source,entry.source.data(),size)==0;};'''
    s=s.replace(old_lambda,new_lambda)
    old_decode='    Image img = decode_texture(ctx, stage);\n    GLuint tex;'
    assert s.count(old_decode)==1
    s=s.replace(old_decode,'''    uint64_t generation_before=0; // NATIVE_RAM_WRITE_WATCH_V1: sampled before the source is read
    if(write_watch && span && native_ram_newest_write(ram_base,ram_bank_bytes,ram_offset,size)!=UINT64_MAX)generation_before=native_ram_generation();
    Image img = decode_texture(ctx, stage);
    GLuint tex;''')
    old_assign='entry.source.assign(source,source+size);if(palette_bytes)entry.palette.assign(palette,palette+palette_bytes);'
    assert s.count(old_assign)==1
    s=s.replace(old_assign,old_assign+'entry.verified_gen=generation_before;')
    f.write_text(s)

cm=p/'build/nwii-full-source/CMakeLists.txt';t=cm.read_text()
if 'NATIVE_RAM_WRITE_WATCH_V1' not in t:
    t+='\n# NATIVE_RAM_WRITE_WATCH_V1\nif(MINGW)\n  target_sources(nwiiruntime PRIVATE "'+(p/'runtime-overlays/ram_write_watch.cpp').as_posix()+'")\nendif()\n'
    cm.write_text(t)

ov=p/'runtime-overlays/static_ram_base.cpp';s=ov.read_text()
if 'MEM_WRITE_WATCH' not in s:
    old='VirtualAlloc(reinterpret_cast<void*>(base), bytes, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);'
    assert s.count(old)==1
    ov.write_text(s.replace(old,'VirtualAlloc(reinterpret_cast<void*>(base), bytes, MEM_RESERVE | MEM_COMMIT | MEM_WRITE_WATCH, PAGE_READWRITE);'))
print('RAM write-watch tracking installed for texture sources')
