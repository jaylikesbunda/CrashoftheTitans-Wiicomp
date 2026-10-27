"""Bulk disc/IOS byte transfers with observer/edge fallbacks and bounded scratch."""
from pathlib import Path
import argparse

MARKER = 'NATIVE_IOS_BULK_TRANSFER_V1'
INCLUDE = '#include "runtime/native_guest_bytes.h" // '+MARKER+'\n'

def edit(source, old, new, count=1):
    assert source.count(old) == count, ('Requalify transfer source', old[:80])
    return source.replace(old, new)

def apply_ios(source):
    if INCLUDE in source:
        assert 'native_guest_bytes::ReadBuffer temp(total_length);' in source
        return source
    source = edit(source, '  for (uint32_t i = 0; i < IPC_SLOT_SIZE; ++i)\n    ctx.mmu.write8(ptr + i, 0);',
                  '  native_guest_bytes::Zero(ctx.mmu, ptr, IPC_SLOT_SIZE);')
    source = edit(source, '    std::vector<uint8_t> temp(total_length);',
                  '    native_guest_bytes::ReadBuffer temp(total_length);')
    source = edit(source, '    for (uint32_t i = 0; i < bytes_read; i++)\n      mmu->write8(buf_ptr + i, temp[i]);',
                  '    native_guest_bytes::Copy(*mmu, buf_ptr, temp.data(), bytes_read);')
    source = edit(source, 'for (uint32_t i = 0; i < total_length; i++) mmu->write8(buf_ptr + i, 0);',
                  'native_guest_bytes::Zero(*mmu, buf_ptr, total_length);', 2)
    return INCLUDE+source

def apply_device(source):
    if INCLUDE in source:
        assert 'native_guest_bytes::ReadBuffer tmp(length);' in source
        return source
    source = edit(source, '                std::vector<uint8_t> tmp(length);',
                  '                native_guest_bytes::ReadBuffer tmp(length);')
    source = edit(source, '                    for (uint32_t i = 0; i < length; ++i)\n                        ctx.mmu.write8(dst + i, tmp[i]);',
                  '                    native_guest_bytes::Copy(ctx.mmu, dst, tmp.data(), length);')
    source = edit(source, 'for (int i = 0; i < 32; i++) ctx.mmu.write8(outbuf + i, 0);',
                  'native_guest_bytes::Zero(ctx.mmu, outbuf, 32);')
    source = edit(source, 'for (int i = 0; i < 32; i++) ctx.mmu.write8(buf + i, 0);',
                  'native_guest_bytes::Zero(ctx.mmu, buf, 32);')
    source = edit(source, '                    for (uint32_t j = 0; j < len; ++j)\n                        ctx.mmu.write8(addr + j, 0);',
                  '                    native_guest_bytes::Zero(ctx.mmu, addr, len);')
    return INCLUDE+source

def apply_hw(source):
    if INCLUDE in source:
        assert 'native_guest_bytes::ReadBuffer tmp(length);' in source
        return source
    source = edit(source, '            std::vector<uint8_t> tmp(length, 0);',
                  '            native_guest_bytes::ReadBuffer tmp(length);')
    source = edit(source, '            for (uint32_t i = 0; i < length; ++i)\n                nwii::runtime::g_mmu->write8(dst + i, tmp[i]);',
                  '            native_guest_bytes::Copy(*nwii::runtime::g_mmu, dst, tmp.data(), length);')
    source = edit(source, '            for (uint32_t i = 0; i < di_len; ++i)\n                nwii::runtime::g_mmu->write8(dst + i, 0);',
                  '            native_guest_bytes::Zero(*nwii::runtime::g_mmu, dst, di_len);')
    return INCLUDE+source

def apply_dvd(source):
    if INCLUDE in source:
        assert 'native_guest_bytes::ReadBuffer buf(length);' in source
        return source
    source = edit(source, '        std::vector<uint8_t> buf(length);',
                  '        native_guest_bytes::ReadBuffer buf(length);')
    source = edit(source, '        for (uint32_t i = 0; i < bytes_read; i++) ctx.mmu.write8(buffer_addr + i, buf[i]);',
                  '        native_guest_bytes::Copy(ctx.mmu, buffer_addr, buf.data(), bytes_read);')
    return INCLUDE+source

TRANSFORMS = (('src/hle/ios.cpp',apply_ios),('src/hle/devices/di_device.cpp',apply_device),
              ('src/hw/hw_di.cpp',apply_hw),('src/hle/dvd.cpp',apply_dvd))

if __name__ == '__main__':
    port = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, default=port/'build/nwii-full-source/nWiiRuntime')
    args = parser.parse_args()
    memory_contract = (args.runtime/'include/runtime/cpu_context.h').read_text()
    assert 'void custom_watch(uint32_t, uint32_t, int) {}' in memory_contract, 'Requalify custom byte observers'
    header = (port/'runtime-overlays/native_guest_bytes.h').read_text()
    destination = args.runtime/'include/runtime/native_guest_bytes.h'
    if not destination.exists() or destination.read_text() != header:
        destination.write_text(header)
    for rel, transform in TRANSFORMS:
        path = args.runtime/rel
        original = path.read_text()
        changed = transform(original)
        assert transform(changed) == changed
        if changed != original:
            path.write_text(changed)
    print('IOS/device/HW/DVD byte transfers batched; scratch bounded; observers and completion retained.')
