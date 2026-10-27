"""Focused checks for trace qualification, publication and bounded recording."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import guest_region_trace as T


class TraceTests(unittest.TestCase):
    def test_only_selected_functions_are_instrumented_after_promotion(self):
        source = '''void func_803061E0(NWII_CTX_PARAM ctx) {
    uint64_t ic;
    ic = ctx.inst_count;
loc_803061E0: ;
    nwii::runtime::native_call_indirect_cached(ctx, 383);
    return;
}
void func_80004000(NWII_CTX_PARAM ctx) {
loc_80004000: ;
    return;
}
'''
        with patch.object(T, 'qualified_owners', return_value=T.OWNERS):
            result, stats = T.instrument(source)
        self.assertEqual(len(stats), 1)
        self.assertIn('guest_region_trace::Record(0x803061E0,ic,0)', result)
        self.assertIn('guest_region_trace::Indirect(ctx,383,0)', result)
        self.assertIn('const bool region_trace_selected=guest_region_trace::BeginRoot(ctx);', result)
        self.assertIn('guest_region_trace::EndRoot(ctx,region_trace_selected);return;',result)
        self.assertNotIn('RootScope',result)
        self.assertEqual(result[result.index('void func_80004000'):], source[source.index('void func_80004000'):])
        with patch.object(T, 'qualified_owners', return_value=T.OWNERS):
            with self.assertRaisesRegex(ValueError, 'Already instrumented'):
                T.instrument(result)

    def test_resume_functions_record_context_counter_when_not_promoted(self):
        source = '''static void native_guest_resume_802CC108(NWII_CTX_PARAM ctx) {
loc_802CC108: ;
    ++ctx.inst_count;
}
'''
        with patch.object(T, 'qualified_owners', return_value=T.OWNERS):
            result, stats = T.instrument(source)
        self.assertIn('Record(0x802CC108,ctx.inst_count,1)', result)
        self.assertEqual(stats[0]['labels'], 1)

    def test_recorder_arming_nesting_limit_and_overflow(self):
        cxx = shutil.which('g++')
        if not cxx:
            self.skipTest('g++ unavailable')
        with tempfile.TemporaryDirectory(prefix='guest-region-trace-') as temp:
            root = Path(temp)
            (root/'runtime').mkdir()
            (root/'runtime/native_hud.h').write_text('#pragma once\n#include <atomic>\nnamespace native_hud{inline std::atomic<bool> on{true};}\n')
            (root/'runtime/cpu_context.h').write_text('#pragma once\n#include <cstdint>\nnamespace nwii::runtime{struct CPUContext{uint32_t pc=0x803061E0;uint64_t inst_count=7;};}\n')
            (root/'guest_region_trace.h').write_text(T.HEADER)
            cpp = '#include "guest_region_trace.h"\n#include <cassert>\n' + T.DEFINITIONS + r'''
namespace nwii::runtime {void native_call_indirect_cached(CPUContext& c,uint32_t){++c.inst_count;c.pc=123;}}
int main(){
 using namespace guest_region_trace;nwii::runtime::CPUContext c;
 {bool s=BeginRoot(c);EndRoot(c,s);}assert(!active && !started && guest_region_trace_count==0);
 native_hud::on=false;{bool s=BeginRoot(c);EndRoot(c,s);}assert(guest_region_trace_state==1);
 native_hud::on=true;
 {bool s=BeginRoot(c);assert(active && depth==1);{bool nested=BeginRoot(c);assert(depth==2);EndRoot(c,nested);}
  Record(0x803061E4,c.inst_count,2);Indirect(c,383,2);EndRoot(c,s);}
 assert(depth==0 && invocation==1 && guest_region_trace_completed==1);
 assert(guest_region_trace_rows[0].pc==0x803061E0);
 assert((guest_region_trace_rows[0].tag&255)==1);
 bool saw_before=false,saw_after=false;
 for(unsigned i=0;i<guest_region_trace_count;++i){auto r=guest_region_trace_rows[i];
  if((r.tag&255)==3){saw_before=true;assert(r.ic==7);}
  if((r.tag&255)==4){saw_after=true;assert(r.pc==123 && r.ic==8);}}
 assert(saw_before && saw_after);
 for(unsigned i=1;i<64;++i){bool s=BeginRoot(c);Record(0x803061E4,c.inst_count,0);EndRoot(c,s);}
 assert(!active && guest_region_trace_state==3 && guest_region_trace_completed==64);
 const auto end=guest_region_trace_count.load();{bool s=BeginRoot(c);EndRoot(c,s);}assert(guest_region_trace_count==end);
 active=true;depth=1;guest_region_trace_count=262143;Record(17,19,1);Record(23,29,2);
 assert(!active && guest_region_trace_state==4 && guest_region_trace_count==262144);
 assert(guest_region_trace_rows[262143].pc==17 && guest_region_trace_rows[262143].ic==19);
 invocation=63;depth=1;EndRoot(c,true);assert(guest_region_trace_state==4);
}
'''
            (root/'test.cpp').write_text(cpp)
            command = [cxx,'-std=c++20','-O2','-static','-I',str(root),str(root/'test.cpp'),'-o',str(root/'test.exe')]
            result = subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result = subprocess.run([str(root/'test.exe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':
    unittest.main()
