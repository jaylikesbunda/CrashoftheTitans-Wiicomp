"""Cache launch-fixed diagnostic flags; preserve every guest operation/poll.

The startup runner configures NWII flags before launch. Live input and probe
arming remain files; no guest clock, scheduling, input or completion is changed.
"""
from pathlib import Path
import re

PORT=Path(__file__).resolve().parents[1]
stage=PORT/'build/nwii-full-source/nWiiRuntime'
flags=('NWII_SAMPLE','NWII_DSPTRACE','NWII_CPTRACE','NWII_AI_PROBE',
       'NWII_PI_FIFO_PROBE','NWII_IPC_PROBE')
for path in [stage/'include/runtime/cpu_context.h',*sorted((stage/'src').rglob('*.cpp'))]:
    source=path.read_text()
    source=re.sub(r'(?:#include <cstdlib>\n)?// NATIVE_DIAGNOSTIC_CACHE_BEGIN\n.*?// NATIVE_DIAGNOSTIC_CACHE_END\n',
                  '',source,flags=re.S)
    functions=[]
    for flag in flags:
        call=f'std::getenv("{flag}")'
        helper='NativeLaunchFlag_'+flag
        if call not in source and helper+'()' not in source:continue
        source=source.replace(call,helper+'()')
        functions.append(f'#ifndef NATIVE_LAUNCH_CACHE_{flag}\n#define NATIVE_LAUNCH_CACHE_{flag}\n'
                         f'static inline char *{helper}() {{ static char *const value = std::getenv("{flag}"); return value; }}\n#endif\n')
    if not functions:continue
    # Insert after includes, before namespaces/classes/functions. Explicitly
    # include cstdlib rather than relying on transitive includes.
    lines=source.splitlines(keepends=True)
    last=max(i for i,line in enumerate(lines) if line.startswith('#include'))+1
    block='#include <cstdlib>\n// NATIVE_DIAGNOSTIC_CACHE_BEGIN\n'+''.join(functions)+'// NATIVE_DIAGNOSTIC_CACHE_END\n'
    lines.insert(last,block)
    result=''.join(lines)
    if result!=path.read_text():path.write_text(result)
    print(path.relative_to(stage),len(functions),'cached launch flags')
