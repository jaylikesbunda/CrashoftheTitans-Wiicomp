"""Check that A/B aggregation keeps CPU, counter units and elapsed time separate."""
import unittest
from capture_guest_execution_region import summarize


def rows(epoch=1,mode=1,count=80):
    out=[]
    for i in range(count):
        out.append(dict(epoch=epoch,mode=mode,time_ns=i*100_000_000,xfb=i*5,
            inst_count=i*32_000_000,
            threads={'guest':dict(cycles=i*256_000_000,kernel_100ns=0,user_100ns=i*750_000),
                     'render':dict(cycles=i*32_000_000,kernel_100ns=0,user_100ns=i*100_000)},
            scopes={'render:gx.parse':i*5000},attempts=[i*20,i*20],completed=[i*20,i*20],
            root_units=[i*10000,i*10000],root_tsc=[i*20000,i*20000]))
    return out


class CaptureTests(unittest.TestCase):
    def test_units_and_roles(self):
        windows,aggregate=summarize(rows())
        self.assertEqual(len(windows),1)
        w=windows[0]
        self.assertEqual(w['counter_units_per_xfb'],6_400_000)
        self.assertAlmostEqual(w['whole_frame_ms'],20)
        self.assertEqual(w['threads']['guest']['cpu_ms_per_xfb'],15)
        self.assertEqual(w['threads']['guest']['windows_cycles_per_counter_unit'],8)
        self.assertEqual(w['threads']['render']['cpu_ms_per_xfb'],2)
        self.assertEqual(w['scopes']['render:gx.parse'],1)
        self.assertEqual(w['root']['elapsed_tsc_ticks_per_counter_unit'],2)
        self.assertEqual(aggregate['baseline']['windows'],1)

    def test_variants_do_not_mix(self):
        windows,aggregate=summarize(rows()+rows(2,2))
        self.assertEqual([w['variant'] for w in windows],['baseline','region'])
        self.assertEqual(set(aggregate),{'baseline','region'})

    def test_disarmed_and_short_windows_rejected(self):
        self.assertEqual(summarize(rows(0,0)+rows(count=10)),([],{}))


if __name__=='__main__':unittest.main()
