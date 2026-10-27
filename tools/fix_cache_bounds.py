"""NATIVE_CACHE_BOUNDS_V1 -- bound the renderer caches and publish their occupancy.

1. Shader program / source / alias caches and the uniform and projection caches (renderer_gl.cpp) had inserts and no
   eviction. A trim at the end of each shader insert evicts least-recently-used aliases (never the bound program), deletes
   programs with no alias left, and forgets their uniform and projection entries (GL reuses program ids).
2. TextureCache::evict_old selects the oldest entries in chunks instead of sorting the whole cache, no longer clears
   hot_keys, and backs off while nothing is old enough to evict. A pass evicts at most kTextureEvictPerPass (256)
   entries unless the cache is past the hard count or byte limit; evict_pending keeps the following misses evicting
   until the low-water mark, so no frame absorbs a whole sweep (NWII_NO_TEXTURE_EVICT_BUDGET=1 restores one full pass).
3. The F3 overlay's PC histogram keeps the top 16 rows with a partial sort.
4. F3/F4 report cache occupancy (performance_census.cache_occupancy in the JSON, a block at the top of the text).

Runs after fix_perf_census.py and fix_gl_program_shadow.py. Idempotent; every edit stores its original as base64 so
`--restore` returns the exact staged text. Pass --root to work on another copy of nWiiRuntime.
"""
from pathlib import Path
import argparse, base64, re

MARK = 'NATIVE_CACHE_BOUNDS_V1'
BLOCK = re.compile(r'/\* ' + MARK + r':(\w+) ([A-Za-z0-9+/=]*) \*/(.*?)/\* END_' + MARK + r':\1 \*/', re.S)
HEADER = 'include/runtime/native_cache_bounds.h'


def read(path):
    return path.read_bytes().decode('utf-8', 'surrogateescape')


def write(path, text):
    path.write_bytes(text.encode('utf-8', 'surrogateescape'))


def edit(text, tag, old, new):
    """Replace `old` (written with \\n) by a restorable block holding `new`; skip when already applied."""
    if f'/* {MARK}:{tag} ' in text:
        return text
    eol = '\r\n' if '\r\n' in text else '\n'
    old = old.replace('\n', eol)
    new = new.replace('\n', eol)
    assert text.count(old) == 1, (tag, text.count(old))
    packed = base64.b64encode(old.encode('utf-8', 'surrogateescape')).decode()
    return text.replace(old, f'/* {MARK}:{tag} {packed} */{new}/* END_{MARK}:{tag} */', 1)


def unblock(text):
    return BLOCK.sub(lambda m: base64.b64decode(m.group(2)).decode('utf-8', 'surrogateescape'), text)


def function_text(text, anchor):
    eol = '\r\n' if '\r\n' in text else '\n'
    start = text.index(anchor)
    end = text.index(eol + '}' + eol, start) + len(eol) + 1 + len(eol)
    return text[start:end].replace(eol, '\n')


# ---------------------------------------------------------------- renderer_gl.cpp
UNIFORM_NEW = r'''static bool NativeUniformChanged(GLuint prog, GLint loc, const void *data, size_t bytes) {
  // NATIVE_UNIFORM_LOOKUP_CACHE_V1: unordered-map references survive rehash.
  auto& cache=s_native_uniform_cache;auto* hot=s_native_uniform_hot;
  const uint64_t key=(uint64_t(prog)<<32)|uint32_t(loc);
  auto& slot=hot[(uint32_t(prog)*33u ^ uint32_t(loc))&127u];
  if(slot.value==nullptr || slot.key!=key){
    // Hard bound (kUniformEntriesMax). Dropping entries only costs a re-upload: an absent entry never matches.
    if(cache.size()>=65536 && cache.find(key)==cache.end()){cache.clear();for(auto& h:s_native_uniform_hot)h=NativeUniformHot{};++s_native_uniform_clears;}
    slot.value=&cache[key];slot.key=key;}
  auto& entry=*slot.value;
  if (entry.size() == bytes && std::memcmp(entry.data(), data, bytes) == 0) return false;
  entry.assign(static_cast<const uint8_t *>(data), static_cast<const uint8_t *>(data) + bytes);
  return true;
}
'''

TRIM_NEW = r'''// NATIVE_CACHE_BOUNDS_V1: shader cache bound. Render thread only, like every other user of these maps.
static native_cache_bounds::SamplerHistory s_native_sampler_history;
static GLuint s_native_bound_program=0;
static uint64_t s_native_live_programs=0;
static void NativeRestoreSamplerHistory(uint64_t hash,GLShader& shader){
  const auto parked=s_native_sampler_history.find(hash);
  if(parked==s_native_sampler_history.end())return;
  static_assert(sizeof shader.sampler_values==sizeof(parked->second));
  std::memcpy(shader.sampler_values,parked->second.data(),sizeof shader.sampler_values);
  s_native_sampler_history.erase(parked);
}
static void NativeTrimShaderCaches(){
  using namespace native_cache_stats;
  if(s_shader_cache.size()>native_cache_bounds::kShaderAliasHigh || s_native_live_programs>native_cache_bounds::kShaderProgramHigh){
    native_trace::Scope trace_trim("gx.shader_trim");
    const auto result=native_cache_bounds::TrimShaders(s_shader_cache,s_shader_sources,
      s_native_sampler_history,size_t(s_native_live_programs),
      s_active_shader.id,s_native_bound_program,[&](uint32_t id){
        native_cache_bounds::ForgetUniformProgram(s_native_uniform_cache,s_native_uniform_hot,id);
        s_native_projection_cache.erase(GLuint(id));
        const GLuint program=GLuint(id);glDeleteProgram(program);--s_native_live_programs;
      });
    Add(c.shader_trims);Add(c.shader_alias_evictions,result.aliases);Add(c.shader_program_evictions,result.programs);
  }
  Set(c.shader_programs,s_native_live_programs);Set(c.shader_aliases,s_shader_cache.size());Set(c.shader_sources,s_shader_sources.size());
  Set(c.uniform_entries,s_native_uniform_cache.size());Set(c.projection_entries,s_native_projection_cache.size());Set(c.uniform_clears,s_native_uniform_clears);
}
'''


def patch_renderer(text):
    text = edit(text, 'inc', '#include "runtime/native_shader_cache_io.h"\n',
                '#include "runtime/native_shader_cache_io.h"\n#include "runtime/native_cache_bounds.h"\n')
    text = edit(text, 'struct',
                '  GLint uLightCol, uLightPos, uLightDir, uLightCosAtt, uLightDistAtt;\n};\nstatic std::unordered_map<uint64_t, GLShader> s_shader_cache;\n',
                '  GLint uLightCol, uLightPos, uLightDir, uLightCosAtt, uLightDistAtt;\n  uint64_t native_last_used=0; // NATIVE_CACHE_BOUNDS_V1: shader-lookup tick of the last use\n};\nstatic std::unordered_map<uint64_t, GLShader> s_shader_cache;\n')
    text = edit(text, 'uniforms',
                'static std::unordered_map<std::string,GLShader> s_shader_sources;\nstatic uint64_t s_native_shader_program_count=0;\n',
                'static std::unordered_map<std::string,GLShader> s_shader_sources;\nstatic uint64_t s_native_shader_program_count=0;\n'
                'static uint64_t s_native_shader_tick=0;\n'
                'static std::unordered_map<uint64_t, std::vector<uint8_t>> s_native_uniform_cache; // NATIVE_CACHE_BOUNDS_V1: keyed (program<<32|location)\n'
                'struct NativeUniformHot { uint64_t key=0; std::vector<uint8_t>* value=nullptr; };\n'
                'static NativeUniformHot s_native_uniform_hot[128];\n'
                'static uint64_t s_native_uniform_clears=0;\n')
    text = edit(text, 'projection',
                'static bool NativeProjectionUploadNeeded(bool bound,GLuint program,const float* p){\n static std::unordered_map<GLuint,std::vector<uint8_t>> cache;\n',
                'static std::unordered_map<GLuint,std::vector<uint8_t>> s_native_projection_cache; // NATIVE_CACHE_BOUNDS_V1: forgotten with its program\n'
                'static bool NativeProjectionUploadNeeded(bool bound,GLuint program,const float* p){\n auto& cache=s_native_projection_cache;\n')
    if f'/* {MARK}:uniform ' not in text:
        old = function_text(text, 'static bool NativeUniformChanged(')
        text = edit(text, 'uniform', old, UNIFORM_NEW)
    text = edit(text, 'trim', 'static void SetupDrawState(const GXCommand &cmd) {\n', TRIM_NEW + 'static void SetupDrawState(const GXCommand &cmd) {\n')
    text = edit(text, 'hit',
                '  if (it != s_shader_cache.end()) {\n    s_active_shader = it->second;\n',
                '  if (it != s_shader_cache.end()) {\n    it->second.native_last_used=++s_native_shader_tick; // NATIVE_CACHE_BOUNDS_V1\n    s_active_shader = it->second;\n')
    text = edit(text, 'reuse',
                '      s_active_shader=source_hit->second;s_shader_cache[hash]=s_active_shader;\n',
                '      s_active_shader=source_hit->second;s_active_shader.native_last_used=++s_native_shader_tick;\n'
                '      NativeRestoreSamplerHistory(hash,s_active_shader);s_shader_cache[hash]=s_active_shader;NativeTrimShaderCaches();\n')
    text = edit(text, 'compile',
                '    s_shader_sources[source_key]=sh;\n    s_shader_cache[hash] = sh;\n    s_active_shader = sh;\n',
                '    ++s_native_live_programs;sh.native_last_used=++s_native_shader_tick;\n'
                '    s_shader_sources[source_key]=sh; // the source copy keeps clean sampler history; aliases own theirs\n'
                '    NativeRestoreSamplerHistory(hash,sh);\n'
                '    s_shader_cache[hash] = sh;\n    s_active_shader = sh;\n    NativeTrimShaderCaches();\n')
    text = edit(text, 'bound', ' s_native_program = s_active_shader.id; }\n',
                ' s_native_program = s_active_shader.id; s_native_bound_program = s_native_program; }\n')
    return text


# ---------------------------------------------------------------- texture cache
EVICT_OLD_NEW = r'''void TextureCache::evict_old() {
    // NATIVE_CACHE_BOUNDS_V1: same trigger, same low-water marks and in-use window as before, but the oldest entries are
    // selected in chunks (no full sort), hot_keys survive for live entries, and a pass that found nothing old enough
    // is not repeated until the window has moved.
    // NATIVE_TEXTURE_EVICT_BUDGET_V1: a pass evicts at most kTextureEvictPerPass entries unless the cache is past a hard
    // limit; evict_pending keeps the following misses evicting until the low-water mark. NWII_NO_TEXTURE_EVICT_BUDGET=1
    // restores the single unbounded pass.
    if (tick < evict_retry_tick) return;
    native_cache_stats::Add(native_cache_stats::c.texture_eviction_passes);
    static const bool budgeted = std::getenv("NWII_NO_TEXTURE_EVICT_BUDGET") == nullptr;
    const size_t target_bytes = (640ull << 20) * 7 / 10;
    const size_t target_count = 4200;
    const auto over = [&] { return total_bytes > target_bytes || cache.size() > target_count; };
    const bool hard = cache.size() > native_cache_bounds::kTextureHardCount || total_bytes > native_cache_bounds::kTextureHardBytes;
    const size_t budget = budgeted && !hard ? native_cache_bounds::kTextureEvictPerPass : SIZE_MAX;
    evict_scratch.clear();
    for (const auto& pair : cache)
        if (pair.second.stamp + native_cache_bounds::kTextureProtectTicks <= tick) // not in use by the draws being set up now
            evict_scratch.emplace_back(pair.second.stamp, pair.first);
    size_t removed = 0;
    const size_t excess = cache.size() > target_count ? cache.size() - target_count : 0;
    const size_t evicted = native_cache_bounds::EvictOldest(evict_scratch, std::min(excess, budget),
        [&] { return !over() || removed >= budget; },
        [&](const TextureKey& key) {
            ++removed;
            auto it = cache.find(key);
            if (it == cache.end()) return;
            mip_ready.erase(it->second.tex); NativeTextureDelete(1, &it->second.tex);
            total_bytes -= std::min<size_t>(total_bytes, it->second.bytes);
            cache.erase(it);
        });
    const bool capped = removed >= budget && over();
    evict_pending = capped;
    if (over() && !capped) {
        evict_retry_tick = tick + native_cache_bounds::kTextureProtectTicks;
        native_cache_stats::Add(native_cache_stats::c.texture_eviction_blocked);
    } else evict_retry_tick = 0;
    if (evicted) {
        native_cache_stats::Add(native_cache_stats::c.texture_evictions, evicted);
        prune_hot_keys();
    }
    note_cache_size();
}

void TextureCache::prune_hot_keys() {
    // A hot key whose entry was evicted can only fall back to the hashed lookup, so drop exactly those.
    for (auto it = hot_keys.begin(); it != hot_keys.end();) {
        if (cache.find(it->second) == cache.end()) it = hot_keys.erase(it); else ++it;
    }
    native_cache_stats::Add(native_cache_stats::c.texture_hot_prunes);
}

void TextureCache::note_cache_size() {
    if (hot_keys.size() > cache.size() * 2 + native_cache_bounds::kTextureHotSlack) {
        prune_hot_keys();
        if (hot_keys.size() > cache.size() * 2 + native_cache_bounds::kTextureHotSlack) hot_keys.clear(); // accelerator only
    }
    native_cache_stats::Set(native_cache_stats::c.texture_entries, cache.size());
    native_cache_stats::Set(native_cache_stats::c.texture_bytes, total_bytes);
    native_cache_stats::Set(native_cache_stats::c.texture_hot_keys, hot_keys.size());
}
'''


def patch_texture_cpp(text):
    text = edit(text, 'inc', '#include "runtime/texture_cache_gl.h"\n',
                '#include "runtime/texture_cache_gl.h"\n#include "runtime/native_cache_bounds.h"\n')
    if f'/* {MARK}:evict ' not in text:
        old = function_text(text, 'void TextureCache::evict_old() {')
        text = edit(text, 'evict', old, EVICT_OLD_NEW)
    text = edit(text, 'clear', '    cache.clear();hot_keys.clear();\n    total_bytes = 0;\n',
                '    cache.clear();hot_keys.clear();\n    total_bytes = 0;\n    evict_retry_tick = 0; evict_pending = false; note_cache_size(); // NATIVE_CACHE_BOUNDS_V1\n')
    text = edit(text, 'trigger', '    if (cache.size() > 6000 || total_bytes > (640ull << 20))\n        evict_old();\n',
                '    if (cache.size() > 6000 || total_bytes > (640ull << 20) || evict_pending) // NATIVE_TEXTURE_EVICT_BUDGET_V1\n        evict_old();\n')
    anchor = 'entry.bytes+=size+palette_bytes;total_bytes+=size+palette_bytes;hot_keys[metadata]=key;}\n'
    text = edit(text, 'insert', anchor, anchor + '    note_cache_size(); // NATIVE_CACHE_BOUNDS_V1\n')
    return text


def patch_texture_h(text):
    return edit(text, 'members', '    void evict_old();\n',
                '    void evict_old();\n'
                '    // NATIVE_CACHE_BOUNDS_V1\n'
                '    uint64_t evict_retry_tick = 0;\n'
                '    bool evict_pending = false; // NATIVE_TEXTURE_EVICT_BUDGET_V1: a capped pass left entries to evict\n'
                '    std::vector<std::pair<uint64_t, TextureKey>> evict_scratch;\n'
                '    void prune_hot_keys();\n'
                '    void note_cache_size();\n')


# ---------------------------------------------------------------- census export
def patch_export(text):
    text = edit(text, 'inc', '#include "runtime/native_perf_census.h"\n',
                '#include "runtime/native_perf_census.h"\n#include "runtime/native_cache_bounds.h"\n')
    text = edit(text, 'sig', 'inline std::vector<PcRow> PcRows(PcSlot* slots) {\n',
                'inline std::vector<PcRow> PcRows(PcSlot* slots,size_t limit=SIZE_MAX) {\n')
    text = edit(text, 'sort',
                ' std::sort(rows.begin(),rows.end(),[](const PcRow&a,const PcRow&b){return a.calls>b.calls || (a.calls==b.calls && a.pc<b.pc);});\n return rows;\n',
                ' // NATIVE_CACHE_BOUNDS_V1: the order is total (calls, then pc), so a partial sort returns the same first `limit` rows.\n'
                ' const auto order=[](const PcRow&a,const PcRow&b){return a.calls>b.calls || (a.calls==b.calls && a.pc<b.pc);};\n'
                ' if(limit<rows.size()){std::partial_sort(rows.begin(),rows.begin()+limit,rows.end(),order);rows.resize(limit);}\n'
                ' else std::sort(rows.begin(),rows.end(),order);\n return rows;\n')
    text = edit(text, 'json', ' s << "{\\"enabled\\":"',
                ' s << "{\\"cache_occupancy\\":" << native_cache_stats::Json() << ",\\"enabled\\":"')
    text = edit(text, 'disabled', ' if(!enabled)return "Performance census: disabled (launch with NWII_PERF_CENSUS=1 to record independently of F3).\\n";\n',
                ' if(!enabled)return native_cache_stats::Text()+"Performance census: disabled (launch with NWII_PERF_CENSUS=1 to record independently of F3).\\n";\n')
    text = edit(text, 'text', ' std::ostringstream s;\n s << "Performance census: "',
                ' std::ostringstream s;\n s << native_cache_stats::Text();\n s << "Performance census: "')
    text = edit(text, 'top', 'auto rows=PcRows(pair.second);\n', 'auto rows=PcRows(pair.second,16);\n')
    return text


FILES = (
    ('src/hle/gx/renderer_gl.cpp', patch_renderer),
    ('src/hle/texture_cache_gl.cpp', patch_texture_cpp),
    ('include/runtime/texture_cache_gl.h', patch_texture_h),
    ('include/runtime/native_perf_census_export.h', patch_export),
)


def apply(port, rt):
    shared = (port / 'runtime-overlays/native_cache_bounds.h').read_text()
    target = rt / HEADER
    if not target.exists() or target.read_text() != shared:
        target.write_text(shared)
    for rel, patch in FILES:
        path = rt / rel
        old = read(path)
        new = patch(old)
        if new != old:
            write(path, new)
    print('Renderer caches bounded; occupancy exported to F3/F4.')


def restore(port, rt):
    for rel, _ in FILES:
        path = rt / rel
        old = read(path)
        new = unblock(old)
        if new != old:
            write(path, new)
    (rt / HEADER).unlink(missing_ok=True)
    print('Cache bounds removed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    port = Path(__file__).resolve().parents[1]
    rt = args.root or port / 'build/nwii-full-source/nWiiRuntime'
    (restore if args.restore else apply)(port, rt)
