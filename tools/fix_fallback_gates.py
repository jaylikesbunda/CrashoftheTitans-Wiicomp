"""NATIVE_FALLBACK_REASONS_V1 -- measurement-only call site for the generic-path gate census (F3 / F4 export).

The classifier is native_gx_work::FallbackGates (runtime-overlays/native_gx_work_metrics.h): one bit per gate of the packed decode
that a draw fails (count below the specialisation threshold, direct versus indexed position, position/texcoord/colour/normal
formats, NBT normal indices, stride, wide inputs, loader disabled). This stage adds one call on the generic fallback path of
NativePackedGenericPosition, right after the NATIVE_FALLBACK_CENSUS_V1 fallback counter, so the rows are exported with the
fallback census. Nothing decodes differently: the call only reads the VAT and the draw.

Runs after fix_fallback_census.py (its anchor is that stage's text). Edits keep their original text base64-encoded in markers,
so --restore is exact. Idempotent. Pass --root to work on another copy of nWiiRuntime/src.
"""
from pathlib import Path
import argparse, base64, re

MARK = 'NATIVE_FALLBACK_REASONS_V1'
BLOCK = re.compile(r'/\* ' + MARK + r':(\w+) ([A-Za-z0-9+/=]*) \*/(.*?)/\* END_' + MARK + r':\1 \*/', re.S)
FILES = {
    'hle/gx/fifo_parser.cpp': [
        ('gates', 'native_gx_work::AddFallbackPos(unsigned(c.raw->vat.posType),bool(c.raw->vat.posMatIdx));',
         'native_gx_work::AddFallbackPos(unsigned(c.raw->vat.posType),bool(c.raw->vat.posMatIdx)); '
         'if(native_gx_work::capture.load(std::memory_order_relaxed))native_gx_work::AddFallbackGates(native_gx_work::FallbackGates(*c.raw,inputs));'),
    ],
}
port = Path(__file__).resolve().parents[1]


def read(path):
    return path.read_bytes().decode('utf-8', 'surrogateescape')


def write(path, text):
    path.write_bytes(text.encode('utf-8', 'surrogateescape'))


def edit(text, tag, old, new):
    if f'/* {MARK}:{tag} ' in text:
        return text
    eol = '\r\n' if '\r\n' in text else '\n'
    assert text.count(old) == 1, (tag, text.count(old))
    packed = base64.b64encode(old.encode('utf-8', 'surrogateescape')).decode()
    return text.replace(old, f'/* {MARK}:{tag} {packed} */{new}/* END_{MARK}:{tag} */', 1)


def unblock(text):
    return BLOCK.sub(lambda m: base64.b64decode(m.group(2)).decode('utf-8', 'surrogateescape'), text)


def apply(rt):
    for rel, edits in FILES.items():
        path = rt / rel
        text = read(path)
        for tag, old, new in edits:
            text = edit(text, tag, old, new)
        write(path, text)
    print('Fallback gates: generic fallback draws count the packed-decode gates they fail (F3 fallback_gate_* rows).')


def restore(rt):
    for rel in FILES:
        path = rt / rel
        text = read(path)
        new = unblock(text)
        if new != text:
            write(path, new)
    print('Fallback gates stage removed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    rt = args.root or port / 'build/nwii-full-source/nWiiRuntime/src'
    (restore if args.restore else apply)(rt)
