"""Let generic packed draws attempt the geometry retention cache.

NativeTryPackedDecode only attempts NativeGeometryKey for the `fast` and
`observed` families, so every generic draw is re-decoded every frame (~76% of all
fresh packed vertices). NativeGeometryKey already self-guards every unsafe layout
(NBT, secondary texture/clr matrices, Direct masks, arrays outside MEM1/MEM2), and
on a hit the live matrix bank is still taken from the command snapshot, so
admitting generic draws reuses proven machinery. Build rollback:
NWII_NO_GENERIC_RETENTION=1. Idempotent.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_GENERIC_RETENTION_V1'

OLD = ' if(fast || observed){native_trace::Scope validate("gx.geometry_validate");retain=NativeGeometryKey(r,inputs,geometry_key);'
NEW = (' static const bool generic_retain=std::getenv("NWII_NO_GENERIC_RETENTION")==nullptr; // ' + MARK + '\n'
       ' if(fast || observed || generic_retain){native_trace::Scope validate("gx.geometry_validate");retain=NativeGeometryKey(r,inputs,geometry_key);')


def apply(s):
    if MARK in s:
        return s
    if s.count(OLD) != 1:
        raise SystemExit('generic retention anchor not unique: %d' % s.count(OLD))
    return s.replace(OLD, NEW, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_GENERIC_RETENTION', '1') == '0':
        print('generic retention disabled')
        return
    path = args.port / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/fifo_parser.cpp'
    before = path.read_text()
    after = apply(before)
    if before != after:
        path.write_text(after)
    print('generic retention staged')


if __name__ == '__main__':
    main()
