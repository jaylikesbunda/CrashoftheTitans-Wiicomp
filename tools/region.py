"""Disc releases the builder supports, and how a non-reference release reuses the reference stages.

Every stage, overlay and evidence table was written against the PAL release (RQJP7D, `data/`). Another release of the
same game has the same code at shifted addresses. Its folder `data/regions/<game id>/` holds:

    release.json          game id, main.dol SHA-256, runtime HLE settings, stages that stay reference-only
    function-index.tsv    function extents of that release's main.dol (Ghidra export, same convention as data/)
    address-map.tsv       reference address -> release address, for code (function starts) and data

`rebase_tree()` rewrites the guest addresses in a copied stage tree from the reference release to the active one, so the
stages find their anchors under the release's own names. Stages that check a qualified hash of reference code cannot
apply to another release and are listed in `reference_only_stages`; build_native.py skips them.

The active release is the one whose main.dol SHA-256 matches the build input (`NWII_INPUT_DOL`, set by build_native.py).
"""
from pathlib import Path
import bisect, hashlib, json, os, re

PORT = Path(__file__).resolve().parents[1]
REFERENCE = 'RQJP7D'
_TEXT = (0x80004000, 0x80480000)       # main.dol code of every release
_DATA = (0x80480000, 0x80700000)       # initialised data, bss and SDA


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Release:
    def __init__(self, game_id, folder, info):
        self.game_id, self.folder, self.info = game_id, folder, info
        self.reference = game_id == REFERENCE
        self.dol_sha256 = info['dol_sha256']
        self._map = None
        self._exact = None

    @property
    def index(self):
        return self.folder / 'function-index.tsv'

    @property
    def hle(self):
        return self.info.get('hle', {})

    def exact(self, own_address):
        """True when `own_address` is a function whose instructions equal its reference counterpart's once relocated fields
        (branch targets, `lis` halves, non-stack displacements) are masked. The reference release is exact by definition.

        This is the proof that evidence gathered on the reference release (differential tests, hashes of its translated source)
        carries over: the translated text is a function of the instruction words, so it can differ only in those fields."""
        if self.reference:
            return True
        if self._exact is None:
            self._exact = {own for own, _, kind in self._load_map()[0].values() if kind == 'code'}
        return own_address in self._exact

    def trusted(self, expected_sha256, actual_sha256, *own_addresses):
        """The qualification check for a hash of reference source: equal hashes on the reference release, exact functions elsewhere."""
        if self.reference:
            return expected_sha256 == actual_sha256
        return all(self.exact(a) for a in own_addresses)

    def skips(self, script):
        """Reason a stage script cannot apply to this release, or None."""
        return None if self.reference else self.info.get('reference_only_stages', {}).get(script)

    def _load_map(self):
        if self._map is None:
            code, data = {}, {}
            for line in (self.folder / 'address-map.tsv').read_text().splitlines()[1:]:
                kind, ref, size, own = line.split('\t')[:4]
                (code if kind.startswith('code') else data)[int(ref, 16)] = (int(own, 16), int(size, 0), kind)
            self._map = (code, sorted(code), data, sorted(data))
        return self._map

    def address(self, ref):
        """This release's address for reference address `ref`, or None when it has no exact counterpart."""
        if self.reference:
            return ref
        code, code_keys, data, data_keys = self._load_map()
        if _TEXT[0] <= ref < _TEXT[1]:
            i = bisect.bisect_right(code_keys, ref) - 1
            if i < 0:
                return None
            start = code_keys[i]
            own, size, kind = code[start]
            if ref == start:
                return own
            # Offsets inside a function only carry over when the bodies match instruction for instruction.
            return own + (ref - start) if kind == 'code' and ref - start < size else None
        if _DATA[0] <= ref < _DATA[1]:
            i = bisect.bisect_right(data_keys, ref) - 1
            if i < 0:
                return None
            start = data_keys[i]
            own, size, _ = data[start]
            return own + (ref - start) if ref - start < size else None
        return ref                     # OS globals, hardware and MEM2 are the same on every release


_chunks = {}


def chunk_file(function, recorded, generated=None):
    """The generated `output_N.cpp` that holds func_<function>. Evidence tables record the reference release's chunk, but another
    release splits the functions differently; the reference release (and a function not found) keeps the recorded file."""
    if active().reference:
        return recorded
    import glob
    generated = Path(generated) if generated else PORT / 'build/crash-generated'
    key = (str(generated), function.upper())
    if key not in _chunks:
        _chunks.clear()
        for path in sorted(glob.glob(str(generated / 'output_*.cpp'))):
            text = Path(path).read_text(errors='replace')
            for m in re.finditer(r'(?m)^void func_([0-9A-Fa-f]{8})\(', text):
                _chunks[(str(generated), m.group(1).upper())] = Path(path).name
        _chunks.setdefault(key, None)
    return _chunks[key] or recorded


def releases():
    out = {REFERENCE: Release(REFERENCE, PORT / 'data', {'game_id': REFERENCE,
                                                       'dol_sha256': (PORT / 'data/main.dol.sha256').read_text().split()[0]})}
    root = PORT / 'data/regions'
    if root.is_dir():
        for folder in sorted(p for p in root.iterdir() if (p / 'release.json').is_file()):
            info = json.loads((folder / 'release.json').read_text())
            out[info['game_id']] = Release(info['game_id'], folder, info)
    return out


def for_dol(dol):
    digest = _sha256(dol)
    for release in releases().values():
        if release.dol_sha256 == digest:
            return release
    raise SystemExit('main.dol %s... is not a supported release of Crash of the Titans' % digest[:12])


_active = None


def active():
    """The release being built: from NWII_INPUT_DOL, else the reference release."""
    global _active
    if _active is None:
        dol = os.environ.get('NWII_INPUT_DOL')
        _active = for_dol(dol) if dol and Path(dol).is_file() else releases()[REFERENCE]
    return _active


# ---------------------------------------------------------------- rebasing a copied stage tree

_ADDR = re.compile(r'(?<![0-9A-Za-z])(0x|func_|loc_|FUN_|fn_|LAB_|_)?(80[0-9A-Fa-f]{6})(?=[uUlL]*(?![0-9A-Za-z]))')
_SUFFIXES = {'.py', '.cpp', '.h', '.inc', '.json', '.txt', '.tsv', '.toml'}


def rebase_text(text, release, unmapped=None):
    """Rewrite reference guest addresses in `text` to `release`. Keeps each literal's case (all-digit literals take the
    file's prevailing case); addresses without an exact counterpart are left alone and recorded in `unmapped`."""
    lettered = [m.group(2) for m in _ADDR.finditer(text) if not m.group(2).isdigit()]
    upper_default = sum(a.isupper() for a in lettered) * 2 >= len(lettered)

    def sub(m):
        prefix, lit = m.group(1) or '', m.group(2)
        ref = int(lit, 16)
        own = release.address(ref)
        if own is None:
            if unmapped is not None:
                unmapped.add(lit.upper())
            return m.group(0)
        if lit.isdigit():
            upper = prefix != 'FUN_' and (prefix in ('0x', 'func_', 'loc_') or upper_default)
        else:
            upper = lit.isupper()
        return prefix + (('%08X' if upper else '%08x') % own)
    return _ADDR.sub(sub, text)


def rebase_tree(port, release, folders=('tools', 'compiler', 'runtime-overlays', 'reference', 'data')):
    """Rebase every stage input under the copied tree `port` to `release`. Returns {file: unmapped addresses}."""
    report = {}
    port = Path(port)
    for folder in folders:
        for path in sorted((port / folder).rglob('*')):
            rel = path.relative_to(port).as_posix()
            if not path.is_file() or path.suffix not in _SUFFIXES or rel.startswith('data/regions/') or rel == 'data/function-index.tsv':
                continue
            if path.name == 'region.py':
                continue
            text = path.read_bytes().decode('utf-8', 'surrogateescape')     # keep line endings byte for byte
            unmapped = set()
            new = rebase_text(text, release, unmapped)
            if new != text:
                path.write_bytes(new.encode('utf-8', 'surrogateescape'))
            if unmapped:
                report[rel] = sorted(unmapped)
    # Constants that are not addresses but derive from the release's layout (an instruction word, an SDA displacement).
    for rel, edits in release.info.get('text_overrides', {}).items():
        path = port / rel
        text = path.read_bytes().decode('utf-8', 'surrogateescape')
        for old, new in edits:
            if old not in text:
                raise SystemExit('%s: override anchor %r not found' % (rel, old))
            text = text.replace(old, new)
        path.write_bytes(text.encode('utf-8', 'surrogateescape'))
    return report


def runtime_toml(text, release):
    """runtime.toml for `release`: its game id and HLE settings."""
    text = re.sub(r'(?m)^id = ".*"$', 'id = "%s"' % release.game_id, text)
    for key, value in release.hle.items():
        text = re.sub(r'(?m)^%s = .*$' % re.escape(key), '%s = %s' % (key, value), text)
    return text


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Show the release of a main.dol, or map reference addresses.')
    ap.add_argument('--dol', type=Path)
    ap.add_argument('addresses', nargs='*')
    a = ap.parse_args()
    r = for_dol(a.dol) if a.dol else active()
    print(r.game_id, r.info.get('name', ''), r.dol_sha256)
    for x in a.addresses:
        own = r.address(int(x, 16))
        print('%08X -> %s' % (int(x, 16), '%08X' % own if own is not None else 'no counterpart'))
