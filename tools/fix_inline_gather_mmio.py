"""Specialize gather stores before general slow MMU dispatch, with watch fallback."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h';s=f.read_text()
marker='NATIVE_INLINE_GATHER_MMIO_V1'
if marker not in s:
    assert 'void custom_watch(uint32_t, uint32_t, int) {}' in s
    (p/'runtime-overlays/gather-inline-before.h').write_text(s)
    for name,helper in [('write8','GX_WGPIPE_Write8'),('write16','GX_WGPIPE_Write16'),('write32','GX_WGPIPE_Write32'),('write_f32','GX_WGPIPE_WriteF32'),('write_f64','GX_WGPIPE_WriteF64')]:
        anchor='    native_'+name+'_slow(addr,value);';assert s.count(anchor)==1
        direct=f'''    // NATIVE_INLINE_GATHER_MMIO_V1: mirror page, whole scalar write, no watch.
    if((paddr&0xfffff000u)==0x0c008000u && g_watch_addr==0){{{helper}(value);return;}}
'''
        s=s.replace(anchor,direct+anchor,1)
    f.write_text(s)
print('Inline gather MMIO store specialization staged; watches retain slow methods.')
