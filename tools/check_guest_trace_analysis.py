"""Check observation boundaries and incomplete-capture rejection."""
import unittest
from analyze_guest_region_trace import analyze,complete_roots
from trace_guest_ir import Instruction


def capture():
    rows=[]
    for invocation in range(64):
        for pc,kind in [(0x100,1),(0x100,0),(0x200,0),(0x204,0),(0x104,0),
                        (0x300,3),(0x108,4),(0x108,0),(0x1000,2)]:
            rows.append(dict(pc=f'{pc:08X}',kind=kind,owner=0,invocation=invocation,ic=len(rows)))
    return dict(state=3,completed=64,count=len(rows),rows=rows)


class AnalysisTests(unittest.TestCase):
    def test_cross_function_integer_path_stops_at_opaque_and_observer(self):
        units={0x100:Instruction(0x100,0x48000101), # bl 0x200
               0x200:Instruction(0x200,0x38630001), # addi r3,r3,1
               0x204:Instruction(0x204,0x4e800020), # blr to 0x104
               0x104:Instruction(0x104,0xc0210014), # FP: explicit unlifted boundary
               0x108:Instruction(0x108,0x38630001)}
        segments,counts,unknown,observers=analyze(capture(),units)
        self.assertEqual(counts['selected_instructions'],320)
        self.assertEqual(counts['integer_instructions'],256)
        self.assertEqual(counts['internal_direct_calls'],64)
        self.assertEqual(unknown,{'Unsupported primary opcode 48':64})
        self.assertEqual(observers,{'indirect_before':64,'indirect_after':64})
        first=segments[0]
        self.assertEqual(first['pcs'],[0x100,0x200,0x204])
        self.assertEqual(first['calls'],1)
        self.assertEqual(first['reason'],'unsupported')

    def test_reject_overflow_and_partial_records(self):
        data=capture();data['state']=4
        with self.assertRaisesRegex(ValueError,'complete'):analyze(data,{})
        data=capture();data['rows'].pop()
        with self.assertRaisesRegex(ValueError,'row count'):analyze(data,{})
        data=capture();data['rows'][-1]['kind']=0
        with self.assertRaisesRegex(ValueError,'Incomplete'):analyze(data,{})

    def test_explicit_overflow_prefix_discards_outside_and_unbalanced_tail(self):
        data=capture();data['state']=4;data['completed']=2
        data['rows']=[row for row in data['rows'] if row['invocation']<2]
        outside=dict(data['rows'][0],kind=0)
        data['rows'].insert(0,outside)
        data['rows'].extend([dict(data['rows'][1],invocation=2),dict(data['rows'][2],invocation=2)])
        data['count']=len(data['rows'])
        rows,quality=complete_roots(data,allow_prefix=True)
        self.assertEqual(len(rows),18)
        self.assertEqual(quality['completed_roots'],2)
        self.assertEqual(quality['outside_root_rows_removed'],1)
        self.assertEqual(quality['unfinished_tail_rows_removed'],2)
        self.assertTrue(quality['complete_prefix'])
        data['rows'][18]['kind']=0
        with self.assertRaisesRegex(ValueError,'Incomplete'):complete_roots(data,allow_prefix=True)


if __name__=='__main__':unittest.main()
