"""Remove redundant owned copies in display-list parsing, preserving snapshots."""
from pathlib import Path
import argparse, base64, re

MARKER = 'NATIVE_PARSE_WORK_REUSE_V1'
BLOCK = re.compile(r'/\* '+MARKER+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARKER+r' \*/', re.S)
INPLACE = 'NATIVE_PARSE_IN_PLACE_V1'
INPLACE_BLOCK = re.compile(r'/\* '+INPLACE+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+INPLACE+r' \*/', re.S)

def restore(source):
    source = INPLACE_BLOCK.sub(lambda m: base64.b64decode(m[1]).decode(), source)
    source = BLOCK.sub(lambda m: base64.b64decode(m[1]).decode(), source)
    # fix_gx_work_metrics.py nests its own marker block inside these, and the pair can be left
    # incomplete (a begin marker consumed by the nested edit). The markers are comments only, so drop
    # whatever is left instead of refusing to restore a tree this stage already patched: the staged
    # code around an orphaned end marker is semantically the same as the original it replaced.
    for name in (MARKER, INPLACE):
        source = re.sub(r'/\* (?:END_)?' + name + r'(?: [A-Za-z0-9+/=]+)? \*/\n', '', source)
    return source

def apply_inplace(source):
    signature='static void ParseStream(const std::vector<uint8_t>& fifo, size_t& offset, std::vector<GXCommand>& commands, int depth) {'
    start=source.index(signature)
    # ParseStream is the last function before the namespace terminator.
    boundary=source.rfind('\n}'); end=source.rfind('\n}',start,boundary)+2
    if end <= start or source[end:boundary].strip(): raise ValueError('ParseStream staging boundary changed')
    old=source[start:end]
    if old.count('GXCommand c;') != 6 or old.count('commands.push_back(std::move(c));') != 6:
        raise ValueError('Parser command construction sites changed')
    body=old.replace(signature,'template<bool NativeInPlace> static void NativeParseStreamImpl(const std::vector<uint8_t>& fifo, size_t& offset, std::vector<GXCommand>& commands, int depth) {')
    body=body.replace('GXCommand c;', 'NativeParseCommandSlot<NativeInPlace> native_slot(commands);\n            GXCommand& c=native_slot.value;')
    body=body.replace('commands.push_back(std::move(c));','native_slot.Commit(commands);')
    direct='            c.type = GXCommandType::XFRegister;\n            c.length = length;'
    if body.count(direct) != 1: raise ValueError('Direct XF marker initialization anchor changed')
    body=body.replace(direct,'            c.type = GXCommandType::XFRegister;\n            c.val = 0; // Direct XF loads never carry the LOAD_INDX replay marker.\n            c.length = length;')
    helper='''template<bool InPlace> struct NativeParseCommandSlot;
template<> struct NativeParseCommandSlot<false> {
    GXCommand value;
    explicit NativeParseCommandSlot(std::vector<GXCommand>&) {}
    void Commit(std::vector<GXCommand>& commands) { commands.push_back(std::move(value)); }
};
template<> struct NativeParseCommandSlot<true> {
    GXCommand& value;
    explicit NativeParseCommandSlot(std::vector<GXCommand>& commands):value(commands.emplace_back()) {}
    void Commit(std::vector<GXCommand>&) {}
};
'''
    dispatcher='''
static void ParseStream(const std::vector<uint8_t>& fifo, size_t& offset, std::vector<GXCommand>& commands, int depth) {
    static const bool inplace=std::getenv("NWII_NO_PARSE_IN_PLACE")==nullptr;
    if (inplace) NativeParseStreamImpl<true>(fifo,offset,commands,depth);
    else NativeParseStreamImpl<false>(fifo,offset,commands,depth);
}'''
    new=helper+body+dispatcher
    source=source[:start]+'/* '+INPLACE+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+INPLACE+' */'+source[end:]
    return source

def apply(source):
    source = restore(source)
    def replace(old, new):
        nonlocal source
        if source.count(old) == 1:
            source = source.replace(old, '/* '+MARKER+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+MARKER+' */')
            return
        if source.count(old) == 0:
            # Already staged, or a later stage rewrote the region (these blocks can nest another
            # stage's marker block); either way this anchor has nothing left to do.
            return
        raise ValueError('Parse reuse anchor is ambiguous: '+old[:100])
    replace('    static const bool nocache = std::getenv("NWII_NODLCACHE") != nullptr;',
            '    static const bool nocache = std::getenv("NWII_NODLCACHE") != nullptr;\n'
            '    static const bool native_parse_reuse = std::getenv("NWII_NO_PARSE_WORK_REUSE") == nullptr;')
    replace('            for (const GXCommand& c0 : it->second.cmds) {',
            '            if (native_parse_reuse) {\n'
            '                const size_t required=commands.size()+it->second.cmds.size();\n'
            '                if (required>commands.capacity())\n'
            '                    commands.reserve(std::max(required,commands.capacity()<=commands.max_size()/2 ? commands.capacity()*2 : required));\n'
            '            }\n'
            '            for (const GXCommand& c0 : it->second.cmds) {')
    old = '                    auto snapshot=NativeAcquireDrawRaw(); *snapshot=*c.raw; c.raw=std::move(snapshot);'
    new = '''                    const bool native_refresh_banks=!c.raw->snap_pos_bank.empty();
                    auto snapshot=NativeAcquireDrawRaw();
                    if (!native_parse_reuse || c.raw->snap_pos_bank.empty()) {
                        *snapshot=*c.raw;
                    } else {
                        // Copy every non-bank field. Both banks are filled from
                        // the current parse shadow below, never from stale cache.
                        snapshot->vat=c.raw->vat;
                        std::memcpy(snapshot->arrayBase,c.raw->arrayBase,sizeof(snapshot->arrayBase));
                        std::memcpy(snapshot->arrayStride,c.raw->arrayStride,sizeof(snapshot->arrayStride));
                        snapshot->defPosMtxIdx=c.raw->defPosMtxIdx;
                        std::memcpy(snapshot->defTexMtxIdx,c.raw->defTexMtxIdx,sizeof(snapshot->defTexMtxIdx));
                        snapshot->need_normal=c.raw->need_normal;
                        snapshot->have_snap_mtx=c.raw->have_snap_mtx;
                        std::memcpy(snapshot->snap_pos_mtx,c.raw->snap_pos_mtx,sizeof(snapshot->snap_pos_mtx));
                        std::memcpy(snapshot->snap_nrm_mtx,c.raw->snap_nrm_mtx,sizeof(snapshot->snap_nrm_mtx));
                        snapshot->count=c.raw->count;
                        snapshot->bytes=c.raw->bytes;
                    }
                    c.raw=std::move(snapshot);'''
    replace(old,new)
    replace('                    if (!c.raw->snap_pos_bank.empty()) {',
            '                    if (native_refresh_banks) {')
    replace('        std::vector<uint8_t> dl(src, src + size);',
            '        std::vector<uint8_t> dl;\n'
            '        if (native_parse_reuse && !snap.empty()) dl=std::move(snap);\n'
            '        else dl.assign(src, src + size);')
    replace('        commands.insert(commands.end(), local.begin(), local.end());',
            '        if (native_parse_reuse)\n'
            '            commands.insert(commands.end(), std::make_move_iterator(local.begin()), std::make_move_iterator(local.end()));\n'
            '        else commands.insert(commands.end(), local.begin(), local.end());')
    replace('    std::vector<uint8_t> dl(size);\n    if (src) {\n        std::memcpy(dl.data(), src, size);\n    } else {',
            '    std::vector<uint8_t> dl;\n'
            '    if (native_parse_reuse && !snap.empty()) {\n'
            '        dl=std::move(snap);\n'
            '    } else if (src) {\n'
            '        dl.assign(src, src + size);\n'
            '    } else {\n        dl.resize(size);')
    return apply_inplace(source)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--restore',action='store_true')
    args=parser.parse_args()
    runtime=args.port/'build/nwii-full-source/nWiiRuntime'
    path=runtime/'src/hle/gx/fifo_parser.cpp'
    if not args.restore:
        header=(runtime/'include/runtime/gx/fifo_parser.h').read_text()
        body=header.split('struct DrawRaw {',1)[1].split('\n};',1)[0]
        body=re.sub(r'//[^\n]*','',body)
        fields=[re.search(r'(\w+)\s*(?:\[[^]]*\])?\s*(?:=[^;]*)?$',row.strip())[1]
                for row in body.split(';') if row.strip()]
        expected=['vat','arrayBase','arrayStride','defPosMtxIdx','defTexMtxIdx','need_normal','have_snap_mtx',
                  'snap_pos_mtx','snap_nrm_mtx','snap_pos_bank','snap_nrm_bank','count','bytes']
        if fields != expected: raise ValueError('DrawRaw schema changed: update and qualify complete snapshot copy')
    old=path.read_text(); new=restore(old) if args.restore else apply(old)
    if old != new: path.write_text(new)
    print('Display-list owned-copy reuse restored.' if args.restore else 'Display-list owned-copy reuse staged; snapshots and original rollback retained.')

if __name__=='__main__': main()
