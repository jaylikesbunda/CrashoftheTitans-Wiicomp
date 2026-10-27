"""Link the large-block operator new cache into CrashNative (see runtime-overlays/large_block_cache.cpp)."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];cm=p/'build/nwii-full-source/CMakeLists.txt';s=cm.read_text();marker='# NATIVE_LARGE_BLOCK_CACHE_V1'
if marker not in s:
    s+='\n'+marker+'\ntarget_sources(CrashNative PRIVATE "'+(p/'runtime-overlays/large_block_cache.cpp').as_posix()+'")\n'
    cm.write_text(s)
print('Large-block allocation cache linked into CrashNative.')
