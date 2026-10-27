"""NATIVE_CAMERA_GLIDE_V1: stage the vertical-only camera glide.

The helper runs right after the volume camera update at 0x80047824 returns in
func_80045F18, before the camera transform is consumed.  It only changes the
camera Y (+0x78) and look-at Y (+0x84); the runtime helper stays disabled unless
NWII_CAMERA_GLIDE_MS is set.

Every generated chunk is scanned for the exact anchor.  The anchor is wrapped in
a base64 marker block so --restore is byte-exact (CRLF/LF preserved), and the
include is inserted once per patched file.

Build rollback: NWII_BUILD_CAMERA_GLIDE=0.  Idempotent.
"""
from pathlib import Path
import argparse
import base64
import os
import re

MARK = 'NATIVE_CAMERA_GLIDE_V1'
INCLUDE = '#include "runtime/native_camera_glide.h"'
CALL = '    native_camera_glide::Apply(ctx, ctx.gpr[31], (float)ctx.fpr[30]);'
ANCHOR_LINES = (
    '    ctx.pc = 0x80047824; func_80047824(ctx);',
    '    if (ctx.pc != 0x800460D8) return;',
    '    ++ctx.inst_count;',
)
BLOCK = re.compile(r'/\* ' + MARK + r' ([A-Za-z0-9+/=]+) \*/.*?/\* END_' + MARK + r' \*/', re.S)
INCLUDE_LINE = re.compile(r'(?m)^#include "runtime/native_camera_glide\.h"[ \t]*//[ \t]*'
                          + MARK + r'[ \t]*(?:\r\n|\n|\r)?')


def _find_anchor(text):
    for nl in ('\r\n', '\n', '\r'):
        anchor = nl.join(ANCHOR_LINES)
        count = text.count(anchor)
        if count > 1:
            raise ValueError('camera glide anchor matched %d times' % count)
        if count == 1:
            return anchor, nl
    return None, None


def _insert_include(text, nl):
    line = INCLUDE + ' // ' + MARK + nl
    if INCLUDE + ' // ' + MARK in text:
        return text
    # Keep cpu_context complete before the helper is parsed.
    anchor = '#include "runtime/cpu_context.h"'
    pos = text.find(anchor)
    if pos >= 0:
        end = text.find('\n', pos)
        if end >= 0:
            return text[:end + 1] + line + text[end + 1:]
        return text + nl + line
    if text.startswith('\ufeff'):
        return '\ufeff' + line + text[1:]
    return line + text


def patch_text(text):
    """Apply the stage to one chunk; returns (text, changed)."""
    text = restore(text)
    anchor, nl = _find_anchor(text)
    if anchor is None:
        return text, False
    encoded = base64.b64encode(anchor.encode('utf-8', 'surrogateescape')).decode()
    wrapped = ('/* ' + MARK + ' ' + encoded + ' */' + nl + anchor + nl + CALL + nl
               + '/* END_' + MARK + ' */')
    text = text.replace(anchor, wrapped, 1)
    text = _insert_include(text, nl)
    return text, True


def restore(text):
    """Remove the stage exactly; returns the original chunk bytes as text."""
    text = BLOCK.sub(
        lambda m: base64.b64decode(m.group(1)).decode('utf-8', 'surrogateescape'), text)
    text = INCLUDE_LINE.sub('', text)
    if MARK in text:
        raise ValueError('partial camera glide transform')
    return text


def _generated_paths(port):
    paths = []
    for tree in ('crash-generated', 'crash-split'):
        base = port / 'build' / tree
        if base.is_dir():
            paths.extend(sorted(base.glob('*.cpp')))
    return paths


def apply(port, runtime=None, restore_mode=False, dry_run=False):
    """Stage or restore every generated chunk; returns (changed, header_changed)."""
    runtime = runtime or port / 'build/nwii-full-source/nWiiRuntime'
    changed = []
    for path in _generated_paths(port):
        raw = path.read_bytes()
        old = raw.decode('utf-8', 'surrogateescape')
        new = restore(old) if restore_mode else patch_text(old)[0]
        if new != old:
            changed.append(path)
            if not dry_run:
                path.write_bytes(new.encode('utf-8', 'surrogateescape'))

    header_changed = False
    if not restore_mode:
        source = port / 'runtime-overlays/native_camera_glide.h'
        target = runtime / 'include/runtime/native_camera_glide.h'
        data = source.read_bytes()
        if not target.exists() or target.read_bytes() != data:
            header_changed = True
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
    return changed, header_changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--runtime', type=Path,
                        help='build root (default: build/nwii-full-source/nWiiRuntime)')
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if os.environ.get('NWII_BUILD_CAMERA_GLIDE', '1') == '0' and not args.restore:
        print('Camera glide stage disabled (NWII_BUILD_CAMERA_GLIDE=0).')
        return

    changed, header = apply(args.port, args.runtime, args.restore, args.dry_run)
    suffix = ' (dry run)' if args.dry_run else ''
    if args.restore:
        if changed:
            print('Camera glide stage: restored %d file(s)%s.' % (len(changed), suffix))
        else:
            print('Camera glide stage: no changes%s.' % suffix)
        return
    if not changed:
        if header:
            print('Camera glide stage: %s helper (no anchor)%s.' % (
                'would copy' if args.dry_run else 'copied', suffix))
        else:
            print('Camera glide stage: no changes%s.' % suffix)
        return
    detail = '%d file(s)' % len(changed)
    if header:
        detail += ', helper ' + ('would be copied' if args.dry_run else 'copied')
    print('Camera glide stage: %s %s%s.' % (
        'would patch' if args.dry_run else 'patched', detail, suffix))


if __name__ == '__main__':
    main()
