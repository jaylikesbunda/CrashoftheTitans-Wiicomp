"""GX state census and draw-state picker.

NWII_STATE_CENSUS=<file|1> records, at every batch flush, a signature of the draw state (GEN_MODE, indirect setup, TEV order and stages, konst
selects, texture setup including sampler filter / wrap / LOD, blend/z/alpha, XF texgen registers, the raw texture-coordinate range of set 0)
and writes a report at exit. Full serialization must not run during gameplay:
the 6500-state capture took 115-131 ms per report, and 240 flushes is about one frame.

The picker (overlay keys F5/F6/F7, overlay on with F3) steps through the states drawn in the last second, busiest first, and F7 cycles off ->
hide the selected state -> draw only the selected state. The overlay prints the selected state's details, so "this effect vanishes when
state 7 of 23 is hidden" identifies a draw exactly. Costs nothing unless one of them is on.

The hook is one call at the top of FlushBatch plus a forward declaration at the head of the file; the code lives at the end of the file
(runtime-overlays/state_census_body.inc) because fix_efb_probe.py rewrites everything between its marker and FlushBatch each pass. Idempotent.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
r = p / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
MARK = 'NATIVE_STATE_CENSUS_V1'
V2 = '// NATIVE_STATE_CENSUS_V2 (tools/fix_state_census.py)'
s = r.read_text(errors='surrogateescape')

# forward declaration (the first version declared it void)
s = s.replace('static void NativeStateCensus(); } // ' + MARK, 'static bool NativeStateCensus(); } // ' + MARK)
decl = 'namespace nwii::runtime::gx { static bool NativeStateCensus(); } // ' + MARK + '\n'
if decl not in s:
    s = s.replace('#include <cstdint>\n', '#include <cstdint>\n' + decl, 1) if s.startswith('#include <cstdint>\n') else '#include <cstdint>\n' + decl + s

# hook: the picker may ask for the batch not to be drawn
old_hook = '  NativeStateCensus(); // ' + MARK + '\n'
new_hook = ('  if (NativeStateCensus()) { s_batch.clear(); s_gpu_spans.clear(); s_gpu_span_vertices = 0; s_indices.clear(); return; } // ' + MARK +
            ': the draw-state picker hid this batch\n')
if old_hook in s:
    s = s.replace(old_hook, new_hook, 1)
elif new_hook not in s:
    anchor = '  if ((NativeBatchSize()==0))\n    return;\n'
    assert s.count(anchor) == 1, s.count(anchor)
    s = s.replace(anchor, anchor + new_hook, 1)

# body: replace whatever census body is at the end of the file with the current one
body = (p / 'runtime-overlays/state_census_body.inc').read_text()
v1 = '// NATIVE_STATE_CENSUS_V1 (tools/fix_state_census.py)'
if v1 in s:
    s = s[:s.index(v1)].rstrip('\n') + '\n'
if V2 in s:
    s = s[:s.index(V2)].rstrip('\n') + '\n'
s = s.rstrip('\n') + '\n' + body

for h in ('#include <string>', '#include <string_view>', '#include <algorithm>', '#include <mutex>', '#include <cstdio>'):
    if h not in s:
        s = s.replace('#include <cstdio>', h + '\n#include <cstdio>', 1) if '#include <cstdio>' in s else h + '\n' + s
r.write_text(s, errors='surrogateescape')
print('state census installed')
