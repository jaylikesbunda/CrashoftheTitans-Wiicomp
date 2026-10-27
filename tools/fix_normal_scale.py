"""Integer vertex normals carry fixed-point fractions; decode them with the hardware scale (root cause of the XP-orb speckle).

GX normals have a fixed number of fraction bits that depends only on the component type (the VAT has no normal shift): U8 7, S8 6, U16 15,
S16 14 (float normals are unscaled). Dolphin's VertexLoader_Normal uses the same table. Both vertex decoders here built the normal plan with
shift 0, so an S8 normal of length 1.0 (64) came out as 64.0 and an S16 one as 16384.0.

Lighting never showed it because the generated lighting code normalises the normal first. Texture-coordinate generation from normals does
not (hardware does not either), so every environment / sphere map driven by integer normals got coordinates 64x-16384x too large: with
repeat wrap the 32x32 environment map of the orbs' refraction material tiled dozens of times across each orb (speckle) and swam as the orb
turned (shimmer). The water uses the same material but is flat, so its normal (and therefore its lookup) is constant and looked right.
Idempotent.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
f = p / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/fifo_parser.cpp'
MARK = 'NATIVE_NORMAL_SCALE_V1'
s = f.read_text(errors='surrogateescape')
helper = ('// ' + MARK + ': fraction bits of integer normals (no VAT shift for normals): U8 7, S8 6, U16 15, S16 14, float 0.\n'
          'static inline uint8_t NativeNormalFracBits(VtxAttrType type) {\n'
          '    switch (type) { case VtxAttrType::U8: return 7; case VtxAttrType::S8: return 6; case VtxAttrType::U16: return 15; case VtxAttrType::S16: return 14; default: return 0; }\n'
          '}\n')
if 'static inline uint8_t NativeNormalFracBits(' not in s:
    anchor = 'struct NativeVectorPlan {'
    assert s.count(anchor) == 1, s.count(anchor)
    s = s.replace(anchor, helper + anchor, 1)
old = 'const NativeVectorPlan nrm(r.vat.nrmMask,r.vat.nrmType,0,'
new = 'const NativeVectorPlan nrm(r.vat.nrmMask,r.vat.nrmType,NativeNormalFracBits(r.vat.nrmType),'
n = s.count(old)
if n:
    assert n == 2, n                                   # both decoders (normal and GPU-snapshot paths)
    s = s.replace(old, new)
expected=(4 if 'NATIVE_PACKED_DECODE_V1' in s else 2)+(1 if 'NATIVE_DECODE_CHUNKS_V1' in s else 0)
assert s.count(new) == expected, s.count(new)
f.write_text(s, errors='surrogateescape')
print('normal scale installed')
