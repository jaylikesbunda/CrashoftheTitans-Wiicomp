"""NATIVE_DIRECT_FIFO_STREAM_V1 -- pilot patch stage.

Replaces the drain-local std::vector<GXCommand> with an ordered CommandStream
that stores BP/CP register packets inline (12-byte POD, no GXCommand) and keeps
XF bursts / draws / irrelevant heavy payloads as index-referenced GXCommand
objects. Emitted directly by the parser; not a post-parse conversion.

This pilot is opt-in (NWII_DIRECT_FIFO=1; NWII_NO_DIRECT_FIFO forces heavy
emission). Each rewritten file captures its own pre-stage bytes; restoration
never replaces the current tree with another build's saved originals.

Usage:
  python fix_direct_fifo.py --port <tree root containing build/nwii-full-source/nWiiRuntime>
  python fix_direct_fifo.py --port <...> --restore
"""
from pathlib import Path
import argparse, re, sys, base64, hashlib

MARK = 'NATIVE_DIRECT_FIFO_STREAM_V1'
HERE = Path(__file__).resolve().parent
DIR = HERE / 'direct_fifo'
FILES_DIR = DIR / 'files'
RESTORE_MARK = 'NATIVE_DIRECT_FIFO_RESTORE_V1'
SNAPSHOT = re.compile(rb'\A/\* '+RESTORE_MARK.encode()+rb' ([0-9a-f]{64}) ([A-Za-z0-9+/=]+) \*/\r?\n(.*?)\r?\n/\* END_'+RESTORE_MARK.encode()+rb' \*/\r?\n\Z', re.S)

PARSER_REL = 'src/hle/gx/fifo_parser.cpp'
RENDER_REL = 'src/hle/gx/renderer_gl.cpp'


class AnchorError(RuntimeError):
    pass


def rep(text, old, new, count=1, label=''):
    n = text.count(old)
    if n != count:
        raise AnchorError(f'[{label}] expected {count} occurrences, found {n}: {old[:90]!r}')
    return text.replace(old, new, count)


def splice(text, start_anchor, end_anchor, new, label=''):
    s = text.find(start_anchor)
    if s < 0:
        raise AnchorError(f'[{label}] start anchor missing: {start_anchor[:90]!r}')
    e = text.find(end_anchor, s)
    if e < 0:
        raise AnchorError(f'[{label}] end anchor missing: {end_anchor[:90]!r}')
    return text[:s] + new + text[e:]


# ---------------------------------------------------------------- parser ----

EMIT_HELPERS = '''// ''' + MARK + ''' order-preserving stream emission.
static void ParseStream(const std::vector<uint8_t>& fifo, size_t& offset, CommandStream& commands, int depth);

// Direct BP/CP emission: inline POD unless direct mode is disabled
// (NWII_NO_DIRECT_FIFO=1) or the register carries variable-length side data
// (BP 0x65 TLUT snapshot), which keeps a heavy GXCommand.
static inline void EmitBp(CommandStream& commands,uint8_t reg,uint32_t val,native_gx_work::ConstructionBatch& stats){
 if(stats.active)++stats.values[0];
 const bool direct=NativeDirectFifoEnabled();
 if(direct && reg!=0x65){commands.EmitRegister(GXCommandType::BPRegister,reg,val);return;}
 GXCommand& c=commands.EmitHeavy(GXCommandType::BPRegister,reg,val);c.val=val;
}
static inline void EmitCp(CommandStream& commands,uint8_t reg,uint32_t val,native_gx_work::ConstructionBatch& stats){
 if(stats.active)++stats.values[1];
 const bool direct=NativeDirectFifoEnabled();
 if(direct){commands.EmitRegister(GXCommandType::CPRegister,reg,val);return;}
 GXCommand& c=commands.EmitHeavy(GXCommandType::CPRegister,reg,val);c.val=val;
}'''

RECYCLE_NEW = '''void NativeRecycleStream(CommandStream& stream){
 if(!NativeCommandReuseEnabled()){stream.clear();return;}
 auto& pool=NativeCommandPool();std::lock_guard<std::mutex> lock(pool.mutex);
 for(auto& c:stream.heavy){
  // A command's two aliases need not prevent recycling once both are retired.
  c.native_gpu_snapshot.reset();
  if(c.raw && c.raw.use_count()==1 && pool.draw_count<pool.draws.size()){
   const size_t bytes=NativeDrawCapacity(*c.raw);
   if(bytes<=(32u<<20)-pool.draw_bytes){pool.draw_bytes+=bytes;pool.draws[pool.draw_count++]=std::move(c.raw);}
  }
  if(c.payload.capacity() && c.payload.capacity()<=1024 && pool.payload_count<pool.payloads.size()){
   NativeReleasePayload(pool,c.payload);
  }
 }
 stream.clear();
}'''

REPLAY_NEW = '''            g_prof_dl_hits++;
            const CommandStream& native_cached = it->second.cmds;
            if (native_parse_reuse) {
                const size_t required = commands.entries.size() + native_cached.entries.size();
                if (required > commands.entries.capacity()) commands.entries.reserve(required);
                const size_t required_heavy = commands.heavy.size() + native_cached.heavy.size();
                if (required_heavy > commands.heavy.capacity()) commands.heavy.reserve(required_heavy);
            }
            for (const StreamEntry& e0 : native_cached.entries) {
                StreamEntry e = e0;
                GXCommand* native_cp = nullptr;
                if (e0.heavy != StreamEntry::kNoHeavy) {
                    commands.heavy.push_back(native_cached.heavy[e0.heavy]);
                    e.heavy = (uint32_t)(commands.heavy.size() - 1);
                    native_cp = &commands.heavy.back();
                }
                commands.entries.push_back(e);
                // Replay parser-state side effects so main-stream commands
                // after the CALL still see the DL's CP/BP writes.
                if (e.kind == (uint8_t)GXCommandType::CPRegister)
                    ParseCP((uint8_t)e.reg, e.val);
                else if (e.kind == (uint8_t)GXCommandType::BPRegister)
                    ParseBP((uint8_t)e.reg, e.val);
                if (!native_cp) continue;
                GXCommand& c = *native_cp;
                if (c.type == GXCommandType::XFRegister &&
                         (c.val & 0x80000000u)) {
                    // LOAD_INDX marker: re-read the (possibly animated)
                    // matrix data from guest RAM at today's array base.
                    int array = 12 + ((c.val >> 24) & 0x3);
                    int num = (c.val >> 16) & 0xFF;
                    uint32_t index = c.val & 0xFFFF;
                    uint32_t base = g_state.arrayBase[array] +
                                    index * g_state.arrayStride[array];
                    for (int i = 0; i < num && i < (int)c.payload.size(); i++)
                        c.payload[i] =
                            nwii::runtime::g_mmu->read_f32(base + i * 4);
                    if (c.reg < 256 || (c.reg >= 0x400 && c.reg < 0x460))
                        ParseApplyMatrixWrite(c.reg, c.payload);
                } else if (c.type == GXCommandType::XFRegister &&
                           (c.reg < 256 || (c.reg >= 0x400 && c.reg < 0x460))) {
                    // Direct matrix write cached in the DL: feed the parse
                    // shadow so re-snapshots below stay live.
                    ParseApplyMatrixWrite(c.reg, c.payload);
                } else if (c.type == GXCommandType::DrawPrimitive && c.raw &&
                           (c.raw->have_snap_mtx || !c.raw->snap_pos_bank.empty())) {
                    // Re-snapshot the matrix from the current parse shadow so a
                    // cached skinned draw animates instead of freezing at the
                    // pose from when it was first parsed. (raw is shared_ptr;
                    // copy before mutating so the cache entry stays pristine.)
                    // NATIVE_DL_GPU_OWNERSHIP_V1: cache snapshots are immutable;
                    // replay every matrix bank, including per-vertex indices.
                    native_trace::Scope dl_snapshot("gx.dl_snapshot");
                    const bool native_refresh_banks=!c.raw->snap_pos_bank.empty();
                    auto snapshot=NativeAcquireDrawRaw();
                    if (!native_parse_reuse || c.raw->snap_pos_bank.empty()) {
                        *snapshot=*c.raw;
                    } else {
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
                        native_gx_work::Add(native_gx_work::BankBytesAvoided,4ull*(c.raw->snap_pos_bank.size()+c.raw->snap_nrm_bank.size()));
                    }
                    c.raw=std::move(snapshot);
                    if (native_refresh_banks) {
                        c.raw->snap_pos_bank.assign(g_parse_posmtx,g_parse_posmtx+256);
                        c.raw->snap_nrm_bank.assign(g_parse_nrmmtx,g_parse_nrmmtx+96);
                    }
                    uint32_t pb = (uint32_t)c.raw->defPosMtxIdx * 4;
                    uint32_t nb = 3u * (c.raw->defPosMtxIdx & 31);
                    if (pb + 12 <= 256 && nb + 9 <= 96) {
                        std::memcpy(c.raw->snap_pos_mtx, &g_parse_posmtx[pb], 12 * sizeof(float));
                        std::memcpy(c.raw->snap_nrm_mtx, &g_parse_nrmmtx[nb], 9 * sizeof(float));
                    }
                }
            }
'''

MISS_NEW = '''        CommandStream local;
        ParseStream(dl, off, local, depth + 1);
        if (s_dl_cache.size() > 512)
            s_dl_cache.clear();
        s_dl_cache[key] = {sh, ch, local};
        native_gx_work::Add(native_gx_work::CommandsMoved,local.entries.size());
        if (native_parse_reuse) commands.TakeFrom(local);
        else commands.AppendFrom(local);
'''

CONSTRUCTION_METRICS = re.compile(
    r'[ \t]*/\* NATIVE_GX_WORK_METRICS_V1 [A-Za-z0-9+/=]+ \*/\n'
    r'if\(native_constructed\.active\)\{\n(?:.*\n)*?'
    r'    native_slot\.Commit\(commands\);\n'
    r'/\* END_NATIVE_GX_WORK_METRICS_V1 \*/\n')


def apply_parser(text: str) -> str:
    if MARK in text:
        return text
    # 1. recycle function
    text = rep(text,
        'void NativeRecycleCommands(std::vector<GXCommand>& commands){',
        'void NativeRecycleStream(CommandStream& stream){',
        label='recycle-head')
    text = rep(text,
        ' if(!NativeCommandReuseEnabled())return;auto& pool=NativeCommandPool();std::lock_guard<std::mutex> lock(pool.mutex);\n for(auto& c:commands){',
        ' if(!NativeCommandReuseEnabled()){stream.clear();return;}auto& pool=NativeCommandPool();std::lock_guard<std::mutex> lock(pool.mutex);\n for(auto& c:stream.heavy){',
        label='recycle-body')
    text = rep(text,
        '''   NativeReleasePayload(pool,c.payload);\n  }\n }
}''',
        '''   NativeReleasePayload(pool,c.payload);\n  }\n }\n stream.clear();\n}''',
        label='recycle-tail')
    # 2. forward decl + Emit helpers
    text = rep(text,
        '''// NATIVE_COMMAND_REUSE_END\nstatic void ParseStream(const std::vector<uint8_t>& fifo, size_t& offset, std::vector<GXCommand>& commands, int depth);''',
        '// NATIVE_COMMAND_REUSE_END\n' + EMIT_HELPERS,
        label='parse-fwd')
    # 3. DL cache type
    text = rep(text, '    std::vector<GXCommand> cmds;', '    CommandStream cmds;', label='dl-cmds')
    # 4. ExpandDisplayList signature
    text = rep(text,
        '                              std::vector<GXCommand>& commands, int depth) {',
        '                              CommandStream& commands, int depth) {',
        label='dl-sig')
    # 5. replay loop -> stream
    text = splice(text, '            g_prof_dl_hits++;\n',
                  '            auto dl1 = std::chrono::steady_clock::now();\n            g_prof_dl_us +=',
                  REPLAY_NEW, label='dl-replay')
    # 6. miss block -> stream
    text = splice(text, '        std::vector<GXCommand> local;\n',
                  '/* END_NATIVE_PARSE_WORK_REUSE_V1 */\n        auto dl1',
                  MISS_NEW, label='dl-miss')
    # 7. Parse signature
    text = rep(text,
        'void FifoParser::Parse(std::vector<uint8_t>& fifo, std::vector<GXCommand>& commands) {',
        'void FifoParser::Parse(std::vector<uint8_t>& fifo, CommandStream& commands) {',
        label='parse-sig')
    # 8. remove construction metrics bodies (6x)
    text, n = CONSTRUCTION_METRICS.subn('', text)
    if n != 6:
        raise AnchorError(f'expected 6 construction metric blocks, removed {n}')
    # 9. drop ParseCommandSlot machinery + retarget impl
    text = re.sub(
        r'template<bool InPlace> struct NativeParseCommandSlot;\n'
        r'template<> struct NativeParseCommandSlot<false> \{.*?\n\};\n'
        r'template<> struct NativeParseCommandSlot<true> \{.*?\n\};\n',
        '', text, count=1, flags=re.S)
    text = rep(text,
        '''/* NATIVE_GX_WORK_METRICS_V1 dGVtcGxhdGU8Ym9vbCBOYXRpdmVJblBsYWNlPiBzdGF0aWMgdm9pZCBOYXRpdmVQYXJzZVN0cmVhbUltcGwoY29uc3Qgc3RkOjp2ZWN0b3I8dWludDhfdD4mIGZpZm8sIHNpemVfdCYgb2Zmc2V0LCBzdGQ6OnZlY3RvcjxHWENvbW1hbmQ+JiBjb21tYW5kcywgaW50IGRlcHRoKSB7 */\ntemplate<bool NativeInPlace> static void NativeParseStreamImpl(const std::vector<uint8_t>& fifo, size_t& offset, std::vector<GXCommand>& commands, int depth) {\n    native_gx_work::ConstructionBatch native_constructed;\n/* END_NATIVE_GX_WORK_METRICS_V1 */''',
        'static void NativeParseStreamImpl(const std::vector<uint8_t>& fifo, size_t& offset, CommandStream& commands, int depth) {',
        label='impl-sig')
    # 10. emission sites
    text = rep(text,
        '''            NativeParseCommandSlot<NativeInPlace> native_slot(commands);\n            GXCommand& c=native_slot.value;\n            c.reg = reg;\n            if (kind == 0) {\n                uint32_t merged = (g_state.bp[reg] & ~mask) | (val & mask);\n                ParseBP(reg, merged);\n                c.type = GXCommandType::BPRegister;\n                c.val = merged;\n            } else {\n                uint32_t merged = (g_state.cp[reg] & ~mask) | (val & mask);\n                g_state.cp[reg] = merged;\n                ParseCP(reg, merged);\n                c.type = GXCommandType::CPRegister;\n                c.val = merged;\n            }''',
        '''            if (kind == 0) {\n                uint32_t merged = (g_state.bp[reg] & ~mask) | (val & mask);\n                ParseBP(reg, merged);\n                EmitBp(commands, reg, merged);\n            } else {\n                uint32_t merged = (g_state.cp[reg] & ~mask) | (val & mask);\n                g_state.cp[reg] = merged;\n                ParseCP(reg, merged);\n                EmitCp(commands, reg, merged);\n            }''',
        label='emit-rmw')
    text = rep(text,
        '''                NativeParseCommandSlot<NativeInPlace> native_slot(commands);\n            GXCommand& c=native_slot.value;\n                c.type = GXCommandType::XFRegister;\n                c.reg = xf_addr;\n                c.length = num - 1;\n                // LOAD_INDX marker for the DL cache: array/num/index packed so\n                // a cache hit can re-read live matrix data.\n                c.val = 0x80000000u | ((uint32_t)(array - 12) << 24) |\n                        ((uint32_t)num << 16) | (index & 0xFFFF);''',
        '''                // LOAD_INDX marker for the DL cache: array/num/index packed so\n                // a cache hit can re-read live matrix data.\n                const uint32_t native_marker = 0x80000000u | ((uint32_t)(array - 12) << 24) |\n                        ((uint32_t)num << 16) | (index & 0xFFFF);\n                GXCommand& c = commands.EmitHeavy(GXCommandType::XFRegister, (uint16_t)xf_addr, native_marker);\n                c.length = num - 1;\n                c.val = native_marker;''',
        label='emit-loadindx')
    text = rep(text,
        '''            NativeParseCommandSlot<NativeInPlace> native_slot(commands);\n            GXCommand& c=native_slot.value;\n            c.type = GXCommandType::BPRegister;\n            c.reg = reg;\n            c.val = val;''',
        '            EmitBp(commands, reg, val);',
        label='emit-bp')
    text = rep(text,
        '''            NativeParseCommandSlot<NativeInPlace> native_slot(commands);\n            GXCommand& c=native_slot.value;\n            c.type = GXCommandType::CPRegister;\n            c.reg = reg;\n            c.val = val;''',
        '            EmitCp(commands, reg, val);',
        label='emit-cp')
    text = rep(text,
        '''            NativeParseCommandSlot<NativeInPlace> native_slot(commands);\n            GXCommand& c=native_slot.value;\n            c.type = GXCommandType::XFRegister;\n            c.val = 0; // Direct XF loads never carry the LOAD_INDX replay marker.\n            c.length = length;\n            c.reg = (fifo[offset + 3] << 8) | fifo[offset + 4];''',
        '''            GXCommand& c = commands.EmitHeavy(GXCommandType::XFRegister,\n                (uint16_t)((fifo[offset + 3] << 8) | fifo[offset + 4]));\n            c.val = 0; // Direct XF loads never carry the LOAD_INDX replay marker.\n            c.length = length;''',
        label='emit-xf')
    text = rep(text,
        '''            NativeParseCommandSlot<NativeInPlace> native_slot(commands);\n            GXCommand& c=native_slot.value;\n            c.type = GXCommandType::DrawPrimitive;''',
        '            GXCommand& c = commands.EmitHeavy(GXCommandType::DrawPrimitive);',
        label='emit-draw')
    # 11. dispatcher
    text = rep(text,
        '''static void ParseStream(const std::vector<uint8_t>& fifo, size_t& offset, std::vector<GXCommand>& commands, int depth) {\n    static const bool inplace=std::getenv("NWII_NO_PARSE_IN_PLACE")==nullptr;\n    if (inplace) NativeParseStreamImpl<true>(fifo,offset,commands,depth);\n    else NativeParseStreamImpl<false>(fifo,offset,commands,depth);\n}''',
        '''static void ParseStream(const std::vector<uint8_t>& fifo, size_t& offset, CommandStream& commands, int depth) {\n    NativeParseStreamImpl(fifo,offset,commands,depth);\n}''',
        label='parse-dispatch')
    # Count fresh parse packets in either representation without per-packet
    # atomics. Cached replay is measured separately by drain record counts.
    text=rep(text,'static void NativeParseStreamImpl(const std::vector<uint8_t>& fifo, size_t& offset, CommandStream& commands, int depth) {',
             'static void NativeParseStreamImpl(const std::vector<uint8_t>& fifo, size_t& offset, CommandStream& commands, int depth) {\n    native_gx_work::ConstructionBatch native_constructed;',label='stream-counter-batch')
    for old in ('EmitBp(commands, reg, merged);','EmitCp(commands, reg, merged);','EmitBp(commands, reg, val);','EmitCp(commands, reg, val);'):
        text=rep(text,old,old[:-2]+',native_constructed);',label='register-counter')
    text=rep(text,'                c.val = native_marker;', '                c.val = native_marker;\n                if(native_constructed.active)++native_constructed.values[2];',label='indexed-xf-counter')
    text=rep(text,'            c.val = 0; // Direct XF loads never carry the LOAD_INDX replay marker.', '            if(native_constructed.active)++native_constructed.values[2];\n            c.val = 0; // Direct XF loads never carry the LOAD_INDX replay marker.',label='direct-xf-counter')
    text=rep(text,'            GXCommand& c = commands.EmitHeavy(GXCommandType::DrawPrimitive);', '            GXCommand& c = commands.EmitHeavy(GXCommandType::DrawPrimitive);\n            if(native_constructed.active)++native_constructed.values[3];',label='draw-counter')
    return text


EARLY_PE_NEW = r'''static void NativeSignalGpuCompletionEarly(nwii::runtime::gx::CommandStream& frame) {
    using namespace nwii::runtime::gx;
    for (auto& e : frame.entries) {
        if (e.kind != (uint8_t)GXCommandType::BPRegister) continue;
        const uint8_t reg = uint8_t(e.reg);
        const uint32_t resolved = ((s_early_bp_shadow[reg] & ~s_early_bp_mask) | (e.val & s_early_bp_mask)) & 0xffffff;
        s_early_bp_shadow[reg] = resolved;
        s_early_bp_mask = reg == 0xfe ? resolved : 0xffffff;
        if (reg == 0x65) { // TLUT load: copy the source now, exactly as ApplyBPRegister(0x65) would
            uint32_t src = (s_early_bp_shadow[0x64] & 0xFFFFFF) << 5;
            if (nwii::runtime::Config::get().platform == nwii::runtime::Platform::GameCube) src &= 0x01FFFFFF;
            const uint32_t dst = (resolved & 0x3FF) << 9, bytes = ((resolved >> 10) & 0x7FF) * 32;
            if (nwii::runtime::g_mmu && dst + bytes <= sizeof(nwii::runtime::gx::g_state.tlutMem)) {
                GXCommand* h = frame.Heavy(e);
                if (!h) {
                    e.heavy = (uint32_t)frame.heavy.size();
                    frame.heavy.emplace_back();
                    h = &frame.heavy.back();
                    h->type = GXCommandType::BPRegister; h->reg = reg; h->val = e.val;
                }
                h->native_tlut.resize(bytes);
                if (const uint8_t* p = nwii::runtime::g_mmu->get_ptr(src)) { if (bytes) std::memcpy(h->native_tlut.data(), p, bytes); }
                else for (uint32_t i = 0; i < bytes; ++i) h->native_tlut[i] = nwii::runtime::g_mmu->read8(src + i);
                h->native_tlut_snapshot = true;
                e.flags |= kStreamTlutSnapshot;
            }
        }
        if (reg == 0x45 && (resolved & 0xffu) == 2u) {
            native_trace::mark("pe.early_signal", 0x45, resolved);
            nwii::runtime::hw::pe_signal_finish();
            e.flags |= kStreamSignalled;
        } else if (reg == 0x47 || reg == 0x48) {
            native_trace::mark("pe.early_signal", reg, resolved & 0xffffu);
            nwii::runtime::hw::pe_signal_token(resolved & 0xffffu, reg == 0x48);
            e.flags |= kStreamSignalled;
        }
    }
}'''


def apply_gx(text: str) -> str:
    if 'NativeRecycleStream' in text:
        return text
    text='#include "runtime/native_gx_work_metrics.h"\n'+text
    text = rep(text,
        'namespace nwii::runtime::gx {void NativeRecycleCommands(std::vector<GXCommand>&);}',
        'namespace nwii::runtime::gx {void NativeRecycleStream(CommandStream&);}',
        label='gx-recycle-decl')
    text = splice(text,
        'static void NativeSignalGpuCompletionEarly(std::vector<nwii::runtime::gx::GXCommand>& frame) {\n',
        '\n// NATIVE_EARLY_PE_SIGNAL_END', EARLY_PE_NEW, label='gx-early-pe')
    text = rep(text,
        '''    static std::vector<nwii::runtime::gx::GXCommand> s_command_pool;
    static bool s_command_pool_busy=false;
    std::vector<nwii::runtime::gx::GXCommand> local_commands;
    const bool use_command_pool=!s_command_pool_busy;
    if(use_command_pool){s_command_pool_busy=true;s_command_pool.clear();if(s_command_pool.capacity()<2048)s_command_pool.reserve(2048);}
    struct NativeCommandPoolRelease{bool active;~NativeCommandPoolRelease(){if(active){native_trace::Scope release("gx.command_release");nwii::runtime::gx::NativeRecycleCommands(s_command_pool);s_command_pool.clear();s_command_pool_busy=false;}}} native_command_pool_release{use_command_pool};
    std::vector<nwii::runtime::gx::GXCommand>& commands=use_command_pool?s_command_pool:local_commands;''',
        '''    static nwii::runtime::gx::CommandStream s_command_pool;
    static bool s_command_pool_busy=false;
    nwii::runtime::gx::CommandStream local_commands;
    const bool use_command_pool=!s_command_pool_busy;
    if(use_command_pool){s_command_pool_busy=true;s_command_pool.clear();s_command_pool.reserve(2048,256);}
    struct NativeCommandPoolRelease{bool active;~NativeCommandPoolRelease(){if(active){native_trace::Scope release("gx.command_release");nwii::runtime::gx::NativeRecycleStream(s_command_pool);s_command_pool_busy=false;}}} native_command_pool_release{use_command_pool};
    nwii::runtime::gx::CommandStream& commands=use_command_pool?s_command_pool:local_commands;''',
        label='gx-pool')
    text = rep(text,
        '    std::vector<nwii::runtime::gx::GXCommand>& frame=commands;',
        '    nwii::runtime::gx::CommandStream& frame=commands;\n'
        '    native_gx_work::Add(native_gx_work::DirectFifoDrains);\n'
        '    native_gx_work::Add(native_gx_work::DirectFifoRecords,frame.entries.size());\n'
        '    native_gx_work::Add(native_gx_work::DirectFifoInlineRecords,frame.entries.size()-frame.heavy.size());\n'
        '    native_gx_work::Add(native_gx_work::DirectFifoHeavyRecords,frame.heavy.size());',
        label='gx-frame')
    text = rep(text,
        '''        for (const auto &c : frame)
            if (c.type == nwii::runtime::gx::GXCommandType::DrawPrimitive) {
                draws++;
                verts += c.raw ? c.raw->count : c.vertices.size();
            }''',
        '''        for (const auto &e : frame.entries)
            if (e.kind == (uint8_t)nwii::runtime::gx::GXCommandType::DrawPrimitive) {
                const auto &c = frame.heavy[e.heavy];
                draws++;
                verts += c.raw ? c.raw->count : c.vertices.size();
            }''',
        label='gx-framesig1')
    text = rep(text,
        '''        for (const auto &c : frame) {
            if (c.type == nwii::runtime::gx::GXCommandType::DrawPrimitive && c.raw)
                for (size_t i = 0; i < c.raw->bytes.size(); i += 4)
                    drawsig = drawsig * 33 + c.raw->bytes[i];
            if (c.type == nwii::runtime::gx::GXCommandType::XFRegister)
                for (float f : c.payload) {
                    uint32_t u; std::memcpy(&u, &f, 4);
                    xfsig = xfsig * 33 + u;
                }
        }''',
        '''        for (const auto &e : frame.entries) {
            if (e.kind == (uint8_t)nwii::runtime::gx::GXCommandType::DrawPrimitive &&
                e.heavy != nwii::runtime::gx::StreamEntry::kNoHeavy && frame.heavy[e.heavy].raw) {
                const auto &c = frame.heavy[e.heavy];
                for (size_t i = 0; i < c.raw->bytes.size(); i += 4)
                    drawsig = drawsig * 33 + c.raw->bytes[i];
            }
            if (e.kind == (uint8_t)nwii::runtime::gx::GXCommandType::XFRegister &&
                e.heavy != nwii::runtime::gx::StreamEntry::kNoHeavy)
                for (float f : frame.heavy[e.heavy].payload) {
                    uint32_t u; std::memcpy(&u, &f, 4);
                    xfsig = xfsig * 33 + u;
                }
        }''',
        label='gx-framesig2')
    text = rep(text,
        '    extern void NativePreparePackedLayoutsBridge(std::vector<nwii::runtime::gx::GXCommand>&);',
        '    extern void NativePreparePackedLayoutsBridge(nwii::runtime::gx::CommandStream&);',
        label='gx-layout-decl')
    text = rep(text,
        '        for(auto& c:frame)if(c.type==GXCommandType::DrawPrimitive){',
        '        for(auto& e:frame.entries)if(e.kind==(uint8_t)GXCommandType::DrawPrimitive){GXCommand& c=frame.heavy[e.heavy];',
        label='gx-decode')
    text = rep(text,
        '''        for(const auto &c:frame)if(c.type==nwii::runtime::gx::GXCommandType::BPRegister && !c.native_signalled) {
            if(c.reg==0x45 && (c.val&0xffu)==2u)nwii::runtime::hw::pe_signal_finish();
            else if(c.reg==0x47 || c.reg==0x48)nwii::runtime::hw::pe_signal_token(c.val&0xffffu,c.reg==0x48);
        }''',
        '''        for(const auto &e:frame.entries)if(e.kind==(uint8_t)nwii::runtime::gx::GXCommandType::BPRegister && !(e.flags&nwii::runtime::gx::kStreamSignalled)) {
            if(e.reg==0x45 && (e.val&0xffu)==2u)nwii::runtime::hw::pe_signal_finish();
            else if(e.reg==0x47 || e.reg==0x48)nwii::runtime::hw::pe_signal_token(e.val&0xffffu,e.reg==0x48);
        }''',
        label='gx-headless')
    return text


def apply_fifo_header(text: str) -> str:
    if 'command_stream.h' in text:
        return text
    text = rep(text,
        '''#include <vector>
#include <cstdint>
#include "runtime/gx_command.h"
#include "runtime/gx_state.h"

namespace nwii::runtime::gx {

void ApplyBPRegister(uint8_t reg, uint32_t val);''',
        '''#include <vector>
#include <cstdint>
#include "runtime/gx_command.h"
#include "runtime/gx/command_stream.h"
#include "runtime/gx_state.h"

namespace nwii::runtime::gx {

void ApplyBPRegister(uint8_t reg, uint32_t val);

// NATIVE_DIRECT_FIFO_STREAM_V1: return heavy/payload storage to the pools and
// clear the ordered entry array.
void NativeRecycleStream(CommandStream& stream);''',
        label='fifo-h-decl')
    text = rep(text,
        '    static void Parse(std::vector<uint8_t>& fifo, std::vector<GXCommand>& commands);',
        '    static void Parse(std::vector<uint8_t>& fifo, CommandStream& commands);',
        label='fifo-h-parse')
    return text


def apply_renderer_header(text: str) -> str:
    if 'command_stream.h' in text:
        return text
    text = rep(text,
        '''#include <memory>
#include "runtime/gx_command.h"

namespace nwii::runtime::gx {

class IRenderer {
public:
    virtual ~IRenderer() = default;
    virtual void Initialize(void* window) = 0;
    virtual void Render(const std::vector<GXCommand>& commands) = 0;''',
        '''#include <memory>
#include "runtime/gx_command.h"
#include "runtime/gx/command_stream.h"

namespace nwii::runtime::gx {

class IRenderer {
public:
    virtual ~IRenderer() = default;
    virtual void Initialize(void* window) = 0;
    virtual void Render(const CommandStream& commands) = 0;''',
        label='renderer-h')
    return text


def apply_metal(text: str) -> str:
    return rep(text,
        'void Render(const std::vector<GXCommand>& commands) override {',
        'void Render(const CommandStream& commands) override {',
        label='metal')





RENDER_LOOP_NEW = r'''    for (const auto &e : commands.entries) {
      const uint8_t native_kind = e.kind;

      // NATIVE_CP_BATCH_BOUNDARY_V1: ignored CP commands need no GL flush.
      if (native_kind != (uint8_t)GXCommandType::DrawPrimitive && native_kind != (uint8_t)GXCommandType::CPRegister &&
          !NativeRedundantBp(native_kind,(uint8_t)e.reg,e.val) && s_batch_open &&
          !(native_kind == (uint8_t)GXCommandType::XFRegister && NativeRedundantXf(commands.heavy[e.heavy]))) {
/* NATIVE_FLUSH_REASON_CENSUS_V1 ICAgICAgICBGbHVzaEJhdGNoKCk7CiAgICAgICAgc19iYXRjaF9vcGVuID0gZmFsc2U7 */
        FlushBatch(native_kind==(uint8_t)GXCommandType::BPRegister?native_flush_census::Bp:
                   native_kind==(uint8_t)GXCommandType::XFRegister?native_flush_census::Xf:native_flush_census::Other);
        s_batch_open = false;
/* END_NATIVE_FLUSH_REASON_CENSUS_V1 */
      }
      if (native_kind == (uint8_t)GXCommandType::XFRegister) {
        const GXCommand &cmd = commands.heavy[e.heavy];
        int addr = cmd.reg;
/* NATIVE_STATE_EPOCHS_V1 ICAgICAgICArK3NfbmF0aXZlX2dwdV94Zl9nZW5lcmF0aW9uOyAvLyBBbnkgWEYgY29tbWFuZCBpbnZhbGlkYXRlcyBhbGwgZGVwZW5kZW50IHdvcmRzLg== */
        if(NativeXfBlockChanged(cmd))++s_native_gpu_xf_generation; // Exact block-input change only.
/* END_NATIVE_STATE_EPOCHS_V1 */
        bool touched_proj = false;
        for (size_t i = 0; i < cmd.payload.size(); ++i) {
/* NATIVE_STATE_EPOCHS_V1 ICAgICAgICAgIGludCBjdXJyZW50X2FkZHIgPSBhZGRyICsgKGludClpOw== */
          int current_addr = addr + (int)i;
          if(native_state_epochs::ShaderXf(unsigned(current_addr)) &&
             std::memcmp(&g_state.xf[current_addr-0x1000],&cmd.payload[i],4))native_state_epochs::Invalidate();
/* END_NATIVE_STATE_EPOCHS_V1 */
          if (current_addr >= 0x1000 && current_addr < 0x1000 + 256) {
            std::memcpy(&g_state.xf[current_addr - 0x1000], &cmd.payload[i], 4);
          }
          if (current_addr >= 0x0000 && current_addr <= 0x00FF) {
            g_state.posMatrices[current_addr] = cmd.payload[i];
          } else if (current_addr >= 0x0400 && current_addr <= 0x045F) {
            g_state.normalMatrices[current_addr - 0x0400] = cmd.payload[i];
          } else if (current_addr >= 0x0500 && current_addr < 0x0600) {
            g_state.postMatrices[current_addr-0x0500]=cmd.payload[i];
          } else if (current_addr >= 0x0600 && current_addr <= 0x067F) {
            int li = (current_addr - 0x0600) / 16;
            g_state.lights[li][(current_addr - 0x0600) % 16] = cmd.payload[i];
          } else if (current_addr >= 0x1020 && current_addr <= 0x1026) {
            g_state.projection[current_addr - 0x1020] = cmd.payload[i];
            if (current_addr == 0x1026) {
              std::memcpy(&g_state.projType, &cmd.payload[i], 4);
            }
            touched_proj = true;
          }
        }
        if (touched_proj) {
          g_state.projSet = true;
          ApplyProjection(s_active_shader.uProjMtx);
        }
      } else if (native_kind == (uint8_t)GXCommandType::BPRegister) {
        const uint8_t native_reg = (uint8_t)e.reg;
        const uint32_t bp_value = NativeBpRegisterValue(native_reg, e.val);
        if (native_reg == 0x52) NativeEfbProbe("pre_copy");
        const GXCommand* native_tlut_cmd = commands.Heavy(e);
        if ((e.flags & kStreamTlutSnapshot) && native_reg == 0x65) { // NATIVE_EARLY_TLUT_SNAPSHOT_V1: bytes read before the early signal
          const uint32_t dst = (bp_value & 0x3FF) << 9;
          if (native_tlut_cmd && !native_tlut_cmd->native_tlut.empty()) std::memcpy(&g_state.tlutMem[dst], native_tlut_cmd->native_tlut.data(), native_tlut_cmd->native_tlut.size());
        } else ApplyBPRegister(native_reg, bp_value);
        // NATIVE_GPU_COMPLETION_HOOK: prior batch flushed at stream boundary.
        if(!(e.flags & kStreamSignalled))NativeGpuCompletion(native_reg,bp_value,true); // NATIVE_EARLY_PE_SIGNAL_V1: already delivered after decode

        NativeTevRegisterWrite(native_reg, bp_value);
        if (native_reg == 0x40) {
          ApplyZMode();
        }
        // EFB copy WITHOUT bit14 = copy to a texture in guest RAM. Grab the
        // just-rendered pixels into a GL texture keyed by the destination
        // address; texture lookups on that address sample it directly
        // (render-to-texture: title backgrounds, portraits, effects).
        if (native_reg == 0x52 && !(bp_value & 0x4000)) {
          uint32_t dest = g_state.efbCopyDest;
          uint32_t w = g_state.efbW, h = g_state.efbH;
          if (dest && w > 0 && h > 0 && w <= 1024 && h <= 1024) {
            GLuint tex =
                nwii::runtime::hle::TextureCache::get().find_efb_texture(dest);
            if (!tex)
              NativeTextureGenerate(1, &tex);
            NativeCopyEfbTexture(tex,g_state.efbSrcX,g_state.efbSrcY,w,h,NativeResolutionScale());
            nwii::runtime::hle::g_native_efb_copy_value = bp_value; nwii::runtime::hle::g_native_efb_copy_stride = g_state.efbCopyStride; // NATIVE_EFB_COPY_GUARD_V1
            nwii::runtime::hle::TextureCache::get().register_efb_texture(dest,
                                                                         tex);

          }
        }
        // NATIVE_XFB_COPY_ORDER: the preceding batch was flushed above.
        if (native_reg==0x52) {
          if(bp_value&0x4000)
            NativeLatchXfb(g_state.efbCopyDest,g_state.efbSrcX,g_state.efbSrcY,g_state.efbW,g_state.efbH,NativeResolutionScale());
          if(bp_value&0x800) {
            unsigned char cc[4];::GX_GetClearColor(cc);
            glClearColor(cc[0]/255.f,cc[1]/255.f,cc[2]/255.f,cc[3]/255.f);
            // Copy clears affect the copy rectangle, not the current draw scissor.
            GLint box[4];glGetIntegerv(GL_SCISSOR_BOX,box);
            const GLboolean scissor=glIsEnabled(GL_SCISSOR_TEST);
            NativeRasterEnable(GL_SCISSOR_TEST);
            glScissor(g_state.efbSrcX*NativeResolutionScale(),(kNativeEfbHeight-g_state.efbSrcY-g_state.efbH)*NativeResolutionScale(),g_state.efbW*NativeResolutionScale(),g_state.efbH*NativeResolutionScale());
            NativeEfbProbe("pre_clear");NativeClearEfb();
            glScissor(box[0],box[1],box[2],box[3]);
            if(!scissor)NativeRasterDisable(GL_SCISSOR_TEST);
          }
          g_state.pe_clear_pending=false;
        }
      } else if (native_kind == (uint8_t)GXCommandType::DrawPrimitive) {
        const GXCommand &cmd = commands.heavy[e.heavy];

        if (!s_batch_open && g_ctx_ptr) {
          // NATIVE_USED_TEXTURE_BINDING_V1: validate only the shader's source textures.
          static const bool used_only=std::getenv("NWII_ALL_TEXTURE_SLOTS")==nullptr;
          const unsigned used=used_only?NativeUsedTextureMask(g_state):0xffu;
          for (int i = 0; i < 8; ++i) {
            if(!(used&(1u<<i)))continue;
            const auto &stage = g_state.texStages[i];
            if (stage.base_addr != 0 && stage.width > 0 && stage.height > 0) {
              // NATIVE_TEXTURE_UNIT_V1: cache misses upload on the active unit.
              NativeTextureActive(GL_TEXTURE0 + i);
              GLuint tex = nwii::runtime::hle::TextureCache::get().get_texture(
                  *g_ctx_ptr, stage);
              NativeTextureBind(GL_TEXTURE_2D, tex);
              NativeApplyTextureSampler(i,g_state.bp[(i<4?0x80:0xa0)+(i%4)],g_state.bp[(i<4?0x84:0xa4)+(i%4)]); // NATIVE_MIPMAPS_V1
              { const unsigned mip_filter=(g_state.bp[(i<4?0x80:0xa0)+(i%4)]>>5)&7u; // NATIVE_MIPMAPS_V1: lazy mip chain
                if((mip_filter==1||mip_filter==2||mip_filter==5||mip_filter==6) && ((g_state.bp[(i<4?0x84:0xa4)+(i%4)]>>8)&0xffu)!=0 &&
                   tex!=nwii::runtime::hle::TextureCache::get().find_efb_texture(stage.base_addr))
                  nwii::runtime::hle::TextureCache::get().ensure_mips(tex); }
            }
          }
        }
        DrawPrimitive(cmd);
      }
    }
'''

REDUNDANT_BP_NEW = r'''static bool NativeRedundantBp(uint8_t kind,uint8_t reg,uint32_t val){
 if(kind!=uint8_t(GXCommandType::BPRegister))return false;
 const bool ordinary=reg==0 || (reg>=0x20 && reg<=0x2f) || (reg>=0x40 && reg<=0x43) ||
     (reg>=0xc0 && reg<=0xe7) || (reg>=0xe9 && reg<=0xfd);
 if(!ordinary)return false;
 const uint32_t resolved=((s_native_bp_shadow[reg]&~s_native_bp_mask)|(val&s_native_bp_mask))&0xffffff;
 return resolved==s_native_bp_shadow[reg];
}
static bool NativeRedundantBp(const GXCommand& cmd){
 return NativeRedundantBp(uint8_t(cmd.type),uint8_t(cmd.reg),cmd.val);
}'''

LAYOUT_NEW = r'''void NativePreparePackedLayouts(CommandStream& commands){
 uint32_t bp[256];std::memcpy(bp,s_native_bp_shadow,sizeof(bp));uint32_t mask=s_native_bp_mask;
 uint32_t info[8];std::memcpy(info,g_state.xf+0x40,sizeof(info));
 for(auto& e:commands.entries){
  if(e.kind==(uint8_t)GXCommandType::BPRegister){const unsigned reg=e.reg;const uint32_t resolved=((bp[reg]&~mask)|(e.val&mask))&0xffffff;
   bp[reg]=resolved;mask=reg==0xfe?resolved:0xffffff;
  }else if(e.kind==(uint8_t)GXCommandType::XFRegister){const GXCommand& c=commands.heavy[e.heavy];for(size_t i=0;i<c.payload.size();++i){unsigned reg=c.reg+unsigned(i);if(reg>=0x1040 && reg<0x1048)std::memcpy(info+reg-0x1040,&c.payload[i],4);}}
  else if(e.kind==(uint8_t)GXCommandType::DrawPrimitive){GXCommand& c=commands.heavy[e.heavy];unsigned active=std::min(8u,std::max(1u,bp[0]&15u)),required=active;
   for(unsigned t=0;t<active;++t){unsigned type=(info[t]>>4)&7u,source=(info[t]>>7)&31u;if(type!=2 && type!=3 && source>=5 && source<=12)required=std::max(required,source-4u);}
   c.native_packed_inputs=uint8_t(required);
  }
 }
}'''


def apply_renderer(text: str) -> str:
    if MARK in text:
        return text
    # 1. Render signature
    text = rep(text,
        '  void Render(const std::vector<GXCommand> &commands) override {',
        '  void Render(const CommandStream &commands) override {',
        label='render-sig')
    # 2. Render loop body -> entries
    text = splice(text, '    for (const auto &cmd : commands) {\n',
                  '/* NATIVE_FLUSH_REASON_CENSUS_V1 ICAgIGlmIChzX2JhdGNoX29wZW4pIHsKICAgICAgRmx1c2hCYXRjaCgpOw== */',
                  RENDER_LOOP_NEW, label='render-loop')
    # 3. NativeRedundantBp overloads
    text = rep(text,
        '''static bool NativeRedundantBp(const GXCommand& cmd){
 if(cmd.type!=GXCommandType::BPRegister)return false;
 const unsigned reg=cmd.reg;
 const bool ordinary=reg==0 || (reg>=0x20 && reg<=0x2f) || (reg>=0x40 && reg<=0x43) ||
     (reg>=0xc0 && reg<=0xe7) || (reg>=0xe9 && reg<=0xfd);
 if(!ordinary)return false;
 const uint32_t resolved=((s_native_bp_shadow[reg]&~s_native_bp_mask)|(cmd.val&s_native_bp_mask))&0xffffff;
 return resolved==s_native_bp_shadow[reg];
}''',
        REDUNDANT_BP_NEW, label='redundant-bp')
    # 4. NativePreparePackedLayouts
    text = splice(text, 'void NativePreparePackedLayouts(std::vector<GXCommand>& commands){\n',
                  '/* NATIVE_STATE_EPOCHS_V1 c3RhdGljIHZvaWQgTmF0aXZlUmVzdG9yZUJwU25hcHNob3QoKSB7 */',
                  LAYOUT_NEW + '\n', label='layout')
    # 5. bridge
    text = rep(text,
        'void NativePreparePackedLayoutsBridge(std::vector<nwii::runtime::gx::GXCommand>& commands){nwii::runtime::gx::NativePreparePackedLayouts(commands);}',
        'void NativePreparePackedLayoutsBridge(nwii::runtime::gx::CommandStream& commands){nwii::runtime::gx::NativePreparePackedLayouts(commands);}',
        label='layout-bridge')
    return text


FILES = {
    PARSER_REL: apply_parser,
    RENDER_REL: apply_renderer,
    'src/hle/gx.cpp': apply_gx,
    'include/runtime/gx/fifo_parser.h': apply_fifo_header,
    'include/runtime/gx/renderer.h': apply_renderer_header,
    'src/hle/gx/renderer_metal.mm': apply_metal,
}


def capture_file(original, patched):
    nl = b'\r\n' if b'\r\n' in patched else b'\n'
    return (b'/* '+RESTORE_MARK.encode()+b' '+hashlib.sha256(patched).hexdigest().encode()+
            b' '+base64.b64encode(original)+b' */'+nl+patched+nl+b'/* END_'+RESTORE_MARK.encode()+b' */'+nl)


def restore_file(raw, allow_stale=False):
    """Return the pre-stage bytes embedded in `raw`'s snapshot (or `raw` when unstaged).

    `allow_stale` is for the pipeline's restore pass: a stage that runs after direct FIFO
    (gx.cpp is rewritten by the gather-guard/hot-diag/gather-cache/poll-reload stages, none of
    which is restored before this one) edits the staged body, so the recorded hash no longer
    matches. The pass re-applies every later stage immediately after this restore, so the
    embedded original is still the right base; refusing to restore would leave gx.cpp in its
    final form while the stages that ran *before* direct FIFO re-run against text they never
    saw (tools/fix_command_reuse.py, tools/fix_early_gpu_signal.py). The hash stays a
    diagnostic: a stale one is reported.
    """
    match=SNAPSHOT.fullmatch(raw)
    if match:
        if hashlib.sha256(match[3]).hexdigest().encode()!=match[1]:
            if not allow_stale:
                raise AnchorError('Direct FIFO source changed after staging; preserve it before restoring')
            print('Direct FIFO restore: snapshot hash is stale (a later stage edited the staged body); '
                  'restoring the embedded original so the pass re-applies every later stage.')
        return base64.b64decode(match[2],validate=True)
    if RESTORE_MARK.encode() not in raw:
        return raw
    # A later stage (fix_xf_stream_reuse.py prepends its include) can put text before the snapshot
    # header, so accept the wrapper anywhere in the file; the hash still pins the patched body.
    m=re.search(rb'/\* '+RESTORE_MARK.encode()+rb' ([0-9a-f]{64}) ([A-Za-z0-9+/=]+) \*/\r?\n(.*?)\r?\n/\* END_'
                +RESTORE_MARK.encode()+rb' \*/\r?\n',raw,re.S)
    if not m:
        raise AnchorError('Incomplete direct FIFO restore snapshot')
    if hashlib.sha256(m[3]).hexdigest().encode()!=m[1]:
        if not allow_stale:
            raise AnchorError('Direct FIFO source changed after staging; preserve it before restoring')
        print('Direct FIFO restore: snapshot hash is stale (a later stage edited the staged body); '
              'restoring the embedded original so the pass re-applies every later stage.')
    return raw[:m.start()]+base64.b64decode(m[2],validate=True)+raw[m.end():]


def transformed_file(raw, fn, name=''):
    try:
        original=restore_file(raw)
    except AnchorError as exc:
        # A stale snapshot hash is tolerated here (the restore pass now forces the restore, so
        # this only happens when the stage is run on its own): a later stage rewrote the staged
        # body, so the anchors below are already in their staged form and there is nothing to
        # re-derive. Any other failure (a malformed snapshot) is real.
        if 'changed after staging' not in str(exc):
            raise
        print('Direct FIFO apply: %s carries a stale snapshot; already staged, leaving it alone.' % name)
        return raw
    text=original.decode('utf-8').replace('\r\n','\n')
    if MARK in text:raise AnchorError('Unwrapped direct FIFO prototype; restore it with its original stage first')
    patched=fn(text)
    if patched==text:raise AnchorError('Direct FIFO transformation did not change its target')
    if b'\r\n' in original:patched=patched.replace('\n','\r\n')
    canonical=capture_file(original,patched.encode('utf-8'))
    if original!=raw and canonical!=raw and canonical.replace(b'\r\n',b'\n')!=raw.replace(b'\r\n',b'\n'):
        raise AnchorError('Noncanonical direct FIFO stage')
    return canonical


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port', type=Path, default=HERE.parent, help='tree root containing build/nwii-full-source/nWiiRuntime')
    ap.add_argument('--runtime', type=Path, help='the WiiRuntime directory directly')
    ap.add_argument('--restore', action='store_true')
    a = ap.parse_args()
    if a.runtime:
        rt = a.runtime
    elif a.port:
        rt = a.port / 'build/nwii-full-source/nWiiRuntime'
    header = rt / 'include/runtime/gx/command_stream.h'
    paths=[(rt/rel,fn)for rel,fn in FILES.items()]
    if any(not p.is_file()for p,_ in paths):raise AnchorError('A direct FIFO target is missing')
    # Validate every file before writing any, including canonical metadata on a
    # repeated apply. Unstaged restore is a strict no-op on existing sources.
    changes=[]
    if a.restore:
        for p,_ in paths:
            raw=p.read_bytes()
            try:
                new=restore_file(raw,allow_stale=True)
            except AnchorError:
                # A later stage changed this file after direct FIFO was staged; leave it
                # alone (the apply below re-derives it) instead of failing a fresh build.
                print('Direct FIFO restore: %s changed after staging; leaving it for re-apply.'%p.name)
                new=raw
            changes.append((p,raw,new))
        staged=any(raw!=new for _,raw,new in changes)
        if staged and header.exists() and header.read_bytes()!=(FILES_DIR/'command_stream.h').read_bytes():
            raise AnchorError('Direct FIFO header changed after staging')
        for p,raw,new in changes:
            if raw!=new:p.write_bytes(new)
        if staged and header.exists():header.unlink()
        print('Direct FIFO restored.' if staged else 'Direct FIFO restore: unstaged, no changes.')
        return 0
    for p,fn in paths:
        raw=p.read_bytes();changes.append((p,raw,transformed_file(raw,fn,p.name)))
    expected_header=(FILES_DIR/'command_stream.h').read_bytes()
    if header.exists() and header.read_bytes()!=expected_header:raise AnchorError('Unexpected command_stream.h owner')
    header.parent.mkdir(parents=True,exist_ok=True)
    if not header.exists():header.write_bytes(expected_header)
    for p,raw,new in changes:
        if raw!=new:p.write_bytes(new)
    print('Direct FIFO staged; compact emission requires NWII_DIRECT_FIFO=1.')


if __name__ == '__main__':
    main()
