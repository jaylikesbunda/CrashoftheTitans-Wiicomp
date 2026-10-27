"""NATIVE_TRACKER_HOST_WRITES_V1 -- host stores into guest RAM go through native_arena::HostWriteScope.

The arena write tracker and the sticky pages (page 0, watch spans) only see guest stores. A host store into guest RAM
that is not wrapped in a HostWriteScope is either unrecorded (an armed page is written with no dirty flag from the
scope) or, on a sticky page, a user-mode fault that the handler does not emulate. HostWriteScope makes the covered pages
writable, marks armed tracker pages dirty and re-protects the sticky pages when it ends.

Edits (staged tree only; every edit keeps its original text base64-encoded in its marker, so --restore is exact):
  hw/hw_dsp.cpp        AR DMA copies between MEM1 and MEM2 (two memcpy branches).
  hle/gx.cpp           write-gather fast path: the byte store into the FIFO ring in guest RAM.
  hle/interpreter.cpp  NATIVE_BULK_HELPERS_V1 guest memcpy / memset helpers (NativeHelperCopy, NativeHelperFill).
  hle/ios.cpp          disc reads into guest RAM (file read, WBFS read, zero fill for missing blocks).
  hle/virtual_disc.cpp zero fill before a disc read (the read loop already has its own scope).
Each include line is added at the top of the file. The stage needs the arena: with NWII_BUILD_GUEST_ARENA=0 it is a
no-op. The scopes do not nest (a nested scope would re-protect sticky pages while the outer write is still pending).

Idempotent. Pass --root to work on another copy of nWiiRuntime/src. The default is the build tree used by the other stages.
"""
from pathlib import Path
import argparse, base64, os, re

MARK = 'NATIVE_TRACKER_HOST_WRITES_V1'
BLOCK = re.compile(r'/\* ' + MARK + r':(\w+) ([A-Za-z0-9+/=]*) \*/(.*?)/\* END_' + MARK + r':\1 \*/', re.S)
INCLUDE = '#include "runtime/native_guest_arena.h" // ' + MARK
port = Path(__file__).resolve().parents[1]

SCOPE = 'native_arena::HostWriteScope tracker_scope'
FILES = {
    'hw/hw_dsp.cpp': [
        ('dsp_to_mem2', 'if (!dir) std::memcpy(mem2 + ar_a, mem1 + mm, copy_count);',
         'if (!dir) { %s(mem2 + ar_a, copy_count); std::memcpy(mem2 + ar_a, mem1 + mm, copy_count); }' % SCOPE),
        ('dsp_to_mem1', 'else std::memcpy(mem1 + mm, mem2 + ar_a, copy_count);',
         'else { %s(mem1 + mm, copy_count); std::memcpy(mem1 + mm, mem2 + ar_a, copy_count); }' % SCOPE),
    ],
    'hle/gx.cpp': [
        ('gather', '    std::memcpy(dst,&be,bytes);',
         '    { %s(dst, bytes); std::memcpy(dst,&be,bytes); }' % SCOPE),
    ],
    'hle/interpreter.cpp': [
        ('bulk_copy', '    std::memmove(d,r,n);return true;',
         '    { %s(d, n); std::memmove(d,r,n); }return true;' % SCOPE),
        ('bulk_fill', '    std::memset(d,value,n);return true;',
         '    { %s(d, n); std::memset(d,value,n); }return true;' % SCOPE),
    ],
    'hle/ios.cpp': [
        ('disc_zero', '    std::memset(dst, 0, length);',
         '    { %s(dst, length); std::memset(dst, 0, length); }' % SCOPE),
        ('disc_read', '    file->read(reinterpret_cast<char *>(dst), length);',
         '    { %s(dst, length); file->read(reinterpret_cast<char *>(dst), length); }' % SCOPE),
        ('wbfs_read', '        file->read(reinterpret_cast<char *>(dst + done), chunk);',
         '        { %s(dst + done, chunk); file->read(reinterpret_cast<char *>(dst + done), chunk); }' % SCOPE),
        ('wbfs_zero', '        std::memset(dst + done, 0, chunk);',
         '        { %s(dst + done, chunk); std::memset(dst + done, 0, chunk); }' % SCOPE),
    ],
    'hle/virtual_disc.cpp': [
        ('zero', '    std::memset(dst, 0, len);',
         '    { %s(dst, len); std::memset(dst, 0, len); }' % SCOPE),
    ],
}
# Files that need the arena header added at the top (virtual_disc.cpp already includes it).
INCLUDES = ('hw/hw_dsp.cpp', 'hle/gx.cpp', 'hle/interpreter.cpp', 'hle/ios.cpp')


def read(path):
    return path.read_bytes().decode('utf-8', 'surrogateescape')


def write(path, text):
    path.write_bytes(text.encode('utf-8', 'surrogateescape'))


def block(tag, original, new, eol):
    packed = base64.b64encode(original.encode('utf-8', 'surrogateescape')).decode()
    return f'/* {MARK}:{tag} {packed} */{new.replace(chr(10), eol)}/* END_{MARK}:{tag} */'


def edit(text, tag, old, new):
    """Replace the one-line `old` by a restorable block holding `new`; a no-op when this tag is already applied."""
    if f'/* {MARK}:{tag} ' in text:
        return text
    eol = '\r\n' if '\r\n' in text else '\n'
    old = old.replace('\n', eol)
    assert text.count(old) == 1, (tag, text.count(old))
    return text.replace(old, block(tag, old, new, eol), 1)


def prepend_include(text):
    if f'/* {MARK}:include ' in text:
        return text
    eol = '\r\n' if '\r\n' in text else '\n'
    return block('include', '', INCLUDE + '\n', eol) + text


def unblock(text):
    return BLOCK.sub(lambda m: base64.b64decode(m.group(2)).decode('utf-8', 'surrogateescape'), text)


def apply(rt):
    for rel, edits in FILES.items():
        path = rt / rel
        text = read(path)
        for tag, old, new in edits:
            text = edit(text, tag, old, new)
        if rel in INCLUDES:
            text = prepend_include(text)
        if text != read(path):
            write(path, text)
    print('Tracker host writes: DMA, gather, bulk helpers and disc reads run under HostWriteScope.')


def restore(rt):
    for rel in FILES:
        path = rt / rel
        text = read(path)
        new = unblock(text)
        if new != text:
            write(path, new)
    print('Tracker host-write stage removed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    rt = args.root or port / 'build/nwii-full-source/nWiiRuntime/src'
    if args.restore:
        restore(rt)
    elif os.environ.get('NWII_BUILD_GUEST_ARENA') == '0':
        print('tracker host writes: skipped (NWII_BUILD_GUEST_ARENA=0 leaves native_arena out of the build)')
    else:
        apply(rt)
