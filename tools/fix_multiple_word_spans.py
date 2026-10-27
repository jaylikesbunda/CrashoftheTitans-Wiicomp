"""One ordinary-RAM proof per lmw/stmw; retain each register and fallback operation."""
from pathlib import Path
import argparse, base64, json, re

MARKER = 'NATIVE_MULTIPLE_WORD_SPAN_V1'
INCLUDE = '#include "runtime/native_multiple_words.h"\n'
PATTERN = re.compile(r'    \{\n        uint32_t addr = (?P<address>[^;\n]+);\n'
    r'        for \(int i = (?P<first>\d+); i <= 31; \+\+i\) \{\n'
    r'            (?P<operation>ctx\.gpr\[i\] = ctx\.mmu\.read32\(addr\);|ctx\.mmu\.write32\(addr, ctx\.gpr\[i\]\);)\n'
    r'            addr \+= 4;\n        \}\n    \}')
ENCODED = re.compile(r'    /\* '+MARKER+r' ([A-Za-z0-9+/=]+) \*/\n.*?    /\* END_'+MARKER+r' \*/', re.S)

def restore(source):
    result = ENCODED.sub(lambda m: base64.b64decode(m[1]).decode(), source).replace(INCLUDE, '')
    if MARKER in result: raise ValueError('Incomplete multiple-word staging metadata')
    return result

def apply(source, exclusions=()):
    source = restore(source)
    headers = list(re.finditer(r'\bvoid func_([0-9A-Fa-f]{8})\w*\([^\n]*', source))
    changes = []
    for match in PATTERN.finditer(source):
        owner = next((h for h in reversed(headers) if h.start() < match.start()), None)
        if owner is None or owner[1].upper() in exclusions: continue
        first = int(match['first'])
        if not 0 <= first <= 31: raise ValueError('Invalid multiple-register index')
        # Paired fixtures show the extra proof loses on one/two-word loads and
        # can be noisy on small stores. Amortize it only over at least 16 words.
        if first > 16: continue
        load = match['operation'].startswith('ctx.gpr')
        fast = 'ctx.gpr[i] = native_multiple_words::Load(native_span + 4u * (i - '+str(first)+'));' if load else 'native_multiple_words::Store(native_span + 4u * (i - '+str(first)+'), ctx.gpr[i]);'
        text = ('    /* '+MARKER+' '+base64.b64encode(match[0].encode()).decode()+' */\n'
            '    {\n        uint32_t addr = '+match['address']+';\n'
            '        uint8_t* native_span = native_multiple_words::Span(ctx.mmu, addr, '+str(4*(32-first))+'u);\n'
            '        if (native_span) {\n        for (int i = '+str(first)+'; i <= 31; ++i) {\n'
            '            '+fast+'\n        }\n        } else {\n'
            '        for (int i = '+str(first)+'; i <= 31; ++i) {\n'
            '            '+match['operation']+'\n            addr += 4;\n        }\n        }\n    }\n'
            '    /* END_'+MARKER+' */')
        changes.append((match.start(), match.end(), text))
    for start, end, text in reversed(changes): source = source[:start]+text+source[end:]
    if changes: source = INCLUDE + source
    return source, len(changes)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args(); total = 0
    excluded = set(json.loads((args.port/'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
    if not args.restore:
        target = args.port/'build/nwii-full-source/nWiiRuntime/include/runtime/native_multiple_words.h'
        content = (args.port/'runtime-overlays/native_multiple_words.h').read_bytes()
        if not target.exists() or target.read_bytes() != content: target.write_bytes(content)
    for tree in ('crash-generated', 'crash-hot', 'crash-split'):
        for path in sorted((args.port/'build'/tree).glob('*.cpp')):
            source = path.read_text(errors='surrogateescape')
            if args.restore: changed = restore(source)
            else: changed, count = apply(source, excluded); total += count
            if changed != source: path.write_text(changed, errors='surrogateescape')
    print(('Restored multiple-word canonical inputs.' if args.restore else f'Multiple-word spans: {total} instruction sites; original MMU fallback retained.'))

if __name__ == '__main__': main()
