"""Large retained-geometry spans share the existing exact OS dirty tracker."""
from pathlib import Path
import argparse,base64,re
MARKER='NATIVE_GEOMETRY_WATCH_V1'
BLOCK=re.compile(r'/\* '+MARKER+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARKER+r' \*/',re.S)
def restore(s):
    return BLOCK.sub(lambda m:base64.b64decode(m[1]).decode(),s)
def wrap(old,new):
    return '/* '+MARKER+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'/* END_'+MARKER+' */'
def apply(s):
    s=restore(s)
    changes={
      '#include "runtime/cpu_context.h"':
      '#include "runtime/cpu_context.h"\n#include "runtime/native_geometry_watch.h"',
      'struct NativeGeometrySpan {const uint8_t* source=nullptr;std::vector<uint8_t> bytes;};':
      'struct NativeGeometrySpan {const uint8_t* source=nullptr;std::vector<uint8_t> bytes;mutable uint64_t verified_gen=0;};',
      'for(const auto& span:entry.spans)if(std::memcmp(span.source,span.bytes.data(),span.bytes.size()))return false;':
      'for(const auto& span:entry.spans)if(!native_geometry_watch::Unchanged(span.source,span.bytes.data(),span.bytes.size(),&span.verified_gen))return false;',
      'span.source=attr.base+begin;span.bytes.assign(span.source,span.source+end-begin);':
      'span.source=attr.base+begin;const uint64_t newest=native_geometry_watch::Newest(span.source,end-begin);span.verified_gen=newest==UINT64_MAX?0:newest;span.bytes.assign(span.source,span.source+end-begin);'
    }
    for old,new in changes.items():
        if s.count(old)!=1:raise ValueError('Geometry watch anchor changed: '+old)
        s=s.replace(old,wrap(old,new))
    return s
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--restore',action='store_true');a=ap.parse_args()
    p=a.port/'build/nwii-full-source/nWiiRuntime/src/hle/gx/fifo_parser.cpp'
    s=p.read_text();n=restore(s) if a.restore else apply(s)
    if n!=s:p.write_text(n)
    if not a.restore:
        h=a.port/'build/nwii-full-source/nWiiRuntime/include/runtime/native_geometry_watch.h'
        h.write_text((Path(__file__).resolve().parents[1]/'runtime-overlays/native_geometry_watch.h').read_text())
    print('Geometry dirty watch '+('restored' if a.restore else 'installed'))
if __name__=='__main__':main()
