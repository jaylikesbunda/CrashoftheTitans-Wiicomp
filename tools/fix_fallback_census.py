"""NATIVE_FALLBACK_CENSUS_V1 -- measurement-only call sites for the GX work census (F3 / F4 export).

The counters live in runtime-overlays/native_gx_work_metrics.h (enum, names, AddFallbackPos, AddTexTrack, CountedMemcmp);
the export prints every name, so the new rows need no export change. This stage adds the call sites:

  hle/gx/fifo_parser.cpp        NativePackedGenericPosition fallback: fallback draws by posType and posMatIdx.
  hle/gx/fifo_parser.cpp        generic packed (scalar AoS) path: draws and vertices with more than two packed inputs.
  hle/texture_cache_gl.cpp      tracker answers (clean / dirty / untracked) and memcmp bytes of the texture validation.

Every counter is relaxed and gated by the census capture flag (NWII_PERF_CENSUS or F3 visible), so a hidden F3 costs one
load per site. Nothing here changes what is decoded or drawn.

Edits keep their original text base64-encoded in markers, so --restore is exact. Idempotent.
Pass --root to work on another copy of nWiiRuntime/src. The default is the build tree used by the other stages.
"""
from pathlib import Path
import argparse, base64, re

MARK = 'NATIVE_FALLBACK_CENSUS_V1'
BLOCK = re.compile(r'/\* ' + MARK + r':(\w+) ([A-Za-z0-9+/=]*) \*/(.*?)/\* END_' + MARK + r':\1 \*/', re.S)
INCLUDE = '#include "runtime/native_gx_work_metrics.h" // ' + MARK
port = Path(__file__).resolve().parents[1]

FILES = {
    'hle/gx/fifo_parser.cpp': [
        ('pos_fallback', 'native_gx_work::Add(native_gx_work::GenericPositionFallback);',
         'native_gx_work::Add(native_gx_work::GenericPositionFallback); '
         'native_gx_work::AddFallbackPos(unsigned(c.raw->vat.posType),bool(c.raw->vat.posMatIdx));'),
        ('aos_wide', 'native_gx_work::Add(native_gx_work::GenericPackedDraws);',
         'native_gx_work::Add(native_gx_work::GenericPackedDraws); '
         'if(inputs>2){native_gx_work::Add(native_gx_work::ScalarAosDecodes); native_gx_work::Add(native_gx_work::ScalarAosVertices,r.count);}'),
    ],
    'hle/texture_cache_gl.cpp': [
        ('tex_track', 'const uint64_t newest=native_ram_newest_write(ram_base,ram_bank_bytes,ram_offset,size);',
         'const uint64_t newest=native_ram_newest_write(ram_base,ram_bank_bytes,ram_offset,size); '
         'native_gx_work::AddTexTrack(newest,entry.verified_gen);'),
        ('tex_cmp_dirty', 'if(std::memcmp(source,entry.source.data(),size)!=0)return false;',
         'if(native_gx_work::CountedMemcmp(source,entry.source.data(),size)!=0)return false;'),
        ('tex_cmp_direct', 'return std::memcmp(source,entry.source.data(),size)==0;};',
         'return native_gx_work::CountedMemcmp(source,entry.source.data(),size)==0;};'),
    ],
}
INCLUDES = ('hle/texture_cache_gl.cpp',)  # fifo_parser.cpp already includes the header


def read(path):
    return path.read_bytes().decode('utf-8', 'surrogateescape')


def write(path, text):
    path.write_bytes(text.encode('utf-8', 'surrogateescape'))


def block(tag, original, new, eol):
    packed = base64.b64encode(original.encode('utf-8', 'surrogateescape')).decode()
    return f'/* {MARK}:{tag} {packed} */{new.replace(chr(10), eol)}/* END_{MARK}:{tag} */'


def edit(text, tag, old, new):
    if f'/* {MARK}:{tag} ' in text:
        return text
    eol = '\r\n' if '\r\n' in text else '\n'
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
    print('Fallback census: fallback draws by posType/posMatIdx, scalar AoS draws, texture tracker and memcmp counters.')


def restore(rt):
    for rel in FILES:
        path = rt / rel
        text = read(path)
        new = unblock(text)
        if new != text:
            write(path, new)
    print('Fallback census stage removed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    rt = args.root or port / 'build/nwii-full-source/nWiiRuntime/src'
    (restore if args.restore else apply)(rt)
