"""Make the disabled FP-context probe nearly free.

native_fp_context_probe() is called on every callback poll (every guest loop iteration). Its large frame (ostringstream etc.)
made its prologue/epilogue ~34% of host time even with the probe disabled. Split it into an inline flag test plus the
original body as native_fp_context_probe_slow(). Behaviour with NWII_FP_CONTEXT_PROBE set is unchanged."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
p = PORT / 'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp'
s = p.read_bytes().decode('utf-8')
nl = '\r\n' if '\r\n' in s else '\n'
t = s.replace('\r\n', '\n')
if 'native_fp_context_probe_slow' in t:
    print('already staged')
else:
    old = 'static void native_fp_context_probe(CPUContext &ctx, const char *event, uint32_t c=0) {\n  static const bool enabled = std::getenv("NWII_FP_CONTEXT_PROBE") != nullptr;\n  if (!enabled) return;\n'
    new = ('static void native_fp_context_probe_slow(CPUContext &ctx, const char *event, uint32_t c) {\n')
    if old not in t:
        raise SystemExit('fp probe context changed')
    t = t.replace(old, new, 1)
    end_marker = '// NATIVE_FP_CONTEXT_END'
    wrapper = ('static const bool g_fp_context_probe_enabled = std::getenv("NWII_FP_CONTEXT_PROBE") != nullptr;\n'
               'static inline void native_fp_context_probe(CPUContext &ctx, const char *event, uint32_t c=0) {\n'
               '  if (__builtin_expect(g_fp_context_probe_enabled, 0)) native_fp_context_probe_slow(ctx, event, c);\n'
               '}\n')
    if end_marker not in t:
        raise SystemExit('fp probe end marker missing')
    t = t.replace(end_marker, wrapper + end_marker, 1)
    p.write_bytes(t.replace('\n', nl).encode('utf-8'))
    print('fp probe fast path staged')
