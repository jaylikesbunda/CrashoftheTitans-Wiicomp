"""Stage live render timer/event selection and correlated frame-trace stages."""
from pathlib import Path
import argparse,base64,re
MARK='NATIVE_RENDER_PACING_V1'
BLOCK=re.compile(r'/\* '+MARK+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARK+r' \*/',re.S)

def restore(source):
    result=BLOCK.sub(lambda m:base64.b64decode(m[1]).decode(),source)
    if MARK in result:raise ValueError('Partial render pacing patch')
    return result

def replace(source,old,new):
    if source.count(old)!=1:raise ValueError(('Render pacing anchor',old[:100],source.count(old)))
    return source.replace(old,'/* '+MARK+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+MARK+' */',1)

def main_source(source):
    source=restore(source)
    source=replace(source,'#include "runtime/native_hud_gl.h"','#include "runtime/native_render_wait.h"\n#include "runtime/native_hud_gl.h"')
    anchor='  std::thread cpu_thread(cpu_thread_func, ctx.get());'
    source=replace(source,anchor,'  nwii::runtime::native_render_wait::Initialize();\n'+anchor)
    source=replace(source,'    native_trace::control();','    nwii::runtime::native_render_wait::Poll();\n    native_trace::control();')
    pattern=re.compile(r'      const auto wait_deadline=std::min\(field_deadline,wait_now\+std::chrono::milliseconds\(2\)\);.*?native_hud::record_wait\(false,requested\?0u:after>=field_deadline\?2u:1u,native_hud::now_us\(\)-wait_start,late\);}',re.S)
    matches=list(pattern.finditer(source))
    if len(matches)!=1:raise ValueError(('Render wait block',len(matches)))
    old=matches[0][0]
    begin=old.index('       requested=')
    end=old.index(';}')+1
    new=old[:begin]+'''       requested=nwii::runtime::native_render_wait::Wait(drain_lock,nwii::runtime::g_gx_drain_cv,
                 nwii::runtime::g_gx_drain_request,field_deadline,wait_deadline);'''+old[end:]
    new=new.replace('const uint64_t late=after>wait_deadline?', 'const auto effective_deadline=nwii::runtime::native_render_wait::last_deadline;\n       const uint64_t late=after>effective_deadline?').replace('(after-wait_deadline).count()', '(after-effective_deadline).count()')
    source=replace(source,old,new)
    anchor='''        if (!headless) { glBindFramebuffer(GL_FRAMEBUFFER, efb_fbo); glViewport(0, 0, NativeEfbWidth(), NativeEfbHeight()); }
        ProcessGXFifo();'''
    source=replace(source,anchor,anchor.replace('        ProcessGXFifo();','        nwii::runtime::native_render_wait::DrainStart();\n        ProcessGXFifo();'))
    return source

def hud_source(source):
    source=restore(source)
    anchor='#include "runtime/native_pipeline_stats.h"'
    source=replace(source,anchor,anchor+'\n#include "runtime/native_render_wait.h"')
    anchor='  t += native_hud::stutters.report();'
    source=replace(source,anchor,'''  t += fmt("Render wait: %s (launcher Frame pacing applies live). Wake stages require NWII_FRAME_TRACE.\\n",
           nwii::runtime::native_render_wait::Mode());
'''+anchor)
    anchor='  j += "\\\"gx_optimization_work\\\":"+native_gx_work::Json(GxMetricFrames())+",";'
    return replace(source,anchor,'  j += "\\\"render_wait\\\":\\\"" + std::string(nwii::runtime::native_render_wait::Mode()) + "\\\",";\n'+anchor)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--restore',action='store_true');a=ap.parse_args()
    rt=a.port/'build/nwii-full-source/nWiiRuntime'
    if not a.restore:
        for name in ('native_render_wait.h','native_drain_signal.h'):
            dest=rt/'include/runtime'/name;data=(a.port/'runtime-overlays'/name).read_bytes()
            if not dest.exists()or dest.read_bytes()!=data:dest.write_bytes(data)
    edits=[]
    for rel,fn in [('src/core/main.cpp',main_source),('include/runtime/native_hud_gl.h',hud_source)]:
        dest=rt/rel
        if a.restore and not dest.exists():continue
        old=dest.read_text();edits.append((dest,old,restore(old)if a.restore else fn(old)))
    for dest,old,new in edits:
        if old!=new:dest.write_text(new)
    print('Render pacing '+('restored'if a.restore else'staged: NWII_RENDER_WAIT=cv|timer; live NWII_RENDER_WAIT_FILE; field deadlines unchanged.'))
if __name__=='__main__':main()
