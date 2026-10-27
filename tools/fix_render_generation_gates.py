"""Avoid rebuilding an unchanged GPU XF block; preserve upload/range semantics."""
from pathlib import Path
import argparse

MARKER = "NATIVE_GPU_XF_GENERATION_V1"
INCLUDE = '#include "runtime/native_perf_census.h" // NATIVE_XF_GATE_CENSUS_INCLUDE\n'
TAIL = "\n built_generation=s_native_gpu_xf_generation;\n built_snapshot=s_gpu_vertex_mode?s_gpu_vertex_snapshot:nullptr;\n built_generators=generators;"


def replacements():
    return {
        "static void NativeUploadGpuXf(){":
        "static void NativeUploadGpuXf(){\n native_perf_census::RenderAdd(native_perf_census::XfRequests);",
        "static NativeUploadRing s_gpu_xf_ring; // NATIVE_GPU_XF_RING_V1":
        "static uint64_t s_native_gpu_xf_generation=0; // " + MARKER + "\n"
        "static NativeUploadRing s_gpu_xf_ring; // NATIVE_GPU_XF_RING_V1",
        " static GLuint buffer=0;static NativeGpuXfBlock previous{};static bool have=false;":
        " static GLuint buffer=0;static NativeGpuXfBlock previous{};static bool have=false;\n"
        " // Render-thread sources only. Retain identity so allocator reuse cannot alias it.\n"
        " static const bool gate=std::getenv(\"NWII_NO_XF_GENERATION_GATE\")==nullptr;\n"
        " static uint64_t built_generation=0;\n"
        " static std::shared_ptr<DrawRaw> built_snapshot;\n"
        " static unsigned built_generators=0;\n"
        " const unsigned generators=std::min(8u,std::max(1u,unsigned(g_state.bp[0]&15u)));\n"
        " if(gate && have && ((!s_gpu_vertex_mode && previous.control[0]==0) ||\n"
        "    (s_gpu_vertex_mode && previous.control[0]!=0 &&\n"
        "     built_generation==s_native_gpu_xf_generation &&\n"
        "     built_snapshot==s_gpu_vertex_snapshot && built_generators==generators))){\n"
        "   native_perf_census::RenderAdd(native_perf_census::XfCached);return;\n"
        " }",
        "        int addr = cmd.reg;\n        bool touched_proj = false;":
        "        int addr = cmd.reg;\n"
        "        ++s_native_gpu_xf_generation; // Any XF command invalidates all dependent words.\n"
        "        bool touched_proj = false;",
    }


def restore(source: str) -> str:
    if MARKER not in source:
        if TAIL in source or 'static unsigned built_generators=0;' in source or INCLUDE in source:
            raise RuntimeError("Partial XF generation gate without its marker")
        return source
    if source.count(MARKER) != 1 or source.count(TAIL) != 1 or source.count(INCLUDE) != 1:
        raise RuntimeError("Partial or duplicated XF generation gate")
    for old, new in replacements().items():
        if source.count(new) != 1:
            raise RuntimeError(f"XF gate restore anchor count {source.count(new)}: {new!r}")
        source = source.replace(new, old, 1)
    return source.replace(TAIL, '', 1).replace(INCLUDE, '', 1)


def apply(source: str) -> str:
    if MARKER in source:
        canonical = restore(source)
        if apply(canonical) != source:
            raise RuntimeError("Non-canonical XF generation gate")
        return source
    source = restore(source)  # Reject partial staging before modifying anything.
    source = INCLUDE + source
    for old, new in replacements().items():
        if source.count(old) != 1:
            raise RuntimeError(f"XF gate anchor count {source.count(old)}: {old!r}")
        source = source.replace(old, new, 1)
    start = source.index("static void NativeUploadGpuXf(){")
    end = source.index("\n}\n", start)
    source = source[:end] + TAIL + source[end:]
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    port = args.port
    path = port / "build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp"
    old = path.read_text()
    new = restore(old) if args.restore else apply(old)
    if new != old:
        path.write_text(new)
    print("GPU XF generation gate restored." if args.restore else "GPU XF construction gated by renderer writes, retained snapshot identity and BP generator count.")


if __name__ == "__main__":
    main()

