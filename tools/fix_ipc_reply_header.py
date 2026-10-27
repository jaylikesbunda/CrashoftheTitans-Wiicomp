"""Write the IOS reply header the SDK's reply handler depends on.

On completion IOS rewrites the request: +0 becomes the reply type (8), +4 the
result, and +8 the ORIGINAL command (replacing the file descriptor). The game's
IPC reply handler (FUN_803e41c0) switches on +8 to decide which request buffers
to cache-invalidate. The runtime wrote only +4, so the handler saw the file
descriptor instead: descriptor 7 was read as ioctlv and walked a garbage vector
list, spinning in a dcbi loop. Which descriptor a file got decided whether a
save hung, so failures looked random.
"""
from pathlib import Path
port=Path(__file__).resolve().parents[1]
runtime=port/'build/nwii-full-source/nWiiRuntime'
marker='NATIVE_IPC_REPLY_HEADER_V1'
ios=runtime/'src/hle/ios.cpp'
text=ios.read_text()
if marker not in text:
    old='''  if (result != -0x70000001) { 
      nwii::runtime::g_mmu->write32(virt_addr + 4, (uint32_t)(int32_t)result);
  }
  return result;'''
    new='''  if (result != -0x70000001) { 
      nwii::runtime::g_mmu->write32(virt_addr + 4, (uint32_t)(int32_t)result);
      nwii::runtime::g_mmu->write32(virt_addr + 8, cmd); // NATIVE_IPC_REPLY_HEADER_V1: original command
      nwii::runtime::g_mmu->write32(virt_addr, 8);       // reply type
  }
  return result;'''
    assert text.count(old)==1
    ios.write_text(text.replace(old,new,1))
usb=runtime/'src/hle/devices/usb_device.cpp'
text=usb.read_text()
if marker not in text:
    old='''            ctx.mmu.write32(pr.req_addr + 4, (uint32_t)n);
            nwii::runtime::hw::ipc_post_reply(pr.req_addr);'''
    new='''            ctx.mmu.write32(pr.req_addr + 4, (uint32_t)n);
            { uint32_t original = ctx.mmu.read32(pr.req_addr); // NATIVE_IPC_REPLY_HEADER_V1
              ctx.mmu.write32(pr.req_addr + 8, original); ctx.mmu.write32(pr.req_addr, 8); }
            nwii::runtime::hw::ipc_post_reply(pr.req_addr);'''
    assert text.count(old)==1
    usb.write_text(text.replace(old,new,1))
print('IOS reply header (reply type, result, original command) written on completion.')
