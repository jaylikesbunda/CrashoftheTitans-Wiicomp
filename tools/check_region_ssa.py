"""Differential checks for cross-call state and precise region exits."""
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'compiler'))
from region_ssa import Region, const, emit_cpp, expression as E

FIELDS=tuple('r'+str(i) for i in range(8))+('lr','ctr','lt','gt','eq','so')


def pilot():
    # Synthetic guest caller/callee, not a corpus or frozen benchmark target.
    # The callee's CR result is visible on a cold return mismatch, but is
    # overwritten by the caller on the expected path. LR is saved in private
    # guest RAM, so its restore can be forwarded across the former call.
    r=Region(FIELDS)
    old_sp=r.get('r1');r.set('r1',E('add',old_sp,-16))
    r.write32(0x100,r.get('r1'),old_sp,private=True)
    r.set('r0',r.get('lr'))
    slot=E('add',r.get('r1'),20)
    r.write32(0x108,slot,r.get('r0'),private=True)
    r.set('lr',0x118)
    r.set('r3',E('add',r.get('r3'),r.get('r4')))
    r.set('lt',E('lts',r.get('r3'),0));r.set('gt',E('lts',0,r.get('r3')));r.set('eq',E('eq',r.get('r3'),0))
    # Model a guest return target supplied by an admitted callee. An unexpected
    # return must expose all callee updates before leaving the region.
    r.guard(0x208,E('eq',r.get('r6'),0),True,0x20c)
    r.set('r0',r.read32(0x118,slot,private=True))
    r.set('lr',r.get('r0'));r.set('r1',E('add',r.get('r1'),16))
    r.set('lt',0);r.set('gt',0);r.set('eq',1)
    r.finish(0x120)
    return r


def reference(state,memory):
    s=dict(state);mem=dict(memory);writes=[]
    old=s['r1'];s['r1']=(old-16)&0xffffffff
    mem[s['r1']]=old;writes.append((s['r1'],old))
    s['r0']=s['lr'];address=(s['r1']+20)&0xffffffff
    mem[address]=s['r0'];writes.append((address,s['r0']))
    s['lr']=0x118;s['r3']=(s['r3']+s['r4'])&0xffffffff
    value=s['r3'] if s['r3']<0x80000000 else s['r3']-0x100000000
    s['lt']=int(value<0);s['gt']=int(value>0);s['eq']=int(value==0)
    if s['r6']!=0:return dict(reason='guard',pc=0x20c,state=s),mem,writes
    s['r0']=mem[address];s['lr']=s['r0'];s['r1']=(s['r1']+16)&0xffffffff
    s['lt']=0;s['gt']=0;s['eq']=1
    return dict(reason='exit',pc=0x120,state=s),mem,writes


class RegionTests(unittest.TestCase):
    def test_cross_call_forwarding_and_cold_exit_state(self):
        region=pilot();rng=random.Random(0x726567696f6e)
        self.assertEqual(region.stats['forwarded_loads'],1)
        hot=region.hot_values()
        self.assertFalse(any(v.op=='lts' for v in hot))
        for trial in range(3000):
            state={f:rng.getrandbits(32) for f in FIELDS}
            state.update(r1=0x80100000+rng.randrange(1024)*16,r6=trial%2)
            for f in ('lt','gt','eq','so'):state[f]&=1
            memory={};writes=[]
            expected,expected_memory,expected_writes=reference(state,memory)
            def write(address,value):memory[address]=value;writes.append((address,value))
            actual=region.execute(state,lambda address:memory.get(address,0),write,private_ok=lambda a,n:0x800f0000<=a<0x80110000)
            self.assertEqual(actual,expected)
            self.assertEqual(memory,expected_memory)
            self.assertEqual(writes,expected_writes)

    def test_aliasing_write_invalidates_forwarded_value(self):
        r=Region(('a','b','result'));r.write32(4,r.get('a'),1,private=True)
        r.write32(8,r.get('b'),2,private=True)
        r.set('result',r.read32(12,r.get('a'),private=True));r.finish(16)
        memory={}
        result=r.execute(dict(a=0x100,b=0x100,result=0),lambda a:memory[a],lambda a,v:memory.__setitem__(a,v))
        self.assertEqual(result['state']['result'],2)
        self.assertEqual(r.stats['forwarded_loads'],0)

    def test_observer_sees_prefix_and_invalidates_state_and_memory(self):
        r=Region(('a','value'));r.set('value',7);r.write32(4,r.get('a'),r.get('value'),private=True)
        r.observe(8,'callback');r.set('value',r.read32(12,r.get('a'),private=True));r.finish(16)
        memory={};seen=[]
        def observer(name,state):
            seen.append((name,dict(state)));state['a']=0x200;memory[0x200]=19
        result=r.execute(dict(a=0x100,value=3),lambda a:memory[a],lambda a,v:memory.__setitem__(a,v),observer)
        self.assertEqual(seen,[('callback',dict(a=0x100,value=7))])
        self.assertEqual(result['state'],dict(a=0x200,value=19))
        self.assertEqual(r.stats['forwarded_loads'],0)

    def test_stale_architectural_inputs_are_rejected_after_observer(self):
        r=Region(('a','b'));old=r.get('a');r.observe(4,'callback')
        with self.assertRaisesRegex(ValueError,'cannot cross'):r.set('b',old)

    def test_emitter_does_not_materialize_unchanged_fields(self):
        r=Region(('a','b'));r.set('a',17);r.finish(4)
        code=emit_cpp(r)
        self.assertIn('state.a=17u;',code)
        self.assertNotIn('state.b',code)

    def test_unqualified_read_invalidates_private_facts(self):
        r=Region(('a','result'));r.write32(4,r.get('a'),1,private=True)
        r.read32(8,0xdead0000);r.set('result',r.read32(12,r.get('a'),private=True));r.finish(16)
        memory={}
        def read(address):
            if address==0xdead0000:memory[0x100]=9;return 0
            return memory[address]
        result=r.execute(dict(a=0x100,result=0),read,lambda a,v:memory.__setitem__(a,v))
        self.assertEqual(result['state']['result'],9)

    def test_failed_private_memory_proof_exits_before_write(self):
        r=Region(('a','value'));r.set('value',17);r.write32(4,r.get('a'),r.get('value'),private=True);r.finish(8)
        writes=[]
        result=r.execute(dict(a=0xc008000,value=3),lambda a:0,lambda a,v:writes.append((a,v)),private_ok=lambda a,n:False)
        self.assertEqual(result,dict(reason='memory',pc=4,state=dict(a=0xc008000,value=17)))
        self.assertEqual(writes,[])

    def test_modular_stack_address_normalization(self):
        r=Region(('sp',));original=r.get('sp')
        normalized=E('add',E('add',original,-16),20)
        self.assertEqual(normalized,E('add',original,4))
        self.assertEqual(E('add',const(0xffffffff),2),const(1))
        self.assertEqual(E('rotl',const(1),32),const(1))
        self.assertEqual(E('clz',const(0)),const(32))

    def test_compiled_region_matches_imperative_guest_caller_and_callee(self):
        cxx=shutil.which('g++')
        if not cxx:self.skipTest('g++ unavailable')
        emitted=emit_cpp(pilot())
        declarations=''.join('uint32_t '+f+';' for f in FIELDS)
        cpp=emitted+r'''
#include <map>
#include <vector>
#include <utility>
#include <cassert>
struct State { FIELD_DECLARATIONS };
struct Memory {
 std::map<uint32_t,uint32_t> words;
 std::vector<std::pair<uint32_t,uint32_t>> writes;
 unsigned reads=0;bool valid=true;
 bool TryRead32(uint32_t a,uint32_t& value,bool){if(!valid)return false;++reads;value=words[a];return true;}
 bool TryWrite32(uint32_t a,uint32_t value,bool){if(!valid)return false;words[a]=value;writes.emplace_back(a,value);return true;}
};
struct Runtime {bool Observe(const char*,State&){return true;}};
static RegionExit Reference(State& s,Memory& memory){
 const auto old=s.r1;s.r1-=16;memory.TryWrite32(s.r1,old,true);
 s.r0=s.lr;const auto address=s.r1+20;memory.TryWrite32(address,s.r0,true);
 s.lr=0x118;s.r3+=s.r4;
 const auto value=std::bit_cast<int32_t>(s.r3);
 s.lt=value<0;s.gt=value>0;s.eq=value==0;
 if(s.r6!=0)return {1,0x20c};
 memory.TryRead32(address,s.r0,true);s.lr=s.r0;s.r1+=16;
 s.lt=0;s.gt=0;s.eq=1;return {0,0x120};
}
int main(){
 uint32_t seed=0x31415926;auto next=[&](){seed=seed*1664525u+1013904223u;return seed;};
 for(unsigned t=0;t<5000;++t){
  State a{};INITIALIZE_FIELDS
  a.r1=0x80100000+(next()&0xff0);a.r6=t&1;
  State b=a;Memory ma,mb;Runtime runtime;
  auto ea=Reference(a,ma),eb=RunRegion(b,mb,runtime);
  assert(ea.reason==eb.reason&&ea.pc==eb.pc);CHECK_FIELDS
  assert(ma.words==mb.words&&ma.writes==mb.writes);
  assert(mb.reads==0); // guest stack restore forwarded across the callee
 }
 State s{};s.r1=0x80100000;Memory memory;memory.valid=false;Runtime runtime;
 const auto exit=RunRegion(s,memory,runtime);
 assert(exit.reason==2&&exit.pc==0x100&&s.r1==0x800ffff0&&memory.words.empty());
}
'''
        cpp=cpp.replace('FIELD_DECLARATIONS',declarations)
        cpp=cpp.replace('INITIALIZE_FIELDS',''.join('a.'+f+'=next();' for f in FIELDS))
        cpp=cpp.replace('CHECK_FIELDS',''.join('assert(a.'+f+'==b.'+f+');' for f in FIELDS))
        with tempfile.TemporaryDirectory(prefix='guest-region-ssa-') as temp:
            work=Path(temp);(work/'test.cpp').write_text(cpp)
            for opt in ('-Os','-O2'):
                result=subprocess.run([cxx,'-std=c++20',opt,'-static',str(work/'test.cpp'),'-o',str(work/'test.exe')],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                result=subprocess.run([str(work/'test.exe')],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
