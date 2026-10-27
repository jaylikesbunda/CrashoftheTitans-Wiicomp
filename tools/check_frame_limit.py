"""Check guest threshold patch replay, launch policy and elapsed-time gating."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from fix_frame_limit import patch_guest_update, GUEST_MARKER, apply

PORT=Path(__file__).resolve().parents[1]
CANONICAL='''void func_802198C0(NWII_CTX_PARAM ctx) {
loc_802198E4: ;
    // 0x802198E4: C002A8B8
    ctx.fpr[0] = ctx.ps1[0] = ctx.mmu.read_f32(ctx.gpr[2] + -22344); // lfs
loc_802198E8: ;
    ctx.gpr[0] = ctx.gpr[4] + 1;
}
'''
SOURCE=r'''
#include "runtime/native_frame_limit.h"
#include <cstdio>
#include <bit>
#include <cstdint>
int main(){
 const float original=std::bit_cast<float>(0x3c888889u);
 const float threshold=native_frame_limit::MinimumUpdateSeconds(original);
 // Nonstandard guest thresholds must survive unchanged, including FP payloads.
 for(uint32_t bits:{0u,0x3d088889u,0x3dcccccdu,0x7fc01234u})
  if(std::bit_cast<uint32_t>(native_frame_limit::MinimumUpdateSeconds(std::bit_cast<float>(bits)))!=bits)return 1;
 float accumulated=0;double delivered=0;unsigned frames=0;
 // One second of actual elapsed time: no scaling or invented fixed dt.
 for(unsigned i=0;i<1000000;++i){
  accumulated=float(double(accumulated)+0.000001);
  if(accumulated>=threshold){++frames;delivered+=accumulated;accumulated=0;}
 }
 std::printf("%08x %.12g %u %.12g\n",std::bit_cast<uint32_t>(threshold),threshold,frames,delivered+accumulated);
}
'''


class PatchTests(unittest.TestCase):
    def test_canonical_and_promoted_replay(self):
        promoted=CANONICAL.replace('ctx.fpr[0] = ctx.ps1[0]','f0 = p0').replace('ctx.gpr[2]','g2')
        for text,count in [(CANONICAL,1),(promoted,1),(CANONICAL+promoted,2)]:
            patched,n=patch_guest_update(text)
            self.assertEqual(n,count)
            self.assertEqual(patched.count(GUEST_MARKER),count)
            self.assertEqual(patch_guest_update(patched),(patched,count))
            self.assertIn('ctx.gpr[0] = ctx.gpr[4] + 1;',patched)

    def test_drift_rejected(self):
        for text in [CANONICAL.replace('C002A8B8','C002A8BC'),CANONICAL.replace('-22344','-22340')]:
            with self.assertRaises(ValueError):patch_guest_update(text)

    def test_unrelated_load_unchanged(self):
        text=CANONICAL.replace('802198E4','8021991C')
        self.assertEqual(patch_guest_update(text),(text,0))

    def test_stage_replay(self):
        with tempfile.TemporaryDirectory(prefix='frame-limit-stage-') as directory:
            p=Path(directory);rt=p/'build/nwii-full-source/nWiiRuntime'
            for sub in ('include/runtime','src/core','src/hle'):(rt/sub).mkdir(parents=True)
            (p/'reference/guest-regions').mkdir(parents=True)
            (p/'reference/guest-regions/effect-exclusions.json').write_text('{"addresses":[]}')
            (p/'runtime-overlays').mkdir()
            (p/'runtime-overlays/native_frame_limit.h').write_bytes((PORT/'runtime-overlays/native_frame_limit.h').read_bytes())
            (rt/'src/core/main.cpp').write_text('// NATIVE_CUSTOM_FRAME_LIMIT_V1\n')
            (rt/'src/hle/ios.cpp').write_text('// NATIVE_CUSTOM_VI_PERIOD_V1 NATIVE_CUSTOM_VI_PERIOD_OUTER_V1\n')
            generated=p/'build/crash-generated';generated.mkdir()
            (generated/'output_40.cpp').write_text('loc_80218330: ;\n    ctx.lr = 0x80218334; // save return address\nloc_80218364: ;\n    ctx.lr = 0x80218368; // save return address\n'+CANONICAL)
            apply(p)
            before={str(f.relative_to(p)):f.read_bytes() for f in p.rglob('*') if f.is_file()}
            stamp=(rt/'include/runtime/native_frame_limit.h').stat().st_mtime_ns
            apply(p)
            self.assertEqual(before,{str(f.relative_to(p)):f.read_bytes() for f in p.rglob('*') if f.is_file()})
            self.assertEqual(stamp,(rt/'include/runtime/native_frame_limit.h').stat().st_mtime_ns)

    def test_promoter_keeps_helper_local(self):
        from promote_regs import promote
        original=CANONICAL.replace('    ctx.gpr[0] = ctx.gpr[4] + 1;','    ctx.pc = ctx.lr; return;')
        original=original.replace('loc_802198E4: ;','    ++ctx.inst_count;\nloc_802198E4: ;')
        patched,_=patch_guest_update(original)
        body,stats=promote(patched.split('\n',1)[1].rstrip(),True,True,None)
        self.assertIsNotNone(body,stats)
        self.assertIn('native_frame_limit::MinimumUpdateSeconds(ctx.mmu.read_f32(g2 + -22344))',body)
        self.assertEqual(stats['barriers'],0)


def check_runtime(work):
    work.mkdir(parents=True,exist_ok=True)
    # Stub only HUD timing; the production frame-limit header is tested verbatim.
    include=work/'include/runtime';include.mkdir(parents=True,exist_ok=True)
    (include/'native_hud.h').write_text('#pragma once\n#include <atomic>\n#include <cstdint>\nnamespace native_hud {inline std::atomic<bool> on{false};inline uint64_t now_us(){return 0;}}\n')
    (include/'native_frame_limit.h').write_bytes((PORT/'runtime-overlays/native_frame_limit.h').read_bytes())
    source=work/'check.cpp';source.write_text(SOURCE);exe=work/'check.exe'
    result=subprocess.run([shutil.which('g++'),'-std=c++20','-O2','-I'+str(work/'include'),str(source),'-o',str(exe)],capture_output=True,text=True)
    if result.returncode:raise ValueError(result.stderr)
    cases=[(None,60),('',60),('invalid',60),('nan',60),('inf',60),('9',60),('1001',60),('120junk',60),
           ('60',60),('120',120),('144',144),('30',30),('10',10),('1000',1000)]
    rows=[]
    for value,fps in cases:
        env=dict(os.environ);env.pop('NWII_FPS_LIMIT',None)
        if value is not None:env['NWII_FPS_LIMIT']=value
        result=subprocess.run([exe],env=env,capture_output=True,text=True,check=True)
        bits,threshold,frames,seconds=result.stdout.split();threshold=float(threshold);frames=int(frames);seconds=float(seconds)
        # The unchanged float accumulator rounds one million tiny additions.
        if abs(threshold-1/fps)>1e-8 or abs(frames-fps)>1 or abs(seconds-1)>0.002:
            raise ValueError((value,result.stdout))
        if fps==60 and bits!='3c888889':raise ValueError('Original threshold changed')
        rows.append(dict(setting=value,threshold_seconds=threshold,simulated_frames=frames,accounted_seconds=seconds))
    (work/'results.json').write_text(json.dumps(rows,indent=2))
    print(f'PASS {len(rows)} launch settings; elapsed time preserved; original bits retained for defaults.')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--work',type=Path);args=ap.parse_args()
    result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(PatchTests))
    if not result.wasSuccessful():raise SystemExit(1)
    if args.work:
        work=args.work.resolve()
        if work==PORT or PORT in work.parents:raise ValueError('Test artifacts must be outside repo')
        check_runtime(work)
    else:
        with tempfile.TemporaryDirectory(prefix='native-frame-limit-') as directory:check_runtime(Path(directory))
