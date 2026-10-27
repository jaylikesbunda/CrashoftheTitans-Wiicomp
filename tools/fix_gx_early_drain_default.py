"""Overlap the guest with GX processing by default (NWII_NO_GX_EARLY_DRAIN=1 restores opt-in).

Capture frame-cost-20261006-102126-770700 (11.80 FPS, wall 84.7 ms): the guest thread
ran 48.8 ms of CPU and then waited 34.1 ms in guest.idle_wait. 3,900 of those 4,094 ms
overlapped a gx.drain on the host thread: the guest produced a whole frame of GX bytes,
reached its sync point, idled while one 27-29 ms drain parsed/decoded/rendered everything
queued, and only then continued. Frame time was the sum of both lanes. Early drain
starts that work once 16 KiB is queued so it overlaps the guest's next stretch.

History: it was made opt-in after a first run froze in a stuck-scheduler state. The same
stall reproduced with early drain off (HANDOFF_2026-10-05) and the lost PE-finish race was
repaired afterward (PE_INTERRUPT_TRANSACTION.md), so the old attribution is unproven.
Publication points, locks and pending-byte accounting are unchanged.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp';s=f.read_text()
# fix_sched_recorder.py restores the opt-in expression on every staging pass (its search
# string is a substring of our line), so test the active expression, never a marker comment.
old='static const bool early = std::getenv("NWII_GX_EARLY_DRAIN") != nullptr;'
new='static const bool early = std::getenv("NWII_NO_GX_EARLY_DRAIN") == nullptr;'
if old in s:
    assert s.count(old)==1
    f.write_text(s.replace(old,new))
assert 'std::getenv("NWII_NO_GX_EARLY_DRAIN") == nullptr' in f.read_text()
print('Early GX drain is default-on; NWII_NO_GX_EARLY_DRAIN=1 disables it.')
