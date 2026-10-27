"""Keep all FIFO draws and signal PE completion only after GPU execution."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
src=PORT/'build/nwii-full-source/nWiiRuntime/src'
def save(p,s):
    if p.read_text()!=s:p.write_text(s)
p=src/'hle/gx/fifo_parser.cpp';s=p.read_text()
old='''        if (reg == 0x45 && (val & 0xF) == 2) {
            nwii::runtime::hw::pe_signal_finish();
        } else if (reg == 0x47) {
            nwii::runtime::hw::pe_signal_token(val & 0xFFFF, false);
        } else if (reg == 0x48) {
            nwii::runtime::hw::pe_signal_token(val & 0xFFFF, true);
        }'''
if old in s:s=s.replace(old,'        // NATIVE_GPU_EXECUTION_ORDER: parsing never completes GPU work.',1)
elif '// NATIVE_GPU_EXECUTION_ORDER' not in s:raise SystemExit('Parse PE context changed')
s=s.replace('            nwii::runtime::hw::pe_signal_finish();','            // NATIVE_GPU_EXECUTION_ORDER: EFB copy is not a finish request.')
save(p,s)
p=src/'hle/gx.cpp';s=p.read_text()
if '// NATIVE_GPU_DRAIN_ALL' not in s:
    a=s.index('    s_carry.insert(s_carry.end()')
    b=s.index('    // NWII_FRAMESIG:',a)
    s=s[:a]+'''    // NATIVE_GPU_DRAIN_ALL: execute every complete parsed command now.
    // Working EFB persists between drains; VI presents retained completed XFBs.
    // Dropping draws while retaining copies/clears corrupts render-to-texture.
    std::vector<nwii::runtime::gx::GXCommand> frame=std::move(commands);
    if(frame.empty())return;

'''+s[b:]
    s=s.replace('    // Decode only the surviving frame\'s draws (stale frames\' draws were\n    // dropped above and their vertices are never built).','    // Decode all parsed draws before executing their FIFO commands.')
    # No GL renderer exists in headless mode. Preserve real PE command delivery
    # after decode; do not fabricate finish events for copies.
    a=s.index('    if (g_window) {',s.index('void ProcessGXFifo()'))
    b=s.index('\n}\n\nnamespace nwii::runtime',a)
    block=s[a:b]
    block=block.rstrip()+''' else {
        for(const auto &c:frame)if(c.type==nwii::runtime::gx::GXCommandType::BPRegister) {
            if(c.reg==0x45 && (c.val&0xffu)==2u)nwii::runtime::hw::pe_signal_finish();
            else if(c.reg==0x47 || c.reg==0x48)nwii::runtime::hw::pe_signal_token(c.val&0xffffu,c.reg==0x48);
        }
    }
'''
    s=s[:a]+block+s[b:]
save(p,s)
p=src/'hle/gx/renderer_gl.cpp';s=p.read_text()
if '#include "runtime/hw/hw.h"' not in s:
    s=s.replace('#include "runtime/gx_state.h"','#include "runtime/gx_state.h"\n#include "runtime/hw/hw.h"',1)
begin='// NATIVE_GPU_COMPLETION_BEGIN\n';end='// NATIVE_GPU_COMPLETION_END\n'
if begin in s:
    a=s.index(begin);b=s.index(end,a)+len(end);s=s[:a]+s[b:].lstrip('\n')
needle='class RendererGL : public IRenderer {'
s=s.replace(needle,begin+(PORT/'runtime-overlays/gpu_completion.inc').read_text()+'\n'+end+needle,1)
needle='        ApplyBPRegister((uint8_t)cmd.reg, bp_value);'
hook='''        // NATIVE_GPU_COMPLETION_HOOK: prior batch flushed at stream boundary.
        NativeGpuCompletion((uint8_t)cmd.reg,bp_value,true);
'''
if '// NATIVE_GPU_COMPLETION_HOOK' not in s:
    if s.count(needle)!=1:raise SystemExit('Renderer BP context changed')
    s=s.replace(needle,needle+'\n'+hook,1)
save(p,s)
print('FIFO drains retain every draw; PE finish/token delivered after execution.')
