"""Index sorted disc extents while preserving overlapping-region read order."""
from pathlib import Path
import argparse

MARKER='NATIVE_DISC_REGION_INDEX_V1'
BUILD='''    // NATIVE_DISC_REGION_INDEX_V1: prefix maxima preserve overlapping extents.
    m_region_ends.resize(m_regions.size());
    uint64_t furthest = 0, previous = 0;
    for (size_t i = 0; i < m_regions.size(); ++i) {
        const auto& r = m_regions[i];
        const uint64_t end = r.offset + r.size;
        if (end < r.offset || (i && r.offset < previous)) {
            m_region_ends.clear(); break; // retain the original scan for irregular input
        }
        previous = r.offset;
        furthest = std::max(furthest, end);
        m_region_ends[i] = furthest;
    }

'''
LOOP='''    const bool indexed = end >= offset && m_region_ends.size() == m_regions.size();
    const size_t first = indexed ? size_t(std::upper_bound(m_region_ends.begin(),
        m_region_ends.end(), offset) - m_region_ends.begin()) : 0;
    for (size_t i = first; i < m_regions.size(); ++i) {
        const auto& r = m_regions[i];
        if (r.offset >= end) {
            if (indexed) break;
            continue;
        }
        if (r.offset + r.size <= offset) continue;'''

def apply_header(source):
    if MARKER in source:
        assert 'std::vector<uint64_t> m_region_ends;' in source
        return source
    old='    std::vector<Region> m_regions;'
    assert source.count(old)==1
    return source.replace(old,old+'\n    std::vector<uint64_t> m_region_ends; // '+MARKER,1)

def apply_cpp(source):
    if MARKER in source:
        assert BUILD in source and LOOP in source
        return source
    old='    m_valid = true;'
    assert source.count(old)==1
    source=source.replace(old,BUILD+old,1)
    old='''    for (const auto& r : m_regions) {
        if (r.offset >= end || r.offset + r.size <= offset)
            continue;'''
    assert source.count(old)==1
    return source.replace(old,LOOP,1)

if __name__=='__main__':
    p=Path(__file__).resolve().parents[1]
    ap=argparse.ArgumentParser();ap.add_argument('--runtime',type=Path,default=p/'build/nwii-full-source/nWiiRuntime')
    args=ap.parse_args()
    for rel,transform in [('include/runtime/virtual_disc.h',apply_header),('src/hle/virtual_disc.cpp',apply_cpp)]:
        path=args.runtime/rel;source=path.read_text();changed=transform(source)
        assert transform(changed)==changed
        if changed!=source:path.write_text(changed)
    print('Disc region prefix index staged; original read order and irregular fallback retained.')
