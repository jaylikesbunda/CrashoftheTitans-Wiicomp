"""Barrier census over the staged guest translation units (measurement tool, not a build stage).

A *barrier line* is a line the promoter may not analyse: it passes `ctx` to something else
(promote_regs' own rule is `\\bctx\\b(?!\\.)` or a call to a non-whitelisted `ctx.<method>`).
At such a line the promoter writes the dirty promoted registers back into `ctx` before the
line and reads the live ones back afterwards, because the callee (or the interpreter, or the
exception path) may observe or change them, and anything may unwind.

This tool counts that population, splits it into the shapes the code generator emits, and
prices each shape with the register transfers the promoter actually emitted around it:

  poll          `if (nwii::runtime::native_poll_due()) { ... }` - one per backward branch
  spin          the `ctx.inst_count % 10000000` "Spinning at PC" print inside a poll
  blockcall     `if(native_blocks_on){native_block_XXX_YYY(ctx);goto loc_ZZZ;}` (NATIVE_BOUNDED_BLOCKS_V1)
  call          `func_XXXXXXXX(ctx);` (direct guest call, optionally preceded by `ctx.pc = ...`)
  indirect      `native_call_indirect(ctx)`
  proc_cb       `process_pending_callbacks(ctx)` outside a poll line
  interpret     `interpret_step(ctx)` (the dispatcher fallback)
  ctx_barrier   `NWII_CTX_BARRIER()` (compiler memory barrier around a write-gather store)
  require_fpu   `ctx.require_fpu(0x...)` (MSR.FP fault guard; returns, no spill)
  ic_sync       a line that reads inst_count through a ctx method (read_timebase/read_dec/...)
  other         any remaining line that passes ctx

Cost model: for every barrier line the tool counts the register transfers written next to it
(spill `ctx.gpr[N] = gN;` / reload `gN = ctx.gpr[N];`), both inside a one-line barrier (poll,
blockcall) and on the immediately preceding/following lines (call, other).  Guest instruction
counts come from the per-instruction `// 0xADDR: WORD` comments the generator emits.

Usage:  python tools/barrier_census.py [--root ...] [--units PATTERNS] [--top N] [--quiet]
Writes reports/barrier-census.json and prints the summary.  Read-only: never edits a unit.
"""
from pathlib import Path
import argparse
import json
import re
import sys

PORT = Path(__file__).resolve().parents[1]
NL = '\n'
FUNC_HEAD = re.compile(r'^(?:static\s+)?(?:__attribute__\(\([^)]*\)\)\s*)?(?:void)\s+'
                       r'(func_[0-9A-F]{8}|native_[A-Za-z0-9_]+)\(\s*(?:NWII_CTX_PARAM|CPUContext&)\s+ctx\s*\)\s*\{$')
GUEST_INSN = re.compile(r'^\s*// 0x[0-9A-F]{8}: [0-9A-F]{8}\s*$')
LOCAL = r'(?:g\d+|ctrv|ic|xer|f\d+|p\d+|c\d(?:lt|gt|eq|so))'
CTXREG = r'(?:gpr\[\d+\]|ctr|inst_count|xer|fpr\[\d+\]|ps1\[\d+\]|cr\[\d\]\.(?:lt|gt|eq|so))'
SPILL = re.compile(r'\bctx\.' + CTXREG + r' = ' + LOCAL + r';')
RELOAD = re.compile(r'\b' + LOCAL + r' = ctx\.' + CTXREG + r';')

CLASSES = (
    ('poll', re.compile(r'if \(nwii::runtime::native_poll_due\(\)\) \{')),
    ('spin', re.compile(r'ctx\.inst_count % 10000000\) == 0')),
    ('blockcall', re.compile(r'if\(native_blocks_on(?: && \(ctx\.msr&0x2000\))?\)\{.*?native_block_')),
    ('call', re.compile(r'(?<![\w])func_[0-9A-F]{8}\(ctx\);')),
    ('indirect', re.compile(r'native_call_indirect\(ctx\)')),
    ('proc_cb', re.compile(r'process_pending_callbacks\(ctx\)')),
    ('interpret', re.compile(r'interpret_step\(ctx\)')),
    ('ctx_barrier', re.compile(r'NWII_CTX_BARRIER\(\)')),
    ('require_fpu', re.compile(r'ctx\.require_fpu\(')),
    ('ic_sync', re.compile(r'ctx\.(?:read_timebase|read_dec|write_dec|dec_expired)\s*\(')),
)
OTHER = re.compile(r'\bctx\b(?!\.)')
CLASS_NAMES = [name for name, _ in CLASSES] + ['other']


def code_of(line):
    """Line without its trailing // comment (quote aware)."""
    if '//' not in line:
        return line
    in_str = False
    i = 0
    while i < len(line) - 1:
        c = line[i]
        if c == '"' and (i == 0 or line[i - 1] != '\\'):
            in_str = not in_str
        elif not in_str and c == '/' and line[i + 1] == '/':
            return line[:i]
        i += 1
    return line


def classify(code):
    if not code.strip():
        return None
    for name, pattern in CLASSES:
        if pattern.search(code):
            return name
    if OTHER.search(code):
        return 'other'
    return None


def functions(text):
    """Yield (name, start, end) for every ctx-taking function at column 0."""
    lines = text.split(NL)
    starts = []
    pos = 0
    for line in lines:
        m = FUNC_HEAD.match(line)
        if m:
            starts.append((m.group(1), pos))
        pos += len(line) + 1
    for index, (name, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(text)
        yield name, start, end


def census_text(text, name, file):
    """Census one function body (already extracted, promoted or not)."""
    records = []
    for name, start, end in [(name, 0, len(text))]:
        lines = text.split(NL)
        counts = {k: 0 for k in CLASS_NAMES}
        cost = {k: [0, 0] for k in CLASS_NAMES}          # class -> [spill transfers, reload transfers]
        sites = {k: 0 for k in CLASS_NAMES}
        guest = spill = reload_ = 0
        kinds = []
        for line in lines:
            if GUEST_INSN.match(line):
                guest += 1
            code = code_of(line)
            spill += len(SPILL.findall(code))
            reload_ += len(RELOAD.findall(code))
            kinds.append(classify(code))
        for index, kind in enumerate(kinds):
            if kind is None:
                continue
            counts[kind] += 1
            sites[kind] += 1
            code = code_of(lines[index])
            # transfers inside a one-line barrier (poll / blockcall / a one-line call)
            inside = (len(SPILL.findall(code)), len(RELOAD.findall(code)))
            # transfers the promoter emitted on the neighbouring lines of a multi-line barrier
            if not code.strip().endswith('}'):
                before = 0
                cursor = index - 1
                while cursor >= 0 and SPILL.search(code_of(lines[cursor])):
                    before += len(SPILL.findall(code_of(lines[cursor])))
                    cursor -= 1
                after = 0
                cursor = index + 1
                while cursor < len(kinds) and kinds[cursor] is None and RELOAD.search(code_of(lines[cursor])):
                    after += len(RELOAD.findall(code_of(lines[cursor])))
                    cursor += 1
                inside = (inside[0] + before, inside[1] + after)
            cost[kind][0] += inside[0]
            cost[kind][1] += inside[1]
        barriers = sum(counts[k] for k in ('poll', 'blockcall', 'call', 'indirect', 'proc_cb', 'interpret', 'other'))
        records.append(dict(function=name, file=file, guest_instructions=guest, barriers=barriers,
                            spill_statements=spill, reload_statements=reload_, **counts))
        records[-1]['cost'] = {k: dict(sites=sites[k], spill=cost[k][0], reload=cost[k][1])
                               for k in CLASS_NAMES if sites[k]}
    return records


def census_file(path):
    text = path.read_text(errors='surrogateescape')
    records = []
    for name, start, end in functions(text):
        records.extend(census_text(text[start:end], name, path.name))
    return records


def hot_bodies(root):
    """(name, file, body) for every hot function, ordered hottest first."""
    sys.path.insert(0, str(root / 'tools'))
    from fix_hot_split import make_nest_predicate
    hot = [l.strip() for l in (root / 'tools/hot_functions.txt').read_text().split(NL) if l.strip().startswith('func_')]
    from expand_hot_closure import expand
    hot += expand(root, hot)
    rank = {n: i for i, n in enumerate(hot)}
    try:
        nest = make_nest_predicate()
        nest_available = True
    except AssertionError:
        # The staged main_output.cpp may carry a different dispatch-override spelling than the
        # pinned predicate reads; fall back to "no indirect is nested" and record it.
        nest = lambda func, ret: False
        nest_available = False
    bodies = {}
    for path in sorted((root / 'build/crash-generated').glob('output_*.cpp')):
        if path.name == 'output_74.cpp':
            continue
        text = path.read_text(errors='surrogateescape')
        for name in re.findall(r'^void (func_[0-9A-F]{8})\((?:NWII_CTX_PARAM|CPUContext&) ctx\) \{$', text, re.M):
            if name not in rank or name in bodies:
                continue
            m = re.search(r'^void ' + name + r'\((?:NWII_CTX_PARAM|CPUContext&) ctx\) \{$', text, re.M)
            end = text.find(NL + '}' + NL, m.start())
            if end < 0:
                continue
            body = text[m.start():end + 2]
            body = re.sub(r'^void ' + name + r'\(CPUContext& ctx\) \{$',
                          'void ' + name + '(NWII_CTX_PARAM ctx) {', body, count=1, flags=re.M)
            bodies[name] = (path.name, body)
    ordered = sorted(bodies, key=lambda n: rank[n])
    return [(name, bodies[name][0], bodies[name][1]) for name in ordered], nest, nest_available


def promoted_texts(root, limit=0):
    """[(name, file, promoted_text)], promoting exactly as tools/fix_hot_split.py does."""
    sys.path.insert(0, str(root / 'tools'))
    import promote_regs as P
    ordered, nest, nest_available = hot_bodies(root)
    if limit:
        ordered = ordered[:limit]
    out = []
    stats = dict(hot=len(ordered), promoted=0, refused=0, refused_reasons={}, nest_available=nest_available)
    for name, file, body in ordered:
        promoted, info = P.promote(body, True, True, nest)
        if promoted is None:
            stats['refused'] += 1
            stats['refused_reasons'][str(info)] = stats['refused_reasons'].get(str(info), 0) + 1
            promoted = body
        else:
            stats['promoted'] += 1
        out.append((name, file, promoted))
    return out, stats


def promoted_records(root, limit=0):
    """Promote the hot set exactly as tools/fix_hot_split.py does and census the promoted text."""
    texts, stats = promoted_texts(root, limit)
    records = []
    for name, file, promoted in texts:
        records.extend(census_text(promoted, name, file))
    return records, stats


def aggregate(records):
    total = {k: 0 for k in CLASS_NAMES}
    cost = {k: [0, 0, 0] for k in CLASS_NAMES}
    for record in records:
        for name in CLASS_NAMES:
            total[name] += record[name]
        for name, entry in record['cost'].items():
            cost[name][0] += entry['sites']
            cost[name][1] += entry['spill']
            cost[name][2] += entry['reload']
    return total, cost


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, default=PORT)
    ap.add_argument('--units', default='build/crash-hot/hot_*.cpp,build/crash-split/output_*.cpp,'
                                       'build/crash-generated/output_*.cpp')
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--label', default='')
    ap.add_argument('--promote', type=int, default=0,
                    help='promote the hot set in memory (exactly as tools/fix_hot_split.py does) and '
                         'census the promoted text; 0 = census the staged text as it lies, N>0 = first N hot functions')
    args = ap.parse_args()
    root = args.root.resolve()
    patterns = [p.strip() for p in args.units.split(',') if p.strip()]
    files = []
    for pattern in patterns:
        found = sorted(root.glob(pattern))
        if found:
            files.extend(found)
            break                      # the most-derived layout that exists owns the text
    if not files:
        raise SystemExit('no staged units found for %s' % patterns)
    records = []
    stats = {}
    if args.promote:
        records, stats = promoted_records(root, args.promote if args.promote > 1 else 0)
        files = sorted((root / 'build/crash-generated').glob('output_*.cpp'))
    else:
        for path in files:
            records.extend(census_file(path))
    total, cost = aggregate(records)
    guest = sum(r['guest_instructions'] for r in records)
    barriers = sum(r['barriers'] for r in records)
    ranked = sorted(records, key=lambda r: (-r['barriers'], r['function']))
    dense = sorted((r for r in records if r['guest_instructions'] >= 64),
                   key=lambda r: (-(r['barriers'] * 100.0 / r['guest_instructions']), r['function']))
    report = dict(schema=1, label=args.label, units=[p.name for p in files], unit_bytes=sum(p.stat().st_size for p in files),
                  promote=args.promote, promote_stats=stats,
                  functions=len(records), guest_instructions=guest, barriers=barriers,
                  barriers_per_100_guest_instructions=round(barriers * 100.0 / guest, 3) if guest else 0,
                  classes=total,
                  class_cost={k: dict(sites=v[0], spill_transfers=v[1], reload_transfers=v[2],
                                      spill_per_site=round(v[1] / v[0], 2) if v[0] else 0,
                                      reload_per_site=round(v[2] / v[0], 2) if v[0] else 0)
                              for k, v in sorted(cost.items()) if v[0]},
                  spill_statements=sum(r['spill_statements'] for r in records),
                  reload_statements=sum(r['reload_statements'] for r in records),
                  worst_by_count=[{k: r[k] for k in ('function', 'file', 'guest_instructions', 'barriers',
                                                     'poll', 'blockcall', 'call', 'indirect', 'proc_cb',
                                                     'interpret', 'other', 'require_fpu')}
                                  for r in ranked[:args.top]],
                  worst_by_density=[{k: r[k] for k in ('function', 'file', 'guest_instructions', 'barriers',
                                                       'poll', 'blockcall', 'call', 'other')}
                                    for r in dense[:args.top]])
    out = root / 'reports/barrier-census.json'
    out.write_text(json.dumps(report, indent=2) + NL)
    if not args.quiet:
        print('Barrier census%s: %d functions, %d guest instructions, %d barrier lines '
              '(%.2f per 100 guest instructions), %.1f MiB in %d units' %
              ((' [' + args.label + ']') if args.label else '', len(records), guest, barriers,
               report['barriers_per_100_guest_instructions'], report['unit_bytes'] / 1048576.0, len(files)))
        for name in CLASS_NAMES:
            if total[name]:
                entry = cost.get(name)
                extra = ('  spill %.1f/site, reload %.1f/site' %
                         (entry[1] / entry[0], entry[2] / entry[0])) if entry and entry[0] else ''
                print('  %-12s %7d%s' % (name, total[name], extra))
        print('  %-12s %7d' % ('spill stmts', report['spill_statements']))
        print('  %-12s %7d' % ('reload stmts', report['reload_statements']))
        print('Worst by barrier count:')
        for r in ranked[:6]:
            print('  %s %-16s guest=%-6d barriers=%-5d poll=%-4d block=%-3d call=%-4d indirect=%-4d other=%d' %
                  (r['file'], r['function'], r['guest_instructions'], r['barriers'], r['poll'],
                   r['blockcall'], r['call'], r['indirect'], r['other']))
        print('Worst density:')
        for r in dense[:6]:
            print('  %s %-16s guest=%-6d barriers=%-5d %.1f per 100' %
                  (r['file'], r['function'], r['guest_instructions'], r['barriers'],
                   r['barriers'] * 100.0 / r['guest_instructions']))
        print('Report: %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
