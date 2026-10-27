"""Opt-in turbo: NWII_TURBO=N divides the nominal video field period by N (loading advances one step per field).

Guest timebase/OSGetTime stay on wall-clock time, so game timeouts are unchanged; only the VI field cadence is faster.
Default (unset) is unchanged. Developer speed control only."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
p = PORT / 'build/nwii-full-source/nWiiRuntime/src/core/main.cpp'
s = p.read_bytes().decode('utf-8')
nl = '\r\n' if '\r\n' in s else '\n'
t = s.replace('\r\n', '\n')
if 'NWII_TURBO' in t:
    print('already staged')
else:
    old = '  const auto field_period = std::chrono::nanoseconds(field_pal ? 20000000 : 16683333);'
    new = '''  const double turbo_factor = [](){ const char *e = std::getenv("NWII_TURBO"); double v = e ? std::strtod(e, nullptr) : 1.0; return v >= 1.0 && v <= 64.0 ? v : 1.0; }();
  const auto field_period = std::chrono::nanoseconds(int64_t((field_pal ? 20000000 : 16683333) / turbo_factor));'''
    if old not in t:
        raise SystemExit('field_period anchor changed')
    p.write_bytes(t.replace(old, new, 1).replace('\n', nl).encode('utf-8'))
    print('turbo staged')
