"""Preserve both hardware colour banks instead of uploading an erased bank."""
from pathlib import Path

PORT = Path(__file__).resolve().parents[1]
p = PORT / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
s = p.read_text()
marker = '// NATIVE_TEV_REGISTER_BANKS_V1\n'
overlay = (PORT / 'runtime-overlays/tev_register_banks.inc').read_text()
end_marker = '// NATIVE_GX_DEPTH_MASKS_V1'
if end_marker not in s:
    raise SystemExit('Stage GX depth masks before TEV colour banks')
start = s.index(marker) if marker in s else s.index(end_marker)
end = s.index(end_marker, start)
s = s[:start] + marker + overlay + '\n' + s[end:]
old = '        ApplyBPRegister((uint8_t)cmd.reg, cmd.val);'
new = old + '\n        NativeTevRegisterWrite((uint8_t)cmd.reg, cmd.val);'
# A later stage (fix_bp_write_mask.py) renames the arguments on both calls, so the probe is the
# function name only: a re-staged tree keeps the staged form and must stay a no-op.
if 'NativeTevRegisterWrite(' not in s:
    if s.count(old) != 1:
        raise SystemExit('BP replay context changed')
    s = s.replace(old, new, 1)
old = '  auto decode11 = [](uint32_t val, int shift) -> float {'
if old in s:
    a = s.index(old)
    b = s.index('  static const bool s_tevtrace', a)
    s = s[:a] + '  NativeTevUniforms(color, kcolor);\n\n' + s[b:]
elif '  NativeTevUniforms(color, kcolor);' not in s:
    raise SystemExit('TEV uniforms context changed')
s=s.replace('g_state.GetShaderHash(cmd.prim_type)', 'NativeTevShaderHash(g_state, cmd.prim_type)')
if p.read_text() != s:
    p.write_text(s)
print('Separate persistent TEV regular/constant banks staged.')
