"""Check disjoint full-tail CPU attribution without ETW or a running game."""
import unittest
from summarize_cpu_etl import symbol_group,aggregate_symbols

class Groups(unittest.TestCase):
    def test_precedence(self):
        cases={'func_802DADD0':'func_*',
               'native_guest_resume_803143EC(nwii::runtime::CPUContext&)':'native_guest_*',
               'native_block_803CAC2C_803CAC2C(nwii::runtime::CPUContext&)':'native_block_*',
               'nwii::runtime::interpret_step(nwii::runtime::CPUContext&)':'interpreter',
               'nwii::runtime::native_call_indirect_cached(nwii::runtime::CPUContext&, unsigned int)':'nwii::runtime::*',
               '<external module>':'external','<before first symbol>':'unresolved native',
               'pthread_mutex_lock':'other host helpers'}
        for name,want in cases.items():
            with self.subTest(name=name):self.assertEqual(symbol_group(name),want)
    def test_tail_and_conservation(self):
        rows=[dict(name=f'func_{0x80000000+4*i:08X}',samples=1) for i in range(80)]
        rows+=[dict(name='<external module>',samples=20)]
        groups=aggregate_symbols(rows,100)
        self.assertEqual({r['group']:r['samples'] for r in groups},{'func_*':80,'external':20})
        self.assertEqual(sum(r['share'] for r in groups),1)
        with self.assertRaises(ValueError):aggregate_symbols(rows,99)

if __name__=='__main__':unittest.main()
