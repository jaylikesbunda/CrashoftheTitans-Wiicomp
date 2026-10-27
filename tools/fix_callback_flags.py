"""Resolve process-fixed diagnostic flags once, preserving every callback poll."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp'
s=p.read_text()
if 'NATIVE_EMPTY_POLL_FASTPATH_V1' in s:
    # Both split paths use cached launch flags; do not merge their lexical scopes.
    print('Split callback paths retain cached diagnostic flags.')
    raise SystemExit(0)
needle='bool process_pending_callbacks(CPUContext &ctx) {\n'
a=s.index(needle)+len(needle)
b=s.rfind('\n}\n')
if b<=a:raise SystemExit('Callback end changed')
body=s[a:b].replace('NativeLaunchFlag_NWII_SAMPLE()', 'std::getenv("NWII_SAMPLE")');marker='  // NATIVE_CALLBACK_FLAGS_V1\n'
declaration=marker+'  static const bool callback_sample = std::getenv("NWII_SAMPLE") != nullptr;\n'
if marker in body:
    if declaration not in body:raise SystemExit('Cached flag declaration changed')
    body=body.replace(declaration,'')
count=body.count('std::getenv("NWII_SAMPLE")')
body=body.replace('std::getenv("NWII_SAMPLE")','callback_sample')
body=body.replace('callback_sample != nullptr','callback_sample')
new=s[:a]+declaration+body+s[b:]
if new!=s:p.write_text(new)
print(f'Cached callback diagnostic flag; {count} recurring environment lookups replaced. All callback/IRQ checks retained.')
