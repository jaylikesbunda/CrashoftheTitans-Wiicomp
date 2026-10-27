"""Distinguish real matrix/topology/state submissions from empty batch openings."""
from pathlib import Path
import argparse,base64,re
MARK='NATIVE_FLUSH_REASON_CENSUS_V1'
INCLUDE='#include "runtime/native_flush_census.h" // '+MARK+'\n'
BLOCK=re.compile(r'/\* '+MARK+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARK+r' \*/',re.S)

def restore(s):
    s=BLOCK.sub(lambda m:base64.b64decode(m[1]).decode(),s).replace(INCLUDE,'')
    if MARK in s:raise ValueError('Partial flush census')
    return s

def replace(s,old,new):
    if s.count(old)!=1:raise ValueError(('Flush census anchor',old[:80],s.count(old)))
    return s.replace(old,'/* '+MARK+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+MARK+' */')

def apply(s):
    original=s;s=restore(s)
    if s.count('FlushBatch();')!=4:raise ValueError('Flush call inventory changed')
    s=replace(s,'static void FlushBatch() {',
      'static void FlushBatch(native_flush_census::Reason reason=native_flush_census::Other) {\n'
      '  const bool census=native_hud::on.load(std::memory_order_relaxed)||native_perf_census::Enabled();\n'
      '  native_flush_census::Request(census,reason,NativeBatchSize()==0);')
    s=replace(s,'  native_hud::work_draw();',
      '  native_flush_census::Submit(census,reason,NativeBatchSize());\n  native_hud::work_draw();')
    s=replace(s,'  if(matrix_changed && s_batch_open){FlushBatch();s_batch_open=false;}',
      '  if(matrix_changed && s_batch_open){FlushBatch(gpu!=s_gpu_vertex_mode?native_flush_census::Mode:native_flush_census::Matrix);s_batch_open=false;}')
    s=replace(s,'  if (!s_batch_open || s_batch_prim != topology) {\n    FlushBatch();',
      '  if (!s_batch_open || s_batch_prim != topology) {\n    FlushBatch(s_batch_open?native_flush_census::Topology:native_flush_census::Opening);')
    s=replace(s,'        FlushBatch();\n        s_batch_open = false;',
      '        FlushBatch(cmd.type==GXCommandType::BPRegister?native_flush_census::Bp:\n'
      '                   cmd.type==GXCommandType::XFRegister?native_flush_census::Xf:native_flush_census::Other);\n'
      '        s_batch_open = false;')
    s=replace(s,'    if (s_batch_open) {\n      FlushBatch();',
      '    if (s_batch_open) {\n      FlushBatch(native_flush_census::Drain);')
    s=INCLUDE+s
    if MARK in original and s!=original:raise ValueError('Noncanonical flush census')
    return s

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--restore',action='store_true');ap.add_argument('--headers-only',action='store_true');args=ap.parse_args()
    rt=args.port/'build/nwii-full-source/nWiiRuntime'
    if not args.restore:
        source=Path(__file__).resolve().parents[1]/'runtime-overlays/native_flush_census.h'
        target=rt/'include/runtime/native_flush_census.h';target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists() or target.read_bytes()!=source.read_bytes():target.write_bytes(source.read_bytes())
    if args.headers_only:return
    path=rt/'src/hle/gx/renderer_gl.cpp';before=path.read_text();after=restore(before) if args.restore else apply(before)
    if before!=after:path.write_text(after)
    print('Flush census restored.' if args.restore else 'Real submissions and empty openings classified by boundary cause.')

if __name__=='__main__':main()
