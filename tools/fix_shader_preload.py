"""NATIVE_SHADER_PRELOAD_V1 -- read cached program binaries off the render thread and link them a few at a time.

Measured: a shader's first use spent 11-14 ms in gx.shader_binary_load on the render thread (file read plus
glProgramBinary and the link query), which stalled the guest thread on that frame.

1. A reader thread (runtime-overlays/native_shader_preload.h) scans the program cache, newest file first, and
   validates each file (magic, key equal to the file name, exact length). It holds owned bytes only and never
   calls GL. Bounds: 256 files in total and 64 MB queued.
2. RendererGL::Render calls NativeShaderPreloadPump once per submission. It turns queued binaries into linked
   programs on the render thread, stopping when the 2 ms budget (NWII_SHADER_PRELOAD_BUDGET_US) is spent, and
   keeps at most 128 linked programs waiting for their first use.
3. CompileShader claims a waiting program by the same FNV key before it reads the file. A key that is not
   waiting takes today's on-demand path unchanged, and a binary the driver rejects falls back to compile/link.

NWII_NO_SHADER_PRELOAD=1 disables the feature; NWII_NO_PROGRAM_CACHE=1 disables the cache it reads.
Anchors are limited to the shader-cache code. The pump takes 2 ms per submission; after a 20 ms gap between submissions (idle
render thread) it takes NWII_SHADER_PRELOAD_SLACK_US (default 6000, 0 keeps 2 ms) while binaries are still queued. Idempotent; every edit stores its original as base64 so
`--restore` returns the exact staged text. Pass --root to work on another copy of nWiiRuntime.
"""
from pathlib import Path
import argparse, base64, re

MARK = 'NATIVE_SHADER_PRELOAD_V1'
BLOCK = re.compile(r'/\* ' + MARK + r':(\w+) ([A-Za-z0-9+/=]*) \*/(.*?)/\* END_' + MARK + r':\1 \*/', re.S)
HEADER = 'include/runtime/native_shader_preload.h'
RENDERER = 'src/hle/gx/renderer_gl.cpp'

PUMP = r'''// NATIVE_SHADER_PRELOAD_V1: see tools/fix_shader_preload.py. Only the render thread calls GL here.
namespace {
struct NativeShaderPreloadState {
  native_shader_preload::Loader loader;
  std::unordered_map<uint64_t,GLuint> ready; // linked ahead of first use; CompileShader claims them by key
  bool started=false;
  std::chrono::steady_clock::time_point last_pump{};
  uint64_t created=0,claimed=0,rejected=0,slack_pumps=0;
};
NativeShaderPreloadState& NativeShaderPreload(){static NativeShaderPreloadState state;return state;}
}
static constexpr size_t kNativeShaderPreloadReadyMax=128;
static void NativeShaderPreloadPump(){
  static const bool enabled=std::getenv("NWII_NO_SHADER_PRELOAD")==nullptr;
  if(!enabled)return;
  auto& cache=NativeProgramBinaryCache();
  if(!cache.enabled)return;
  auto& state=NativeShaderPreload();
  if(!state.started){state.started=true;state.loader.start(cache.directory);}
  static const long budget_us=[]{if(const char* e=std::getenv("NWII_SHADER_PRELOAD_BUDGET_US")){const long v=std::strtol(e,nullptr,10);if(v>0)return v;}return 2000L;}();
  // Slack: the render thread went 20 ms or more between submissions (menus, loading screens, a light frame), so this pump
  // runs in the idle time and may take a larger budget while binaries are still queued. Gameplay keeps the 2 ms budget.
  static const long slack_us=[]{if(const char* e=std::getenv("NWII_SHADER_PRELOAD_SLACK_US")){const long v=std::strtol(e,nullptr,10);if(v>=0)return v;}return 6000L;}();
  const auto began=std::chrono::steady_clock::now();
  const bool slack=state.last_pump.time_since_epoch().count()!=0 && began-state.last_pump>=std::chrono::milliseconds(20) && !state.loader.drained();
  state.last_pump=began;
  const auto limit=std::chrono::microseconds(slack && slack_us>budget_us ? slack_us : budget_us);
  if(slack && slack_us>budget_us)++state.slack_pumps;
  native_shader_preload::Entry entry;
  while(state.ready.size()<kNativeShaderPreloadReadyMax && state.loader.take(entry)){
    native_trace::Scope trace_bind("gx.shader_preload_bind"); // the only GL work here: one glProgramBinary and its link query
    while(glGetError()!=GL_NO_ERROR){}
    const GLuint program=glCreateProgram();
    cache.set_binary(program,GLenum(entry.format),entry.data.data(),GLsizei(entry.data.size()));
    GLint linked=0;glGetProgramiv(program,GL_LINK_STATUS,&linked);
    if(!linked || glGetError()!=GL_NO_ERROR){glDeleteProgram(program);++state.rejected;}
    else if(!state.ready.emplace(entry.key,program).second){glDeleteProgram(program);}
    else ++state.created;
    if(std::chrono::steady_clock::now()-began>=limit)break;
  }
}
static GLuint NativeShaderPreloadTake(uint64_t key){
  auto& state=NativeShaderPreload();
  if(state.ready.empty())return 0;
  const auto it=state.ready.find(key);
  if(it==state.ready.end())return 0;
  const GLuint program=it->second;
  state.ready.erase(it);++state.claimed;
  return program;
}
'''

TAKE = ('    if(const GLuint preloaded=NativeShaderPreloadTake(cache_key)){'
        'native_hud::work_shader(1);native_trace::mark("shader.preload_hit");return preloaded;} // NATIVE_SHADER_PRELOAD_V1\n')
RENDER = '    NativeShaderPreloadPump(); // NATIVE_SHADER_PRELOAD_V1: bounded binary loads between draw submissions\n'


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


def patch_renderer(text):
    text = edit(text, 'inc', '#include "runtime/native_shader_cache_io.h"\n',
                '#include "runtime/native_shader_cache_io.h"\n#include "runtime/native_shader_preload.h" // NATIVE_SHADER_PRELOAD_V1\n')
    text = edit(text, 'pump', 'NativeProgramBinaryCache() {static NativeProgramCache cache;return cache;}\n',
                'NativeProgramBinaryCache() {static NativeProgramCache cache;return cache;}\n' + PUMP)
    text = edit(text, 'take', '    cache_key=cache.key(vs,fs);\n', '    cache_key=cache.key(vs,fs);\n' + TAKE)
    text = edit(text, 'render', '    s_compiles_this_frame = 0; // reset the per-frame shader compile budget\n',
                '    s_compiles_this_frame = 0; // reset the per-frame shader compile budget\n' + RENDER)
    return text


def apply(port, rt):
    shared = (port / 'runtime-overlays/native_shader_preload.h').read_bytes()
    target = rt / HEADER
    if not target.exists() or target.read_bytes() != shared:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(shared)
    path = rt / RENDERER
    old = read(path)
    new = patch_renderer(old)
    if new != old:
        write(path, new)
    print('Cached program binaries are read off the render thread and linked in a bounded pump; NWII_NO_SHADER_PRELOAD=1 disables.')


def restore(port, rt):
    path = rt / RENDERER
    old = read(path)
    new = unblock(old)
    if new != old:
        write(path, new)
    (rt / HEADER).unlink(missing_ok=True)
    print('Shader preload removed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    port = Path(__file__).resolve().parents[1]
    rt = args.root or port / 'build/nwii-full-source/nWiiRuntime'
    (restore if args.restore else apply)(port, rt)
