"""Install the host-backed ISFS NAND (saves) and pass the kernel fd to devices."""
from pathlib import Path
port=Path(__file__).resolve().parents[1]
runtime=port/'build/nwii-full-source/nWiiRuntime'
marker='// NATIVE_NAND_FS_V1'
device=runtime/'src/hle/devices/fs_device.cpp'
overlay=(port/'runtime-overlays/nand_fs_device.cpp').read_text()
if device.read_text()!=overlay:
    device.write_text(overlay)
header=runtime/'include/runtime/ios_device.h'
text=header.read_text()
if 'internal_fd' not in text:
    needle='    std::string path;\n    uint32_t mode;\n};'
    assert text.count(needle)==1
    header.write_text(text.replace(needle,'    std::string path;\n    uint32_t mode;\n    uint32_t internal_fd = 0; // NATIVE_NAND_FS_V1: device-local fd for file ioctls\n};',1))
kernel=runtime/'src/hle/ios_kernel.cpp'
text=kernel.read_text()
if 'NATIVE_NAND_FS_V1' not in text:
    for call in ('ioctl','ioctlv'):
        old=f'    return e->device->{call}(ctx, req);'
        assert text.count(old)==1
        text=text.replace(old,f'    IpcRequest local = req; local.internal_fd = (uint32_t)e->internal_fd; // NATIVE_NAND_FS_V1\n    return e->device->{call}(ctx, local);',1)
    kernel.write_text(text)

es=runtime/'src/hle/devices/es_device.cpp'
text=es.read_text()
if 'NATIVE_ES_DATADIR_V1' not in text:
    handler='''        } else if (req.ioctl_cmd == 0x1D) { // NATIVE_ES_DATADIR_V1: ES_GetDataDir
            if (req.arg_cnt_in >= 1 && req.arg_cnt_out >= 1) {
                uint32_t tid_addr = req.ioctlv_vecs[0].addr;
                uint32_t out_addr = req.ioctlv_vecs[req.arg_cnt_in].addr;
                uint32_t out_len = req.ioctlv_vecs[req.arg_cnt_in].len;
                if (tid_addr && out_addr) {
                    char dir[48];
                    std::snprintf(dir, sizeof dir, "/title/%08x/%08x/data",
                                  ctx.mmu.read32(tid_addr), ctx.mmu.read32(tid_addr + 4));
                    for (uint32_t j = 0; j < out_len && j <= std::strlen(dir); ++j)
                        ctx.mmu.write8(out_addr + j, (uint8_t)dir[j]);
                    std::cout << "[ES] DataDir " << dir << std::endl;
                }
            }
'''
    needle='        } else if (req.ioctl_cmd == 0x26) {'
    assert text.count(needle)==1
    text=text.replace(needle,handler+needle,1)
    text=text.replace('#include <iostream>','#include <cstdio>\n#include <cstring>\n#include <iostream>',1)
    es.write_text(text)
print('Host-backed NAND filesystem staged; device fd forwarded to ioctl handlers.')
