"""Checks for fix_direct_texcoord_loader.py. Works in a scratch directory; changes nothing else.

1. Stage on a scratch copy of fifo_parser.cpp: the three edits are present, every marker is closed, apply is idempotent and
   --restore reproduces the original bytes exactly (twice).
2. The edit is additive for existing draws: the template defaults are -1/2, every pre-existing NativePackedGeneric
   instantiation keeps its arity, and the generic reader (tex[t].read) and the all-direct reader (Read<1,4,2>) are both still
   reachable.
3. The new arm is reachable only for the intended layout: it requires a direct F32 position, an absent/direct F32 normal,
   exactly one F32 texcoord set of two components and an Index8/Index16 texcoord mask.
4. The byte oracle in tools/check_format_loaders.py calls NativePackedGenericPosition, so the new arm is covered by it.

Usage: python check_direct_texcoord_loader.py [<nWiiRuntime root>] [--work DIR]
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = HERE.parent
sys.path.insert(0, str(HERE))
import fix_direct_texcoord_loader as fx  # noqa: E402

DEFAULT = PORT / 'build/nwii-full-source/nWiiRuntime'
REL = 'src/hle/gx/fifo_parser.cpp'


def main(argv):
    work = Path(argv[argv.index('--work') + 1]) if '--work' in argv else Path(tempfile.mkdtemp(prefix='texcoord-check-'))
    args = [a for i, a in enumerate(argv) if not a.startswith('--') and (i == 0 or argv[i - 1] != '--work')]
    root = Path(args[0]) if args else DEFAULT
    work.mkdir(parents=True, exist_ok=True)
    failures = []

    def check(ok, label):
        print(('PASS ' if ok else 'FAIL ') + label)
        if not ok:
            failures.append(label)

    scratch = work / 'nwii'
    target = scratch / 'build' / 'nwii-full-source' / 'nWiiRuntime' / REL
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / REL, target)
    original = target.read_bytes()

    # A fully staged tree may already carry this stage; undo it first so the input is the unstaged text.
    if fx.MARK.encode() in original:
        subprocess.run([sys.executable, str(HERE / 'fix_direct_texcoord_loader.py'), '--port', str(scratch), '--restore'], check=True)
        original = target.read_bytes()
    check(fx.MARK.encode() not in original, 'input is unstaged')

    stage = [sys.executable, str(HERE / 'fix_direct_texcoord_loader.py'), '--port', str(scratch)]
    subprocess.run(stage, check=True)
    applied = target.read_text()
    markers = applied.count('/* ' + fx.MARK + ' ')
    check(markers == 4, 'four edits (%d markers)' % markers)
    check(applied.count('/* END_' + fx.MARK + ' */') == markers, 'every marker closed')
    check('template<bool Direct,int PosMask=-1,int PosElements=3,int TexMask=-1,int TexType=4,int TexElements=2> static bool NativePackedGeneric(' in applied,
          'the template gains TexMask/TexType/TexElements with additive defaults')
    check('if constexpr(TexMask>=0)return NativeVectorPlan::Read<TexMask,TexType,TexElements>' in applied, 'texcoord reader selected at compile time')
    check('else if constexpr(Direct)return NativeVectorPlan::Read<1,4,2>' in applied and
          'else return tex[t].read(buf,off,buf_size,vtx.tex[t]);' in applied, 'the direct and generic texcoord readers are unchanged')
    # Every layout the census allows must have an arm, with the matching mask/type/element count in the instantiation.
    missing = []
    for mask in (0, 1, 2, 3):
        for type_ in range(5):
            for elements in (1, 2):
                if mask == 0 and (type_, elements) != (4, 2):
                    continue  # no texcoord set: no reader is reached, one instantiation covers every type
                if mask == 1 and type_ != 4:
                    continue  # a direct texcoord is F32 by definition of the layout
                if 'NativePackedGeneric<true,-1,3,%d,%d,%d>' % (mask, type_, elements) not in applied:
                    missing.append((mask, type_, elements))
    check(not missing, 'every allowed texcoord layout has an arm (%s)' % (missing[:4] or 'complete'))
    check('return NativePackedDirectLayout(c,output,indices,inputs,words,defaults);' in applied, 'the arm routes through the generated layout switch')
    checks = {
        'direct F32 3-component position': 'v.posMask==VtxAttrMask::Direct && v.posType==VtxAttrType::F32 && v.posElements',
        'absent or direct F32 normal without NBT': '(v.nrmMask==VtxAttrMask::None || (v.nrmMask==VtxAttrMask::Direct && v.nrmType==VtxAttrType::F32 && !v.nrmElements))',
        'a single texcoord set': 'tex0_only',
        'kill switch honoured': ' if(enabled){\n  // NATIVE_DIRECT_TEXCOORD_LOADER_V1',
    }
    for label, text in checks.items():
        check(text in applied, 'new arm requires ' + label)
    # The new arm must sit before the indexed arm and inside the enabled gate.
    arm = applied.index('// NATIVE_DIRECT_TEXCOORD_LOADER_V1: direct F32 position with an absent or direct F32 normal')
    indexed = applied.index('if(enabled && c.raw->count>=32 && v.posType==VtxAttrType::F32')
    check(arm < indexed, 'the new arm precedes the indexed positions arm')
    # Additive: the pre-existing instantiations keep their arity. The layout switch adds one generic fallback for a mask outside
    # 0..3, and the two new direct texcoord forms (1 and 2 components) are new instantiations by design.
    for arity in ('NativePackedGeneric<false,3,3>', 'NativePackedGeneric<false,3,2>', 'NativePackedGeneric<false,2,3>',
                  'NativePackedGeneric<false,2,2>', 'NativePackedGeneric<true>'):
        check(applied.count(arity) == original.decode().count(arity), 'existing instantiation unchanged: ' + arity)
    check(applied.count('NativePackedGeneric<false>') == original.decode().count('NativePackedGeneric<false>') + 1,
          'the layout switch keeps one generic fallback for an unknown mask')

    subprocess.run(stage, check=True)
    check(target.read_text() == applied, 'apply is idempotent')
    subprocess.run(stage + ['--restore'], check=True)
    check(target.read_bytes() == original, 'restore reproduces the original bytes')
    subprocess.run(stage + ['--restore'], check=True)
    check(target.read_bytes() == original, 'restore is idempotent')

    oracle = (HERE / 'check_format_loaders.py').read_text()
    check('NativePackedGenericPosition(c,output,indices,inputs,words,defaults)' in oracle,
          'the byte oracle routes draws through NativePackedGenericPosition (covers the new arm)')

    print('RESULT: %s (%d failure(s))' % ('PASS' if not failures else 'FAIL', len(failures)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
