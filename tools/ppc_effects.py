"""Gekko (PPC 750CL + paired singles) register-effect decoder.

effects(pc, word) -> Effects(writes, reads, kind, target) where writes/reads are sets of resource names:
  'r0'..'r31', 'f0'..'f31' (FPR incl. ps1), 'cr0'..'cr7', 'lr', 'ctr', 'xer', 'ca', 'msr', 'fpscr', 'gqr', 'mem'.
kind: 'op' | 'call' | 'branch' | 'cbranch' | 'return' | 'indirect_call' | 'indirect_branch' | 'trap' | 'sys' | 'unknown'.
Only effects are modelled, not values. Unknown encodings are reported as kind 'unknown' with conservative effects.
"""
from dataclasses import dataclass, field

ALL_GPR = {f'r{i}' for i in range(32)}
ALL_FPR = {f'f{i}' for i in range(32)}
ALL_CR = {f'cr{i}' for i in range(8)}
EVERYTHING = ALL_GPR | ALL_FPR | ALL_CR | {'lr', 'ctr', 'xer', 'ca', 'msr', 'fpscr', 'gqr'}


@dataclass
class Effects:
    writes: set = field(default_factory=set)
    reads: set = field(default_factory=set)
    kind: str = 'op'
    target: int = None
    mem_read: bool = False
    mem_write: bool = False


def bits(w, a, b):  # PowerPC bit numbering, inclusive
    return (w >> (31 - b)) & ((1 << (b - a + 1)) - 1)


def sext(v, n):
    return v - (1 << n) if v & (1 << (n - 1)) else v


def r(n): return f'r{n}'
def f(n): return f'f{n}'


SPR = {1: 'xer', 8: 'lr', 9: 'ctr'}


def effects(pc, w):
    op = bits(w, 0, 5)
    d, a, b = bits(w, 6, 10), bits(w, 11, 15), bits(w, 16, 20)
    rc = w & 1
    e = Effects()
    W, R = e.writes, e.reads
    ra0 = (lambda x: set() if x == 0 else {r(x)})  # (rA|0)

    if op in (7, 8, 12, 13, 14, 15):  # mulli subfic addic addic. addi addis
        W.add(r(d)); R.update(ra0(a) if op in (14, 15) else {r(a)})
        if op in (8, 12, 13): W.add('ca')
        if op == 13: W.add('cr0'); R.add('xer')
    elif op in (10, 11):  # cmpli cmpi
        W.add(f'cr{d >> 2}'); R.update({r(a), 'xer'})
    elif op in (20, 21, 23):  # rlwimi rlwinm rlwnm
        W.add(r(a)); R.add(r(d))
        if op == 20: R.add(r(a))
        if op == 23: R.add(r(b))
        if rc: W.add('cr0'); R.add('xer')
    elif op in (24, 25, 26, 27, 28, 29):  # ori oris xori xoris andi. andis.
        W.add(r(a)); R.add(r(d))
        if op in (28, 29): W.add('cr0'); R.add('xer')
    elif op == 16:  # bc
        bo = d
        if not (bo & 0x04): W.add('ctr'); R.add('ctr')
        if not (bo & 0x10): R.add(f'cr{a >> 2}')
        if w & 1: W.add('lr')
        tgt = sext(bits(w, 16, 29) << 2, 16)
        e.target = (tgt if w & 2 else pc + tgt) & 0xffffffff
        e.kind = 'call' if w & 1 else 'cbranch'
    elif op == 18:  # b
        tgt = sext(bits(w, 6, 29) << 2, 26)
        e.target = (tgt if w & 2 else pc + tgt) & 0xffffffff
        if w & 1: W.add('lr'); e.kind = 'call'
        else: e.kind = 'branch'
    elif op == 17:
        e.kind = 'sys'; W.update(EVERYTHING)
    elif op == 3:
        e.kind = 'trap'
    elif op == 19:
        xo = bits(w, 21, 30)
        if xo == 16:  # bclr
            bo = d
            if not (bo & 0x04): W.add('ctr'); R.add('ctr')
            R.add('lr')
            if w & 1: W.add('lr'); e.kind = 'indirect_call'
            else: e.kind = 'return' if bo & 0x14 == 0x14 else 'cbranch'
        elif xo == 528:  # bcctr
            R.add('ctr')
            if w & 1: W.add('lr'); e.kind = 'indirect_call'
            else: e.kind = 'indirect_branch'
        elif xo == 0:  # mcrf
            W.add(f'cr{d >> 2}'); R.add(f'cr{a >> 2}')
        elif xo in (33, 129, 193, 225, 257, 289, 417, 449):  # cr logical
            W.add(f'cr{d >> 2}'); R.update({f'cr{a >> 2}', f'cr{b >> 2}'})
        elif xo == 50:  # rfi
            e.kind = 'sys'; W.update({'msr'})
        elif xo == 150:  # isync
            pass
        else:
            e.kind = 'unknown'; W.update(EVERYTHING)
    elif op in (32, 33, 34, 35, 40, 41, 42, 43):  # integer loads
        W.add(r(d)); R.update(ra0(a)); e.mem_read = True
        if op & 1: W.add(r(a))
    elif op in (36, 37, 38, 39, 44, 45):  # integer stores
        R.update({r(d)} | ra0(a)); e.mem_write = True
        if op & 1: W.add(r(a))
    elif op == 46:  # lmw
        W.update(r(i) for i in range(d, 32)); R.update(ra0(a)); e.mem_read = True
    elif op == 47:  # stmw
        R.update({r(i) for i in range(d, 32)} | ra0(a)); e.mem_write = True
    elif op in (48, 49, 50, 51):  # lfs lfsu lfd lfdu
        W.add(f(d)); R.update(ra0(a)); e.mem_read = True
        if op & 1: W.add(r(a))
    elif op in (52, 53, 54, 55):  # stfs stfsu stfd stfdu
        R.update({f(d)} | ra0(a)); e.mem_write = True
        if op & 1: W.add(r(a))
    elif op in (56, 57):  # psq_l psq_lu
        W.add(f(d)); R.update(ra0(a) | {'gqr'}); e.mem_read = True
        if op == 57: W.add(r(a))
    elif op in (60, 61):  # psq_st psq_stu
        R.update({f(d)} | ra0(a) | {'gqr'}); e.mem_write = True
        if op == 61: W.add(r(a))
    elif op in (59, 63):  # FP
        xo5 = bits(w, 26, 30); xo10 = bits(w, 21, 30)
        if op == 63 and xo10 in (0, 32):  # fcmpu fcmpo
            W.update({f'cr{d >> 2}', 'fpscr'}); R.update({f(a), f(b)})
        elif op == 63 and xo10 == 583:  # mffs
            W.add(f(d)); R.add('fpscr')
        elif op == 63 and xo10 in (38, 70, 134, 711, 64):  # mtfsb1 mtfsb0 mtfsfi mtfsf mcrfs
            W.add('fpscr')
            if xo10 == 64: W.add(f'cr{d >> 2}')
            if xo10 == 711: R.add(f(b))
        else:
            W.update({f(d), 'fpscr'}); R.update({f(a), f(b), f(bits(w, 21, 25))})
        if rc: W.add('cr1')
    elif op == 4:  # paired singles
        xo10 = bits(w, 21, 30); xo6 = bits(w, 25, 30)
        if xo10 in (0, 32, 64, 96):  # ps_cmpu0 ps_cmpo0 ps_cmpu1 ps_cmpo1
            W.update({f'cr{d >> 2}', 'fpscr'}); R.update({f(a), f(b)})
        elif xo6 in (6, 7, 38, 39):  # psq_lx psq_stx psq_lux psq_stux
            if xo6 in (6, 38): W.add(f(d)); e.mem_read = True
            else: R.add(f(d)); e.mem_write = True
            R.update(ra0(a) | {r(b), 'gqr'})
            if xo6 in (38, 39): W.add(r(a))
        elif xo10 == 1014:  # dcbz_l
            R.update(ra0(a) | {r(b)}); e.mem_write = True
        else:
            W.update({f(d), 'fpscr'}); R.update({f(a), f(b), f(bits(w, 21, 25))})
            if rc: W.add('cr1')
    elif op == 31:
        xo = bits(w, 21, 30); xo9 = bits(w, 22, 30); oe = bits(w, 21, 21)
        if xo in (0, 32):  # cmp cmpl
            W.add(f'cr{d >> 2}'); R.update({r(a), r(b), 'xer'})
        elif xo9 in (266, 10, 138, 202, 234, 40, 8, 136, 200, 232, 104, 235, 75, 11, 491, 459):  # XO arithmetic
            W.add(r(d)); R.update({r(a), r(b)})
            if xo9 in (10, 138, 202, 234, 8, 136, 200, 232): W.add('ca')
            if xo9 in (138, 202, 234, 136, 200, 232): R.add('ca')
            if oe: W.add('xer')
            if rc: W.add('cr0'); R.add('xer')
        elif xo in (28, 60, 444, 412, 124, 476, 316, 284, 24, 536, 792, 824, 26, 954, 922):  # logical/shift/ext
            W.add(r(a)); R.update({r(d), r(b)})
            if xo in (792, 824): W.add('ca')
            if rc: W.add('cr0'); R.add('xer')
        elif xo in (23, 55, 87, 119, 279, 311, 343, 375, 534, 790, 20):  # indexed loads (+ lwarx)
            W.add(r(d)); R.update(ra0(a) | {r(b)}); e.mem_read = True
            if xo in (55, 119, 311, 375): W.add(r(a))
        elif xo in (151, 183, 215, 247, 407, 439, 662, 918, 150):  # indexed stores (+ stwcx.)
            R.update({r(d)} | ra0(a) | {r(b)}); e.mem_write = True
            if xo in (183, 247, 439): W.add(r(a))
            if xo == 150: W.add('cr0')
        elif xo in (535, 567, 599, 631):  # lfsx lfsux lfdx lfdux
            W.add(f(d)); R.update(ra0(a) | {r(b)}); e.mem_read = True
            if xo in (567, 631): W.add(r(a))
        elif xo in (663, 695, 727, 759, 983):  # stfsx stfsux stfdx stfdux stfiwx
            R.update({f(d)} | ra0(a) | {r(b)}); e.mem_write = True
            if xo in (695, 759): W.add(r(a))
        elif xo == 597 or xo == 533:  # lswi lswx
            W.update(ALL_GPR); e.mem_read = True
        elif xo == 725 or xo == 661:  # stswi stswx
            R.update(ALL_GPR); e.mem_write = True
        elif xo == 339 or xo == 371:  # mfspr mftb
            spr = bits(w, 16, 20) << 5 | bits(w, 11, 15)
            W.add(r(d)); R.add(SPR.get(spr, 'spr' if xo == 339 else 'tb'))
        elif xo == 467:  # mtspr
            spr = bits(w, 16, 20) << 5 | bits(w, 11, 15)
            R.add(r(d))
            name = SPR.get(spr)
            if name: W.add(name)
            elif 912 <= spr <= 919: W.add('gqr')
            else: W.add('spr')
        elif xo == 19:  # mfcr
            W.add(r(d)); R.update(ALL_CR)
        elif xo == 144:  # mtcrf
            crm = bits(w, 12, 19)
            W.update(f'cr{i}' for i in range(8) if crm & (0x80 >> i)); R.add(r(d))
        elif xo == 83:  # mfmsr
            W.add(r(d)); R.add('msr')
        elif xo == 146:  # mtmsr
            W.add('msr'); R.add(r(d))
        elif xo == 512:  # mcrxr
            W.update({f'cr{d >> 2}', 'xer', 'ca'}); R.add('xer')
        elif xo in (86, 54, 278, 246, 1014, 982, 470, 598, 854, 306, 566):  # cache/sync/eieio/tlb ops
            R.update(ra0(a) | {r(b)})
            if xo == 1014: e.mem_write = True
        elif xo in (595, 659, 210, 242):  # mfsr mfsrin mtsr mtsrin
            if xo in (595, 659): W.add(r(d))
        elif xo == 4:  # tw
            e.kind = 'trap'
        else:
            e.kind = 'unknown'; W.update(EVERYTHING)
    else:
        e.kind = 'unknown'; W.update(EVERYTHING)
    return e
