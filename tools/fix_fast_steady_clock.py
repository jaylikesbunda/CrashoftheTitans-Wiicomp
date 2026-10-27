"""Link the verified10MHz QPC specialization; never change counter rate or epoch."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];cm=p/'build/nwii-full-source/CMakeLists.txt';s=cm.read_text();marker='# NATIVE_FAST_STEADY_CLOCK_V1'
if marker not in s:
    s+='\n'+marker+'\nif(MINGW)\n  target_sources(nwiiruntime PRIVATE "'+(p/'runtime-overlays/steady_clock_fast.cpp').as_posix()+'")\n  target_link_options(nwiiruntime PUBLIC "-Wl,--wrap,_ZNSt6chrono3_V212steady_clock3nowEv")\nendif()\n'
    cm.write_text(s)
print('Exact10MHz QPC clock specialization linked; other clocks retain standard implementation.')
