"""Overlap the guest CPU with GX processing instead of alternating.

Live thread sampling of an in-level session (about 11 fps) showed the guest
thread and the GX/main thread each roughly half busy, and ~16 GPU finishes per
displayed frame. GX bytes were only drained when the guest went idle waiting for
a finish (or at field boundaries), so every one of those 16 waits first made the
GPU thread do all the queued work while the guest sat idle, then the guest
computed again while the GPU sat idle: frame time was the sum of both.

Now, once 16 KiB of GX data is queued the guest asks the GX thread to start
draining right away, so rendering overlaps the guest's next stretch of work and a
later finish wait only covers the remainder. Parsing nearer the time of issue
also reads guest vertex/texture memory closer to what the game wrote. Now opt-in
(NWII_GX_EARLY_DRAIN=1): the first run with it default-on froze in a stuck-scheduler state.

Also: skip the WPAD state lookup on the 31 of 32 polls that return immediately.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
src = port / 'build/nwii-full-source/nWiiRuntime/src/hle'
marker = 'NATIVE_GX_EARLY_DRAIN_V1'

p = src / 'gx.cpp'
t = p.read_text()
if marker not in t:
    old = '        g_gx_pending_bytes.fetch_add(g_cpu_fifo.size(), std::memory_order_relaxed);\n'
    assert t.count(old) == 1
    new = old + '''        { // NATIVE_GX_EARLY_DRAIN_V1
            static const bool early = std::getenv("NWII_GX_EARLY_DRAIN") != nullptr;
            if (early && g_gx_pending_bytes.load(std::memory_order_relaxed) >= 16384 &&
                !g_gx_drain_request.exchange(true, std::memory_order_acq_rel))
                g_gx_drain_cv.notify_one();
        }
'''
    t = t.replace(old, new)
    p.write_text(t)

p = src / 'ios.cpp'
t = p.read_text()
if 'NATIVE_WPAD_POLL_THROTTLE_V2' not in t:
    # The V1 line may already carry the NWII_NO_POLL_THROTTLE switch (the poll-clock stage adds it), so match by shape.
    import re
    pat = re.compile(r'(  auto& s=native_wpad_state\(\);\n  if \(!s\.enabled \|\| ctx\.in_callback \|\| !\(ctx\.msr & 0x8000\)\) return;\n)(  \{[^\n]*NATIVE_WPAD_POLL_THROTTLE_V1\n)')
    assert len(pat.findall(t)) == 1
    t = pat.sub(lambda m: m.group(2).replace('POLL_THROTTLE_V1', 'POLL_THROTTLE_V2') + m.group(1), t, count=1)
    p.write_text(t)
print('GX early drain installed; WPAD poll throttle reordered.')
