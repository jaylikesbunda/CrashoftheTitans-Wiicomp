"""Honour the SDK's PI-FIFO gather-pipe redirect (GXRedirectWriteGatherPipe) when PI base is 0.

The Revolution SDK redirects the write-gather pipe to arbitrary RAM by clearing CP_CR link-enable and programming the
PI CPU FIFO as base=0, end=0x04000000, write pointer=destination (see 803C90B4). wgp_redirect() treated base==0 as
"no redirect" and only mapped MEM1, so Radical's CPU-skinning output (positions/normals written into vertex arrays)
was parsed as GX commands: the parser lost sync, the draw-done BP stayed unparsed, GXDrawDone never returned and the
level load froze with the frame counter stopped.

Redirect when (base == 0 && end >= 0x04000000) or the pre-existing rule (base != 0 and != CP base). The destination
is mapped physical -> MEM1 (0x80000000) or MEM2 (0x90000000)."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
gx = PORT / 'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp'
s = gx.read_bytes().decode('utf-8')
nl = '\r\n' if '\r\n' in s else '\n'
t = s.replace('\r\n', '\n')
if 'NATIVE_PI_BASE0_REDIRECT' in t:
    print('already staged')
else:
    a = t.index('static bool wgp_redirect(uint8_t b) {')
    b = t.index('\n}\n', a) + 3
    body = t[a:b]
    old_zero = '    if (base == 0) return false; '
    old_zero2 = '    if (base == 0) return false;'
    if old_zero in body:
        body = body.replace(old_zero, '    if (base == 0 && end < 0x04000000u) return false; // NATIVE_PI_BASE0_REDIRECT', 1)
    elif old_zero2 in body:
        body = body.replace(old_zero2, '    if (base == 0 && end < 0x04000000u) return false; // NATIVE_PI_BASE0_REDIRECT', 1)
    else:
        raise SystemExit('base==0 guard not found in wgp_redirect')
    old_cmp = '    if ((base & 0x03FFFFFF) == (cp_base & 0x03FFFFFF))\n        return false; '
    old_cmp2 = '    if ((base & 0x03FFFFFF) == (cp_base & 0x03FFFFFF))\n        return false;'
    new_cmp = '    if (base != 0 && (base & 0x03FFFFFF) == (cp_base & 0x03FFFFFF))\n        return false;'
    if old_cmp in body:
        body = body.replace(old_cmp, new_cmp, 1)
    elif old_cmp2 in body:
        body = body.replace(old_cmp2, new_cmp, 1)
    else:
        raise SystemExit('cp_base comparison not found in wgp_redirect')
    old_wr = '    nwii::runtime::g_mmu->write8(0x80000000u | (wptr & 0x01FFFFFFu), b);'
    new_wr = '''    {
        const uint32_t phys = wptr & 0x1FFFFFFFu;
        uint32_t virt = 0;
        if (phys < 0x01800000u) virt = 0x80000000u | phys;
        else if (phys >= 0x10000000u && phys < 0x14000000u) virt = 0x90000000u | (phys & 0x03FFFFFFu);
        if (virt) nwii::runtime::g_mmu->write8(virt, b);
    }'''
    if old_wr not in body:
        raise SystemExit('RAM write not found in wgp_redirect')
    body = body.replace(old_wr, new_wr, 1)
    old_wrap = '    if ((wptr & 0x03FFFFFF) > (end & 0x03FFFFFF))' + chr(10) + '        wptr = base;'
    new_wrap = '    if (!(base == 0 && end >= 0x04000000u) && (wptr & 0x03FFFFFF) > (end & 0x03FFFFFF))' + chr(10) + '        wptr = base;'
    if old_wrap not in body:
        raise SystemExit('wrap check not found in wgp_redirect')
    body = body.replace(old_wrap, new_wrap, 1)
    t = t[:a] + body + t[b:]
    gx.write_bytes(t.replace('\n', nl).encode('utf-8'))
    print('PI base-0 redirect staged')
