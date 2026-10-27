"""Stage bounded reuse without changing command-vector APIs or shared ownership."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
f=rt/'src/hle/gx/fifo_parser.cpp';s=f.read_text();marker='// NATIVE_COMMAND_REUSE_V1:'
if marker in s:
 a=s.index(marker);b=s.index('// NATIVE_COMMAND_REUSE_END',a)+len('// NATIVE_COMMAND_REUSE_END');s=s[:a]+s[b:]
a=s.index('static void ParseStream(')
s=s[:a]+(p/'runtime-overlays/command_reuse.inc').read_text()+'\n// NATIVE_COMMAND_REUSE_END\n'+s[a:]
s=s.replace('c.raw = std::make_shared<DrawRaw>();','c.raw = NativeAcquireDrawRaw();')
s=s.replace('c.raw = std::make_shared<DrawRaw>(*c.raw);','auto snapshot=NativeAcquireDrawRaw(); *snapshot=*c.raw; c.raw=std::move(snapshot);')
for size in ('num','num_floats'):s=s.replace('c.payload.resize('+size+');','NativeAcquirePayload(c.payload,'+size+');')
if '#include <mutex>' not in s:s='#include <mutex>\n'+s
f.write_text(s)
f=rt/'src/hle/gx.cpp';s=f.read_text()
decl='namespace nwii::runtime::gx {void NativeRecycleCommands(std::vector<GXCommand>&);}\n'
if decl not in s:
 # Place after all headers so GXCommand and vector declarations are available.
 lines=s.splitlines(True);last=max(i for i,line in enumerate(lines) if line.startswith('#include'))
 lines.insert(last+1,decl);s=''.join(lines)
needle='s_command_pool.clear();s_command_pool_busy=false;'
replacement='nwii::runtime::gx::NativeRecycleCommands(s_command_pool);'+needle
if replacement not in s:
 assert s.count(needle)==1;s=s.replace(needle,replacement)
f.write_text(s)
print('Bounded draw snapshot / XF payload reuse staged with unique-owner gates.')
