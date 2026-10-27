"""Continuation entry selection that includes poll-site resume PCs (prototype, opt-in).

Why: a poll that delivers an interrupt publishes `ctx.pc = <poll pc>` and returns. When the guest handler
returns (rfi), the dispatcher re-enters the function at that poll pc. guest_entry_specialize only gives
call-return (LR literal) entries an optimized continuation, and only for the functions in
reference/guest-regions/hot-continuations.json, so every other interior entry runs the cold, all-entry
native_guest_resume_* body until the function returns. In the 2026-10-19 PMC capture 3,010 of the 4,911
resume/continuation samples lie inside loops closed by a poll site.

This module only chooses entry PCs. The promotion, the per-entry live-in materialisers and the exact
fallback are guest_entry_specialize's existing continuation machinery: a selected PC enters
native_guest_continuation_<addr>, everything else still enters the cold resume body.

  poll_entries(source)   PCs published immediately before a `native_poll_due()` site
  return_entries(source) PCs written to LR (call-return continuations; the existing rule)
  select(source, cases)  ordered, bounded selection over the function's own interior entries

Environment (all default off / unchanged):
  NWII_BUILD_POLL_CONTINUATIONS=1   use select() instead of the LR-only rule
  NWII_POLL_CONTINUATION_LIMIT=N    entry bound (default 128; the LR-only rule used 64)
  NWII_CONTINUATION_TARGETS=path    JSON {"functions":[{"name":"func_XXXXXXXX"},...]} replacing the allowlist
"""
import hashlib
import json
import os
import re
from pathlib import Path

POLL_SITE = re.compile(r'ctx\.pc = (0x[0-9A-F]{8});\s*\n\s*if \(nwii::runtime::native_poll_due\(\)\)')
LR_SITE = re.compile(r'ctx\.lr = (0x[0-9A-F]{8});')


def poll_entries(source):
    return list(dict.fromkeys(POLL_SITE.findall(source)))


def return_entries(source):
    return list(dict.fromkeys(LR_SITE.findall(source)))


def enabled():
    return os.environ.get('NWII_BUILD_POLL_CONTINUATIONS', '1') != '0'


def limit():
    return int(os.environ.get('NWII_POLL_CONTINUATION_LIMIT', '128'))


def select(source, cases):
    """Return (entries, reason). `cases` is promote_regs.parse(source)[3]: [(addr_text, label), ...].

    Only PCs that are already interior entries of the function are eligible, so the set of PCs the
    function accepts never changes; only which body serves them. Poll PCs come first (they carry the
    loop re-entries), then call returns. Over the bound the selection is refused as a whole, matching
    the existing all-or-nothing rule, rather than silently keeping an arbitrary subset."""
    known = {addr for addr, _ in cases}
    wanted = [a for a in poll_entries(source) + return_entries(source) if a in known]
    wanted = list(dict.fromkeys(wanted))
    if len(wanted) > limit():
        return [], 'over_limit'
    return wanted, 'ok'


DEFAULT_TARGETS = Path(__file__).resolve().parents[1] / 'reference/guest-regions/poll-continuation-targets.json'


def targets_override():
    """Functions that get continuation bodies when this selection is enabled: NWII_CONTINUATION_TARGETS, else
    the measured list in reference/guest-regions/poll-continuation-targets.json (pinned to the DOL hash)."""
    if not enabled():
        return None
    path = os.environ.get('NWII_CONTINUATION_TARGETS')
    if path:
        return {r['name'] for r in json.loads(Path(path).read_text())['functions']}
    manifest = json.loads(DEFAULT_TARGETS.read_text())
    dol = DEFAULT_TARGETS.parents[2].parent / 'orig/main.dol'
    if not dol.is_file() or hashlib.sha256(dol.read_bytes()).hexdigest() != manifest['dol_sha256']:
        return None   # another disc image: keep the existing allowlist rule
    return {r['name'] for r in manifest['functions']}


def targets_from_samples(rows, excluded, minimum=20):
    """Functions whose resume/continuation bodies hold at least `minimum` timer samples.

    `rows` is a capture's guest-functions-all.json list; `excluded` the effect-exclusion addresses,
    applied as a set filter only."""
    totals = {}
    for row in rows:
        m = re.match(r'native_guest_(?:resume|continuation)_([0-9A-F]{8})\b', row['name'])
        if m and m[1] not in excluded:
            totals[m[1]] = totals.get(m[1], 0) + row['samples']
    picked = sorted((a for a, s in totals.items() if s >= minimum), key=lambda a: -totals[a])
    return [{'name': 'func_' + a, 'samples': totals[a]} for a in picked]
