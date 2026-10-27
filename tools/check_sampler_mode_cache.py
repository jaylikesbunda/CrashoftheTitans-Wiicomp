"""Actual before/after helpers: GL parameters, shared-image pixels and driver calls."""
from pathlib import Path
from port_paths import default_storage
import json,subprocess
p=Path(__file__).resolve().parents[1];w=default_storage()/'sampler-mode-tests';w.mkdir(exist_ok=True)
rows=[]
for version,path in [('before',p/'runtime-overlays/sampler-mode-before.cpp'),('after',p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp')]:
 s=path.read_text();a=s.index('static void NativeApplyTextureSampler(');i=s.index('{',a)+1;depth=1
 while depth:depth+=(s[i]=='{')-(s[i]=='}');i+=1
 helper=s[a:i]
 source=(p/'tools/texture_sampler_tests.cpp').read_text().replace('#include "../runtime-overlays/texture_sampler.inc"','#include <cstdlib>\n#include <chrono>\n'+helper)
 # The production helper supports mips now; the archived baseline deliberately
 # has the old level-zero filter. Qualify each against its documented behavior.
 if 'NATIVE_MIPMAPS_V1' in helper:
  source=source.replace('check(value==((mode&128)?GL_LINEAR:GL_NEAREST),"min");',
   'const GLint filters[]={GL_NEAREST,GL_NEAREST_MIPMAP_NEAREST,GL_NEAREST_MIPMAP_LINEAR,GL_LINEAR,GL_LINEAR,GL_LINEAR_MIPMAP_NEAREST,GL_LINEAR_MIPMAP_LINEAR,GL_LINEAR};check(value==(std::getenv("NWII_NO_MIPMAPS")?((mode&128)?GL_LINEAR:GL_NEAREST):filters[(mode>>5)&7]),"min");')
 spy='''static PFNGLSAMPLERPARAMETERIPROC driver_sampler=nullptr;static uint64_t parameter_calls=0;
static void APIENTRY count_sampler(GLuint id,GLenum name,GLint value){++parameter_calls;driver_sampler(id,name,value);}
'''
 source=source.replace('int main(){',spy+'int main(){',1)
 source=source.replace(' unsigned passed=0,total=0;', ' driver_sampler=glad_glSamplerParameteri;glad_glSamplerParameteri=count_sampler;\n unsigned passed=0,total=0;',1)
 source=source.replace(' check(glGetError()==GL_NO_ERROR', ''' parameter_calls=0;auto begin=std::chrono::steady_clock::now();
for(unsigned i=0;i<200000;++i)NativeApplyTextureSampler(i%8,0x95u|(((i/8)%4)<<5));
std::printf("SAMPLER_REPEAT parameter_calls=%llu ns=%lld\\n",(unsigned long long)parameter_calls,(long long)std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-begin).count());
 check(glGetError()==GL_NO_ERROR''',1)
 cpp=w/(version+'.cpp');exe=w/(version+'.exe');cpp.write_text(source)
 cmd=json.loads((p/'reports/gx-depth-mask-tests.json').read_text())[0]['command'];cmd=[str(cpp) if x.endswith(('gx-depth-tests/test.cpp','gx-depth-tests\\test.cpp')) else str(exe) if x.endswith(('gx-depth-tests/test.exe','gx-depth-tests\\test.exe')) else x for x in cmd]
 for command in (cmd,[str(exe)]):
  r=subprocess.run(command,capture_output=True,text=True,timeout=180);rows.append(dict(version=version,command=command,exit_code=r.returncode,output=r.stdout+r.stderr));(p/'reports/sampler-mode-tests.json').write_text(json.dumps(rows,indent=2)+'\n');print((r.stdout+r.stderr)[-1600:],flush=True)
  if r.returncode:raise SystemExit(r.returncode)
