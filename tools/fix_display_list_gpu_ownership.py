"""Keep cached encoded draws immutable and refresh every animated matrix bank."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/fifo_parser.cpp';s=f.read_text()
baseline=p/'runtime-overlays/dl-gpu-ownership-before.cpp'
if not baseline.exists():baseline.write_text(s)
# DecodeDraw can receive a shallow command copy from the DL cache. The raw
# stream belongs to that cache even after this command drops its own raw handle.
s=s.replace('snapshot->bytes.clear();native_trace::mark("decode.gpu_vertices",c.vertices.size());','native_trace::mark("decode.gpu_vertices",c.vertices.size());')
if 'NATIVE_DL_GPU_OWNERSHIP_V1' not in s:
    s=s.replace('''                           c.raw->have_snap_mtx) {''','''                           (c.raw->have_snap_mtx || !c.raw->snap_pos_bank.empty())) {''',1)
    old='''                    c.raw = std::make_shared<DrawRaw>(*c.raw);
                    uint32_t pb = (uint32_t)c.raw->defPosMtxIdx * 4;'''
    new='''                    // NATIVE_DL_GPU_OWNERSHIP_V1: cache snapshots are immutable;
                    // replay every matrix bank, including per-vertex indices.
                    c.raw = std::make_shared<DrawRaw>(*c.raw);
                    if (!c.raw->snap_pos_bank.empty()) {
                        c.raw->snap_pos_bank.assign(g_parse_posmtx,g_parse_posmtx+256);
                        c.raw->snap_nrm_bank.assign(g_parse_nrmmtx,g_parse_nrmmtx+96);
                    }
                    uint32_t pb = (uint32_t)c.raw->defPosMtxIdx * 4;'''
    assert s.count(old)==1;s=s.replace(old,new,1)
if f.read_text()!=s:f.write_text(s)
assert 'snapshot->bytes.clear()' not in s
print('Shared display-list bytes preserved; cached single/full matrix snapshots refreshed.')
