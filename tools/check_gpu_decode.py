"""Compare production GPU raw decoding normalized through its snapshot with CPU decoding."""
from pathlib import Path
from port_paths import default_storage
import json,os,re,subprocess
p=Path(__file__).resolve().parents[1];base=default_storage()
w=base/'gpu-decode-tests';w.mkdir(exist_ok=True)
source=(base/'index-tuple-tests/decoder.cpp').read_text()
source=source.replace('uint64_t reused_draws=0,saved_vertices=0;', 'uint64_t reused_draws=0,saved_vertices=0,gpu_draws=0;')
source=source.replace('FifoParser::DecodeDraw(c);digest(c);','FifoParser::DecodeDraw(c);if(c.native_gpu_snapshot)++gpu_draws;digest(c);')
source=source.replace('c.raw=std::make_shared<DrawRaw>();','c.native_packed_inputs=1; c.raw=std::make_shared<DrawRaw>();')
source=source.replace('const auto&v=c.NativeVertex(i);','''auto v=c.NativeVertex(i);
if(c.native_gpu_snapshot){const auto& r=*c.native_gpu_snapshot;
 const float* m=r.snap_pos_bank.empty()?r.snap_pos_mtx:r.snap_pos_bank.data()+4*v.posMtxIdx;
 float pos[3];for(unsigned row=0;row<3;++row)pos[row]=m[row*4]*v.pos[0]+m[row*4+1]*v.pos[1]+m[row*4+2]*v.pos[2]+m[row*4+3];
 memcpy(v.pos,pos,sizeof(pos));
 if(v.has_norm){const float* n=r.snap_nrm_bank.empty()?r.snap_nrm_mtx:r.snap_nrm_bank.data()+3*(v.posMtxIdx&31);
 float normal[3];for(unsigned row=0;row<3;++row)normal[row]=n[row*3]*v.norm[0]+n[row*3+1]*v.norm[1]+n[row*3+2]*v.norm[2];memcpy(v.norm,normal,sizeof(normal));}v.pre_xf=true;
}''')
cut=source.index('for(unsigned sharing:{1u,2u,3u,8u})')
source=source[:cut]+'std::cout<<"GPU_DRAWS "<<gpu_draws<<\'\\n\';return 0;}\n'
(w/'fixture.cpp').write_text(source)
marker='# NATIVE_GPU_DECODE_TEST_V1'
wrong=p/'build/crash-generated/CMakeLists.txt';old=wrong.read_text()
if marker in old:wrong.write_text(old[:old.index(marker)])
cm=p/'build/nwii-full-source/CMakeLists.txt';s=cm.read_text()
if marker not in s:
    s+='\n'+marker+f'\nadd_executable(crash_gpu_decode "{(w/"fixture.cpp").as_posix()}")\ntarget_link_libraries(crash_gpu_decode PRIVATE nwiiruntime)\ntarget_compile_options(crash_gpu_decode PRIVATE -O2 -fwrapv -fno-strict-aliasing -ffp-contract=off)\n'
    cm.write_text(s)
rows=[]
def run(cmd,env=None):
    r=subprocess.run(list(map(str,cmd)),env=env,capture_output=True,text=True,timeout=240)
    rows.append(dict(command=list(map(str,cmd)),exit_code=r.returncode,output=r.stdout+r.stderr))
    (p/'reports/gpu-decode-tests.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(r.stdout+r.stderr,flush=True)
    if r.returncode:raise SystemExit(r.returncode)
    return r.stdout
run(['cmake','--build',base/'toolchain','--target','crash_gpu_decode','-j','6'])
env={k:v for k,v in os.environ.items() if not k.startswith('NWII_')};env['NWII_NO_DIAG']='1'
env['NWII_NO_GPU_VERTEX']='1';before=run([base/'toolchain/crash_gpu_decode.exe'],env)
env.pop('NWII_NO_GPU_VERTEX');after=run([base/'toolchain/crash_gpu_decode.exe'],env)
env['NWII_SLOW_GPU_VALIDATION']='1';scan=run([base/'toolchain/crash_gpu_decode.exe'],env)
env['NWII_NO_PACKED_DECODE']='1';expanded=run([base/'toolchain/crash_gpu_decode.exe'],env)
assert re.search(r'DIFFERENTIAL.*',expanded)[0]==re.search(r'DIFFERENTIAL.*',after)[0]
assert re.search(r'DIFFERENTIAL.*',after)[0]==re.search(r'DIFFERENTIAL.*',scan)[0]
assert re.search(r'GPU_DRAWS.*',after)[0]==re.search(r'GPU_DRAWS.*',scan)[0]
assert re.search(r'DIFFERENTIAL.*',before)[0]==re.search(r'DIFFERENTIAL.*',after)[0]
assert int(re.search(r'GPU_DRAWS (\d+)',after)[1])>100
print('GPU decoder snapshot / fallback normalized vertex hashes agree; no game launched.')
