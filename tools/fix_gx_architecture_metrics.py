"""Per-draw denominators for architecture changes; no per-vertex atomics."""
from pathlib import Path
import argparse,base64,re
MARK='NATIVE_GX_ARCHITECTURE_METRICS_V1'
INCLUDE='#include "runtime/native_gx_work_metrics.h" // '+MARK+'\n'
BLOCK=re.compile(r'/\* '+MARK+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARK+r' \*/',re.S)
def restore(s):
    s=BLOCK.sub(lambda m:base64.b64decode(m[1]).decode(),s).replace(INCLUDE,'')
    if MARK in s:raise ValueError('Incomplete architecture metrics')
    return s
def replace(s,old,new,count=1):
    if s.count(old)!=count:raise ValueError(('Architecture metric anchor',old[:80],s.count(old),count))
    return s.replace(old,'/* '+MARK+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+MARK+' */')
def parser(s):
    s=restore(s)
    for mask in (2,):
        for n in (2,3):
            old=f'return NativePackedGeneric<false,{mask},{n}>(c,output,indices,inputs,words,defaults);'
            s=replace(s,old,'{native_gx_work::Add(native_gx_work::GenericPositionSpecialized); '+old+'}')
    old='return NativePackedGeneric<false>(c,output,indices,inputs,words,defaults);'
    s=replace(s,old,'native_gx_work::Add(native_gx_work::GenericPositionFallback); '+old)
    old='else if(!hit){\n bool direct='
    s=replace(s,old,'else if(!hit){\n native_gx_work::Add(native_gx_work::GenericPackedDraws);\n native_gx_work::Add(native_gx_work::GenericPackedVerticesAttempted,r.count);\n bool direct=')
    old='c.native_geometry_retained=hit; // GPU admission requires demonstrated reuse.'
    s=replace(s,old,'if(!hit){native_gx_work::Add(native_gx_work::FreshPackedVertices,output.size()/words);native_gx_work::Add(native_gx_work::FreshPackedBytes,output.size()*sizeof(float));}\n '+old)
    return INCLUDE+s
def renderer(s):
    s=restore(s)
    for old,metric in [('++counts.shader_requests;','ShaderHashRequests'),('++counts.shader_skips;','ShaderHashSkipped'),('++counts.xf_requests;','XfInputChecks'),('++counts.xf_unchanged;','XfInputUnchanged')]:
        s=replace(s,old,'native_gx_work::Add(native_gx_work::'+metric+'); '+old)
    return INCLUDE+s
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--restore',action='store_true');a=ap.parse_args();rt=a.port/'build/nwii-full-source/nWiiRuntime'
    changes=[]
    for rel,fn in [('src/hle/gx/fifo_parser.cpp',parser),('src/hle/gx/renderer_gl.cpp',renderer)]:
        p=rt/rel;s=p.read_text();changes.append((p,s,restore(s)if a.restore else fn(s)))
    for p,s,n in changes:
        if s!=n:p.write_text(n)
    print('GX architecture metrics '+('restored'if a.restore else'staged'))
if __name__=='__main__':main()
