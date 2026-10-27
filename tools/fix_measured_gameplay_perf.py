"""Cache immutable dispatch choices and retain bounded active texgen work."""
from pathlib import Path
port=Path(__file__).resolve().parents[1]
cache=(port/'runtime-overlays/dispatch_cache.inc').read_text()
choice_path=port/'runtime-overlays/dispatch_choice.inc'
original=choice_path.read_text()
old=original.split('// NATIVE_DISPATCH_CACHE_V1')[0].rstrip()+'\n'
old=old.replace('static DispatchChoice find_dispatch_target_uncached(uint32_t target)',
                'static DispatchChoice find_dispatch_target(uint32_t target)',1)
new=old.replace('static DispatchChoice find_dispatch_target(uint32_t target)',
                'static DispatchChoice find_dispatch_target_uncached(uint32_t target)',1)+'\n'+cache
if original!=new:choice_path.write_text(new)
for path in (port/'build/crash-generated/main_output.cpp',port/'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp'):
    text=path.read_text()
    if 'NATIVE_DISPATCH_CACHE_V1' not in text and 'NATIVE_DENSE_HOT_CACHE_V1' not in text:
        def emit(s):return ''.join('    out << "'+line.replace(chr(92),chr(92)*2).replace('"',chr(92)+'"').replace('\n',chr(92)+'n')+'";\n' for line in s.splitlines(True))
        a,b=(emit(old),emit(new)) if path.name=='recompiler.cpp' else (old,new)
        assert text.count(a)==1, str(path)
        text=text.replace(a,b,1)
    if path.read_text()!=text:path.write_text(text)
p=port/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp';text=p.read_text()
anchor='static void NativeTexGen(const GXState& state, const VertexData& vertex, float out[8][3]) {\n'
old=anchor+'  for (unsigned t=0;t<8;++t) {'
new=anchor+'''  // NATIVE_ACTIVE_TEXGEN_WORK_V1: shader consumes only active generators.
  const unsigned count=std::min(8u,std::max(1u,unsigned(state.bp[0]&15u)));
  for (unsigned t=0;t<count;++t) {'''
if 'NATIVE_ACTIVE_TEXGEN_WORK_V1' not in text and 'texgen_count' not in text:  # the vertex-texgens stage may already limit the loop
    assert text.count(old)==1
    text=text.replace(old,new,1)
if p.read_text()!=text:p.write_text(text)
p=port/'tools/build_native.py';text=p.read_text()
if "run('native-measured-gameplay-perf-stage'" not in text:
    anchor="run('native-game-build',"
    assert text.count(anchor)==1
    text=text.replace(anchor,"run('native-measured-gameplay-perf-stage',[sys.executable,PORT/'tools/fix_measured_gameplay_perf.py'])\n"+anchor,1)
if p.read_text()!=text:p.write_text(text)
print('Tagged dispatch cache and late active-texgen stage installed.')
