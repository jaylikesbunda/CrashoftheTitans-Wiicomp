"""Stage the source-backed native lowerings into the generated guest code.

For every adopted candidate in data/native-lowering-candidates.json this stage

  * renames the translated guest function to `native_original_<addr>`,
  * inserts a native entry that serves the ordinary entry and falls back to
    the translated function for every other entry,
  * rewrites the direct call sites so the caller's values stay in host
    registers instead of round-tripping through the CPUContext.

The translated body is never edited, so a disabled lowering, a traced call or
an interior entry keeps the original behaviour. Running the stage twice
re-stages the same block, and `--restore` puts the translated function back.
"""
from pathlib import Path
import json
import re
import sys

import region
import native_lowering as NL
from native_lowering import (BEGIN, END, GENERATED, PORT, candidates, check_identity,
                              load_manifest, restore_direct_calls, rewrite_direct_calls,
                              stage_chunk)

REPORT = PORT / 'reports/native-lowering-stage.json'


def chunk_for(entry):
    recorded = entry.get('chunk') or entry.get('file')
    if not recorded:
        raise SystemExit('candidate %s has no chunk file' % entry['address'])
    return region.chunk_file(entry['address'], recorded, GENERATED)


def restore(text, address):
    """Undo the staging of one function."""
    begin_marker = BEGIN + address
    end_marker = END + address
    if begin_marker not in text:
        return text, False
    a = text.index(begin_marker)
    z = text.index(end_marker, a) + len(end_marker)
    while z < len(text) and text[z] == '\n':
        z += 1
    block = text[a:z]
    original = block[block.index('static void native_original_' + address):]
    # A later staging stage rewrites the entry signature (it drops the host ABI
    # attribute and spells the context parameter CPUContext&), so the anchor
    # accepts either spelling.
    anchor = re.search(r'\n\n(?:NWII_GUEST_ABI )?void func_' + address + r'\(', original)
    assert anchor, 'the staged entry of func_%s was not found' % address
    original = original[:anchor.start()]
    original = original.replace('static void native_original_' + address,
                                'NWII_GUEST_ABI void func_' + address, 1)
    return text[:a] + original + '\n\n' + text[z:], True


def include_overlays(text, headers):
    """Add the overlay includes the staged code needs."""
    added = []
    for header in headers:
        include = '#include "%s"' % (PORT / header).as_posix()
        if include not in text:
            anchor = '#include "functions.h"'
            if anchor not in text:
                raise SystemExit('cannot place the overlay include: no functions.h anchor')
            text = text.replace(anchor, anchor + '\n' + include, 1)
            added.append(include)
    return text, added


def main():
    restore_mode = '--restore' in sys.argv
    manifest_path = NL.MANIFEST
    if '--manifest' in sys.argv:
        manifest_path = Path(sys.argv[sys.argv.index('--manifest') + 1])
    manifest = load_manifest(manifest_path)
    adopted = candidates(manifest)
    problems = check_identity(manifest, adopted)
    if problems:
        for problem in problems:
            print('IDENTITY: ' + problem)
        raise SystemExit('the native lowering qualification is stale: requalify the candidates')
    headers = []
    for entry in adopted:
        header = entry.get('header')
        if header and header not in headers:
            headers.append(header)

    by_chunk = {}
    for entry in adopted:
        by_chunk.setdefault(chunk_for(entry), []).append(entry)

    # The direct call sites of a candidate live in other chunks, so every
    # chunk is scanned for them.
    call_targets = {e['address'].upper(): e for e in adopted}

    report = {'mode': 'restore' if restore_mode else 'stage', 'functions': [], 'chunks': []}
    for path in sorted(GENERATED.glob('output_*.cpp')):
        text = path.read_text(errors='surrogateescape')
        before = text
        chunk = path.name
        entries = by_chunk.get(chunk, [])
        touched = False
        for entry in entries:
            address = entry['address'].upper()
            if restore_mode:
                text, changed = restore(text, address)
                if changed:
                    touched = True
                restored = restore_direct_calls(text, entry)
                if restored != text:
                    touched = True
                text = restored
                report['functions'].append({'address': address, 'restored': changed})
            elif entry.get('native_entry', 'definition') != 'call_sites_only':
                text, stats = stage_chunk(text, entry)
                report['functions'].append(stats)
                touched = True
        # The call sites, in this chunk and in every other one.
        for address, entry in call_targets.items():
            if restore_mode:
                restored = restore_direct_calls(text, entry)
                if restored != text:
                    touched = True
                text = restored
            else:
                text, calls = rewrite_direct_calls(text, entry)
                if calls:
                    touched = True
                    report['functions'].append({'address': address, 'chunk': chunk,
                                                'direct_calls_rewritten': calls})
        if restore_mode:
            # This stage is the only writer of the overlay includes, so the
            # restore removes them unconditionally: that keeps the restore
            # idempotent even when an earlier run was interrupted.
            for header in headers:
                include = '#include "%s"\n' % (PORT / header).as_posix()
                text = text.replace(include, '')
        elif touched:
            text, added = include_overlays(text, headers)
            if added:
                report['chunks'].append({'chunk': chunk, 'includes': added})
        if text != before:
            path.write_text(text, errors='surrogateescape')
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + '\n')
    count = len(report['functions'])
    if restore_mode:
        print('Native lowering staging restored for %d functions' % count)
    else:
        print('Native lowering staged for %d functions' % count)
        for entry in report['functions']:
            print('  %s: %d direct call sites rewritten' %
                  (entry['address'], entry.get('direct_calls_rewritten', 0)))


if __name__ == '__main__':
    main()
