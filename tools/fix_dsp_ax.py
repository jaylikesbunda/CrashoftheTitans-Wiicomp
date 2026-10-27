"""Install the real AX DSP command backend without replacing translated SDK code."""
from pathlib import Path
import shutil,subprocess,sys
P=Path(__file__).resolve().parents[1]
subprocess.run([sys.executable,str(P/'tools/prepare_ax_backend.py')],check=True)
O=P/'runtime-overlays/native_ax'
coeff=(P/'reference/dolphin-ax/dsp_coef.bin').read_bytes()
assert len(coeff)==4096
text='// Dolphin free DSP coefficient table; GPL-2.0-or-later.\n#pragma once\nnamespace NativeAX {\ninline constexpr std::array<u8,4096> coefficients={\n'+','.join(str(x) for x in coeff)+'\n};\n}\n'
c=O/'coefficients.h'
if not c.exists() or c.read_text()!=text:c.write_text(text)
stage=P/'build/nwii-full-source/nWiiRuntime'
target=stage/'include/runtime/native_ax';target.mkdir(exist_ok=True)
for src in O.iterdir():
    dst=target/src.name
    if not dst.exists() or dst.read_bytes()!=src.read_bytes():shutil.copyfile(src,dst)
p=stage/'src/hw/hw_dsp.cpp';s=p.read_text()
if '#include "runtime/native_ax/protocol.h"' not in s:s='#include "runtime/native_ax/protocol.h"\n'+s
begin='// NATIVE_AX_BACKEND_BEGIN';end='// NATIVE_AX_BACKEND_END'
block=begin+'\n'+(P/'runtime-overlays/dsp_ax.inc').read_text()+'\n'+end+'\n'
if begin in s:
    a=s.index(begin);b=s.index(end,a)+len(end)+1;s=s[:a]+block+s[b:]
else:
    needle='static void ar_dma_execute()';a=s.index(needle);s=s[:a]+block+'\n'+s[a:]
    a=s.index('                    auto reply = [](uint16_t lo)');b=s.index('\n                }\n                if (NativeLaunchFlag_NWII_DSPTRACE())',a)
    s=s[:a]+'                    native_dsp_send(mail);'+s[b:]
    s=s.replace('                static int s_ax_state = 0;','')
    needle='                dsp_control &= ~0x0080;\n                dsp_update_pi();'
    assert s.count(needle)==1
    s=s.replace(needle,needle+'\n                native_dsp_schedule_mail();',1)
    old='                    dsp_mbox_dsp_hi = 0xDCD1;\n                    dsp_mbox_dsp_lo = 0x0000;'
    assert s.count(old)==1;s=s.replace(old,'                    native_dsp_reset();',1)
    # ADMA emits exactly the programmed frames at 32kHz, not an unrelated 5ms tick.
    s=s.replace('g_ctx_ptr->tb_freq / 200, adma_event_cb','std::max<uint64_t>(1, g_ctx_ptr->tb_freq * (uint64_t(adma_control & 0x7fff) * 8) / 32000), adma_event_cb')
if p.read_text()!=s:p.write_text(s)
# AX output RAM is interleaved R,L; SDL expects L,R. Queue only produced frames.
s=p.read_text()
s=s.replace('                adma_ring.push_back((int16_t)g_mmu->read16(va + i));\n                adma_ring.push_back((int16_t)g_mmu->read16(va + i + 2));',
            '                adma_ring.push_back((int16_t)g_mmu->read16(va + i + 2));\n                adma_ring.push_back((int16_t)g_mmu->read16(va + i));')
if p.read_text()!=s:p.write_text(s)
p=stage/'src/core/main.cpp';s=p.read_text()
s=s.replace('          nwii::runtime::hw::dsp_audio_pull(abuf, 1024);\n          SDL_QueueAudio(audio_dev, abuf, 1024 * 4);',
            '          size_t produced=nwii::runtime::hw::dsp_audio_pull(abuf, 1024);\n          if(produced)SDL_QueueAudio(audio_dev, abuf, Uint32(produced * 4));')
if p.read_text()!=s:p.write_text(s)
b=P/'tools/build_native.py';s=b.read_text();needle="run('native-game-build',"
if "run('native-dsp-ax-stage'" not in s:s=s.replace(needle,"run('native-dsp-ax-stage',[sys.executable,PORT/'tools/fix_dsp_ax.py'])\n"+needle,1);b.write_text(s)
print('AX DSP hardware processing installed; unknown commands fail without acknowledgement.')
