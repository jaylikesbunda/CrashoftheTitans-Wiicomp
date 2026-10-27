"""Derive data/regions/<id>/address-map.tsv: reference (PAL) addresses -> another release's addresses.

    python tools/derive_release_map.py --reference-dol PAL/main.dol --dol OTHER/main.dol \
        --index data/regions/<id>/function-index.tsv --out data/regions/<id>/address-map.tsv

Code: a reference function maps to the release function whose instructions are equal once relocated fields are masked
(branch targets, `lis` halves and non-stack displacements); those are `code` rows and offsets inside them carry over.
Functions that changed between releases are paired in link order inside the gap between matched neighbours when their
opcode sequences are similar; those are `code-start` rows (the start maps, inner offsets do not).
Data: every absolute or small-data address that a matched reference function forms is paired with the address the
release function forms at the same instruction; runs of pairs with one displacement become `data` ranges.
"""
from pathlib import Path
import argparse, bisect, collections, difflib, struct
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument('--reference-dol', type=Path, required=True)
ap.add_argument('--reference-index', type=Path, default=Path(__file__).resolve().parents[1] / 'data/function-index.tsv')
ap.add_argument('--dol', type=Path, required=True)
ap.add_argument('--index', type=Path, required=True)
ap.add_argument('--out', type=Path, required=True)
ap.add_argument('--write-index', action='store_true', help='add matched reference functions that the release index lacks to --index')
args = ap.parse_args()

DFORM = [14, 24, 25, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57]


class Dol:
    def __init__(self, path):
        d = path.read_bytes()
        off, addr, size = (struct.unpack('>18I', d[i:i + 72]) for i in (0, 72, 144))
        self.text = []
        self.secs = [(i < 7, off[i], addr[i], size[i]) for i in range(18) if size[i]]
        for is_text, o, a, z in self.secs:
            if not is_text:
                continue
            w = np.frombuffer(d[o:o + z], dtype='>u4').astype(np.uint32)
            op, ra = w >> 26, (w >> 16) & 31
            n = w.copy()
            n[op == 18] &= 0xfc000003
            n[op == 15] &= 0xffff0000
            n[np.isin(op, DFORM + [60, 61]) & (ra != 1)] &= 0xffff0000
            coarse = np.where(op == 31, w & 0xfc0007fe, np.where(np.isin(op, [4, 19, 59, 63]), w & 0xfc00003e, w & 0xfc000000))
            self.text.append((a, a + z, w, n, coarse))
        self.sda = self._sda(d)

    def seg(self, a):
        return next((s for s in self.text if s[0] <= a < s[1]), None)

    def words(self, a, size, kind=2):
        s = self.seg(a)
        return s[kind][(a - s[0]) // 4:(a - s[0] + size) // 4]

    def _sda(self, d):
        out = {}
        for s in self.text:
            w = s[2]
            for i in range(len(w) - 1):
                a, b = int(w[i]), int(w[i + 1])
                for r in (2, 13):
                    if r not in out and a >> 26 == 15 and (a >> 21) & 31 == r and (a >> 16) & 31 == 0 \
                            and b >> 26 == 24 and (b >> 21) & 31 == r and (b >> 16) & 31 == r:
                        out[r] = (a & 0xffff) << 16 | (b & 0xffff)
        return out


def index(path):
    rows = [l.split('\t') for l in path.read_text().splitlines()[1:]]
    return [(int(r[0], 16), int(r[1], 0)) for r in rows]


ref, own = Dol(args.reference_dol), Dol(args.dol)
ref_funcs, own_funcs = index(args.reference_index), index(args.index)
own_size = dict(own_funcs)
own_starts = [a for a, _ in own_funcs]


# ---- 1. exact matches anywhere in the code, following the running displacement
def search(rf, rs, lo, hi, guess):
    body = ref.words(rf, rs, 3)
    hits = []
    for seg in own.text:
        a, b = max(lo, seg[0]), min(hi, seg[1] - rs)
        if a > b:
            continue
        i0, i1 = (a - seg[0]) // 4, (b - seg[0]) // 4 + 1
        if seg[0] <= guess <= seg[1] - rs and np.array_equal(seg[3][(guess - seg[0]) // 4:(guess - seg[0] + rs) // 4], body):
            return [guess]
        for k in np.nonzero(seg[3][i0:i1] == body[0])[0] + i0:
            if np.array_equal(seg[3][k:k + len(body)], body):
                hits.append(seg[0] + int(k) * 4)
    return hits


code = {}
delta = 0
for rf, rs in ref_funcs:
    hits = search(rf, rs, rf + delta - 0x10000, rf + delta + 0x10000, rf + delta)
    if hits:
        code[rf] = min(hits, key=lambda c: abs(c - (rf + delta)))
        delta = code[rf] - rf

# ---- 2. keep the longest increasing run (link order is shared), so a duplicate body cannot pair out of order
keys = [rf for rf, _ in ref_funcs if rf in code]
tails, tail_at, prev = [], [], [-1] * len(keys)
for i, k in enumerate(keys):
    j = bisect.bisect_left(tails, code[k])
    if j == len(tails):
        tails.append(code[k]); tail_at.append(i)
    else:
        tails[j] = code[k]; tail_at[j] = i
    prev[i] = tail_at[j - 1] if j else -1
keep, i = set(), tail_at[-1]
while i >= 0:
    keep.add(keys[i]); i = prev[i]
code = {k: v for k, v in code.items() if k in keep}

# A match must be a release function of the same extent. One in a gap of the release index (code the release's
# analysis did not mark as a function) is added to the index; one that straddles release functions is dropped.
ref_size = dict(ref_funcs)
added = []
for rf, of in sorted(code.items()):
    if own_size.get(of) == ref_size[rf]:
        continue
    i = bisect.bisect_right(own_starts, of + ref_size[rf] - 1) - 1
    if of not in own_size and (i < 0 or own_starts[i] + own_size[own_starts[i]] <= of):
        added.append((of, ref_size[rf]))
    else:
        del code[rf]
if added:
    for of, size in added:
        own_size[of] = size
    own_starts = sorted(own_size)
    if args.write_index:
        rows = ['address\tsize\tname\tfile'] + ['%08x\t0x%x\tFUN_%08x\t%08x_FUN_%08x' % (a, own_size[a], a, a, a) for a in own_starts]
        args.index.write_text('\n'.join(rows) + '\n')
claimed = set(code.values())

# ---- 3. changed functions: pair inside each gap between matched neighbours, in order, by opcode similarity
starts = {}
order = [rf for rf, _ in ref_funcs]
ref_size = dict(ref_funcs)
i = 0
while i < len(order):
    if order[i] in code:
        i += 1; continue
    j = i
    while j < len(order) and order[j] not in code:
        j += 1
    lo = code[order[i - 1]] if i else 0
    hi = code[order[j]] if j < len(order) else 0xffffffff
    cands = [c for c in own_starts[bisect.bisect_right(own_starts, lo):bisect.bisect_left(own_starts, hi)] if c not in claimed]
    k = 0
    for rf in order[i:j]:
        a = ref.words(rf, ref_size[rf], 4).tolist()
        best = None
        for n, c in enumerate(cands[k:k + 4]):
            b = own.words(c, own_size[c], 4).tolist()
            r = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
            if r >= 0.6 and (best is None or r > best[0]):
                best = (r, k + n, c)
        if best:
            starts[rf] = best[2]; k = best[1] + 1
    i = j

# ---- 4. data and code targets formed by matched functions
pairs = collections.defaultdict(collections.Counter)


def sx(v):
    return v - 0x10000 if v & 0x8000 else v


for rf, of in code.items():
    hr, ho = {}, {}
    rw, ow = ref.words(rf, ref_size[rf]), own.words(of, ref_size[rf])
    for n in range(len(rw)):
        a, b = int(rw[n]), int(ow[n])
        op, rd, ra = a >> 26, (a >> 21) & 31, (a >> 16) & 31
        if op == 15 and ra == 0:
            hr[rd], ho[rd] = (a & 0xffff) << 16, (b & 0xffff) << 16
            continue
        if op == 24:                                  # ori rA,rS,lo: the source is rS, the result rA
            if rd in hr:
                pairs[hr[rd] | (a & 0xffff)][ho[rd] | (b & 0xffff)] += 1
            hr.pop(ra, None); ho.pop(ra, None)
            continue
        if op in DFORM and op != 25:
            if ra in (2, 13) and ra in ref.sda and ra in own.sda:
                pairs[(ref.sda[ra] + sx(a & 0xffff)) & 0xffffffff][(own.sda[ra] + sx(b & 0xffff)) & 0xffffffff] += 1
            elif ra in hr:
                pairs[(hr[ra] + sx(a & 0xffff)) & 0xffffffff][(ho[ra] + sx(b & 0xffff)) & 0xffffffff] += 1
        if op in (25, 26, 27, 28, 29, 31):
            hr.pop(ra, None); ho.pop(ra, None)       # logical and X-form results land in rA (X-form: approximate)
        if op not in (16, 18, 19, 36, 37, 38, 39, 44, 45, 47, 52, 53, 54, 55):
            hr.pop(rd, None); ho.pop(rd, None)       # rd was written (stores and branches write none)
data = sorted((r, c.most_common(1)[0][0]) for r, c in pairs.items() if not (0x80004000 <= r < 0x80480000))
ranges = []
for r, o in data:
    if ranges and o - r == ranges[-1][2] and r - ranges[-1][1] <= 0x400:
        ranges[-1][1] = r
    else:
        ranges.append([r, r, o - r])

rows = ['kind\treference\tsize\trelease']
merged = sorted([(rf, 'code', ref_size[rf], of) for rf, of in code.items()] +
                [(rf, 'code-start', ref_size[rf], of) for rf, of in starts.items()])
rows += ['%s\t%08x\t0x%x\t%08x' % (kind, rf, size, of) for rf, kind, size, of in merged]
for n, (lo, last, d) in enumerate(ranges):
    nxt = ranges[n + 1][0] if n + 1 < len(ranges) else last + 4
    size = min(nxt, last + 0x40) - lo        # a little past the last pair: the rest of that object
    rows.append('data\t%08x\t0x%x\t%08x' % (lo, size, (lo + d) & 0xffffffff))
args.out.parent.mkdir(parents=True, exist_ok=True)
args.out.write_text('\n'.join(rows) + '\n')
print('reference functions %d: %d exact, %d changed (start only), %d without a counterpart; %d data ranges from %d pairs; %d functions %s the release index'
      % (len(ref_funcs), len(code), len(starts), len(ref_funcs) - len(code) - len(starts), len(ranges), len(data), len(added), 'added to' if args.write_index else 'missing from'))
