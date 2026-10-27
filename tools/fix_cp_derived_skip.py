"""NATIVE_CP_DERIVED_SKIP_V1 -- rebuild CP derived state only on value change.

Array bases, matrix indices and the vertex attribute tables are pure functions
of the CP register value.  Reparsing a burst of identical CP writes re-derives
exactly the same state.  This stage leaves every command in the ordered stream
(the direct-FIFO compaction is untouched) and only skips the redundant rebuild.

The rewrite is marker based and reversible: ``--restore`` puts the exact prior
bytes back, so repeated staging is a fixed point and an unstaged tree is a
strict no-op.
"""
from pathlib import Path
import argparse, base64, re

MARK = 'NATIVE_CP_DERIVED_SKIP_V1'
PARSER_REL = 'src/hle/gx/fifo_parser.cpp'
BLOCK = re.compile(r'/\* ' + MARK + r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_' + MARK + r' \*/\n', re.S)

CP_TOP_OLD = '''    void ParseCP(uint8_t reg, uint32_t val) {
        g_state.cp[reg] = val;'''
CP_TOP_NEW = '''    void ParseCP(uint8_t reg, uint32_t val) {
        // Derived CP state is a pure function of the register value: rebuild it
        // only when the value changes. The command is still emitted downstream.
        if (g_state.cp[reg] == val) return;
        g_state.cp[reg] = val;'''

RMW_OLD = '''                uint32_t merged = (g_state.cp[reg] & ~mask) | (val & mask);
                g_state.cp[reg] = merged;
                ParseCP(reg, merged);'''
RMW_NEW = '''                uint32_t merged = (g_state.cp[reg] & ~mask) | (val & mask);
                ParseCP(reg, merged);'''


def restore(text):
    text = BLOCK.sub(lambda m: base64.b64decode(m[1]).decode(), text)
    if MARK in text:
        raise ValueError('Partial CP derived-skip transform')
    return text


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError(('CP derived-skip anchor', old[:80], text.count(old)))
    return text.replace(old, '/* ' + MARK + ' ' + base64.b64encode(old.encode()).decode() + ' */\n'
                        + new + '\n/* END_' + MARK + ' */\n', 1)


def transform(text):
    text = restore(text)
    text = replace(text, CP_TOP_OLD, CP_TOP_NEW)
    if text.count(RMW_OLD) == 1:
        text = replace(text, RMW_OLD, RMW_NEW)
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument('--runtime', type=Path, help='the WiiRuntime directory directly')
    ap.add_argument('--restore', action='store_true')
    a = ap.parse_args()
    rt = a.runtime if a.runtime else a.port / 'build/nwii-full-source/nWiiRuntime'
    path = rt / PARSER_REL
    raw = path.read_bytes()
    crlf = b'\r\n' in raw
    text = raw.decode('utf-8').replace('\r\n', '\n')
    out = restore(text) if a.restore else transform(text)
    if crlf:
        out = out.replace('\n', '\r\n')
    encoded = out.encode('utf-8')
    if encoded != raw:
        path.write_bytes(encoded)
    print('CP derived-state skip restored.' if a.restore else 'CP derived-state skip staged.')


if __name__ == '__main__':
    main()
