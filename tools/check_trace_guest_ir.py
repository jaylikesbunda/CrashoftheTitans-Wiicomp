"""Word-level checks for the experimental integer trace frontend."""
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'compiler'))
from region_ssa import Region
from trace_guest_ir import FIELDS,Instruction,Unsupported,lower


class WordTests(unittest.TestCase):
    def state(self,seed):
        rng=random.Random(seed)
        return {field:rng.getrandbits(32) for field in FIELDS}

    def execute(self,words,state,memory):
        region=Region(FIELDS)
        for pc,word in words:
            self.assertNotEqual(lower(Instruction(pc,word),region,pc+4,set(pc for pc,_ in words)),'exit')
        region.finish(0x300)
        return region.execute(state,lambda address:memory.get(address,0),lambda address,value:memory.__setitem__(address,value))['state']

    def test_update_store_and_special_registers(self):
        # stwu r1,-16(r1); mflr r0; stw r0,20(r1); mtlr r3; mtctr r12.
        words=[(0x100,0x9421fff0),(0x104,0x7c0802a6),(0x108,0x90010014),
               (0x10c,0x7c6803a6),(0x110,0x7d8903a6)]
        for trial in range(500):
            state=self.state(trial);memory={};original_sp=state['r1']
            actual=self.execute(words,state,memory)
            self.assertEqual(actual['r1'],(original_sp-16)&0xffffffff)
            self.assertEqual(memory[actual['r1']],original_sp)
            self.assertEqual(memory[(actual['r1']+20)&0xffffffff],state['lr'])
            self.assertEqual(actual['r0'],state['lr'])
            self.assertEqual(actual['lr'],state['r3'])
            self.assertEqual(actual['ctr'],state['r12'])

    def test_integer_wrap_rotate_and_compare(self):
        # addi r3,r3,-1; rlwinm r4,r3,27,5,31; cmpwi r3,0.
        words=[(0x100,0x3863ffff),(0x104,0x5464d97e),(0x108,0x2c030000)]
        for trial in range(1000):
            state=self.state(trial);actual=self.execute(words,state,{})
            value=(state['r3']-1)&0xffffffff
            rotated=((value<<27)|(value>>5))&0xffffffff
            self.assertEqual(actual['r3'],value)
            self.assertEqual(actual['r4'],rotated&0x07ffffff)
            signed=value if value<0x80000000 else value-0x100000000
            self.assertEqual((actual['c0lt'],actual['c0gt'],actual['c0eq']),(int(signed<0),int(signed>0),int(signed==0)))
            self.assertEqual(actual['c0so'],state['c0so'])

    def test_conditional_guard_and_dynamic_return_exit(self):
        for equal in (0,1):
            state=self.state(9);state['c0eq']=equal
            region=Region(FIELDS)
            # beq 0x120; observed fall-through. Taken alternative must exit 0x120.
            lower(Instruction(0x100,0x41820020),region,0x104,{0x100,0x104,0x120})
            region.finish(0x104)
            result=region.execute(state,lambda a:0,lambda a,v:None)
            self.assertEqual((result['reason'],result['pc']),('guard',0x120) if equal else ('exit',0x104))
            self.assertEqual(result['state'],state)
        region=Region(FIELDS)
        lower(Instruction(0x108,0x4e800020),region,0x200,{0x108,0x200})
        region.finish(0x200)
        for target in (0x200,0x12345678):
            state=self.state(11);state['lr']=target
            result=region.execute(state,lambda a:0,lambda a,v:None)
            self.assertEqual((result['reason'],result['pc']),('exit',0x200) if target==0x200 else ('guard',target))

    def test_unknown_instruction_cannot_silently_change_state(self):
        region=Region(FIELDS);before=region.snapshot()
        with self.assertRaises(Unsupported):lower(Instruction(0x100,0xc0210014),region,0x104,{0x100,0x104})
        self.assertEqual(region.snapshot(),before)
        self.assertEqual(region.effects,[])


if __name__=='__main__':unittest.main()
