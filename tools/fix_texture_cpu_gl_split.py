"""Texture cache split into a CPU stage and a GL stage (NATIVE_TEXTURE_CPU_GL_SPLIT_V1).

Stage (a), TextureCache::prepare_texture, runs on the drain thread inside ProcessGXFifo, after Parse and the decode phase
and before NativeSignalGpuCompletionEarly. It does the lookup, the validation (unchanged(), the write tracker, the memo,
the TLUT epochs), the EFB guard and the decode on a miss, and it decides every cache change. Guest RAM is read only there.
It makes no GL call: each upload, bind-name allocation and delete is queued as a TextureOp.

Stage (b), TextureCache::resolve_texture, runs in RendererGL::Render at each draw. It runs the queued ops in order and
returns the GL name to bind. Uploads restore the binding they disturb.

The draw-time reference list comes from NativePrepareTextures (runtime-overlays/texture_cpu_gl_split_walk.inc). It replays
the drain's BP writes in command order on a mirror of RenderState(), so each draw sees the same texture state Render would.

Edits are anchored and count-checked, like the other stages. Replaced regions keep their original text inside an
`#if 0` block, so --restore is exact. Runs after the renderer state split (it uses RenderState()). Only applied when
NWII_BUILD_RENDER_STATE_SPLIT=1; the restore runs unconditionally at the start of every build.
Harness: tools/check_texture_cpu_gl_split.py.
"""
import re
import sys
from pathlib import Path

p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime'
overlays = p / 'runtime-overlays'
MARK = 'NATIVE_TEXTURE_CPU_GL_SPLIT_V1'
RESTORE = '--restore' in sys.argv
TEX = rt / 'src/hle/texture_cache_gl.cpp'
TEX_H = rt / 'include/runtime/texture_cache_gl.h'
REN = rt / 'src/hle/gx/renderer_gl.cpp'
GX = rt / 'src/hle/gx.cpp'


def read(path):
    with open(path, 'r', encoding='utf-8', errors='surrogateescape', newline='') as f:
        return f.read()


def write(path, text):
    with open(path, 'w', encoding='utf-8', errors='surrogateescape', newline='') as f:
        f.write(text)


def pattern(s):
    # Anchors are written with LF; a file may be CRLF, CR or mixed, so every newline in an anchor matches either.
    return re.compile(re.escape(s).replace('\\\n', '\\r?\\n'))


def find_one(text, s, label):
    hits = list(pattern(s).finditer(text))
    assert len(hits) == 1, (label, len(hits), s[:80])
    return hits[0]


def newlines_of(text):
    return '\r\n' if '\r\n' in text else '\n'


def swap(path, old, new, label):
    """Replace the single occurrence of old with new. Restore replaces new with old."""
    text = read(path)
    if RESTORE:
        if len(list(pattern(new).finditer(text))) == 1:
            m = find_one(text, new, label + ':restore')
            nl = newlines_of(m.group(0))
            write(path, text[:m.start()] + old.replace('\n', nl) + text[m.end():])
        return
    if len(list(pattern(new).finditer(text))) == 1:
        return  # already applied: new may itself contain old, so test new first
    m = find_one(text, old, label)
    nl = newlines_of(m.group(0))
    write(path, text[:m.start()] + new.replace('\n', nl) + text[m.end():])


def region(path, start, end, new, name):
    """Replace the text from start through the first end after it. The original text is kept in an #if 0 block."""
    text = read(path)
    begin = f'// BEGIN {MARK} {name}'
    if RESTORE:
        block = re.compile(
            re.escape(f'// BEGIN {MARK} {name}') + r'\r?\n.*?' + re.escape(f'// END {MARK} {name}') + r'\r?\n'
            + re.escape(f'#if 0 // {MARK}:original {name}') + r'\r?\n(.*?)\r?\n'
            + re.escape(f'#endif // {MARK}:original {name}') + r'\r?\n', re.S)
        m = block.search(text)
        if m:
            write(path, text[:m.start()] + m.group(1) + text[m.end():])
        return
    if begin in text:
        return  # already applied
    s = find_one(text, start, name + ':start')
    e_hit = pattern(end).search(text, s.start())
    assert e_hit is not None, (name, 'end anchor not found', end[:60])
    nl = newlines_of(text)
    original = text[s.start():e_hit.end()]
    # Only the markers and the new text take the file's newline; the original text is kept byte for byte.
    wrapper_head = f'{begin}\n{new}\n// END {MARK} {name}\n#if 0 // {MARK}:original {name}\n'.replace('\n', nl)
    wrapper_tail = f'\n#endif // {MARK}:original {name}\n'.replace('\n', nl)
    write(path, text[:s.start()] + wrapper_head + original + wrapper_tail + text[e_hit.end():])


def insert_block(path, anchor, block, name):
    """Insert block (between BEGIN/END markers) before anchor. Restore deletes the marked block entirely."""
    text = read(path)
    begin = f'// BEGIN {MARK} {name}'
    if RESTORE:
        m = re.compile(re.escape(begin) + r'\r?\n.*?' + re.escape(f'// END {MARK} {name}') + r'\r?\n', re.S).search(text)
        if m:
            write(path, text[:m.start()] + text[m.end():])
        return
    if begin in text:
        return
    a = find_one(text, anchor, name + ':anchor')
    nl = newlines_of(text)
    marked = f'{begin}\n{block}\n// END {MARK} {name}\n'.replace('\n', nl)
    write(path, text[:a.start()] + marked + text[a.start():])


WALK = (overlays / 'texture_cpu_gl_split_walk.inc').read_text(encoding='utf-8')

# ---- texture_cache_gl.h ------------------------------------------------------------------------------------------
swap(TEX_H, '    GLuint get_texture(CPUContext& ctx, const gx::TexStage& stage);\n', '''    // NATIVE_TEXTURE_CPU_GL_SPLIT_V1: prepare_texture is stage (a), CPU only: lookup, validation and decode, with every cache
    // change decided there. resolve_texture is stage (b): it runs the GL work queued for that reference, in order, and
    // returns the GL name to bind. Guest RAM is read only by prepare_texture.
    struct TextureRef {
        enum Kind : uint8_t { Slot, Efb, Direct } kind = Slot;
        int slot = -1;
        uint32_t efb_addr = 0;
        GLuint direct = 0;
        size_t ops_end = 0; // ops queued before this reference; resolve_texture runs up to here
    };
    TextureRef prepare_texture(CPUContext& ctx, const gx::TexStage& stage, const uint8_t* tlut);
    GLuint resolve_texture(const TextureRef& ref);
    void flush_ops();
    bool efb_known(uint32_t addr) const;
''', 'header:get_texture')
swap(TEX_H, '    void register_efb_texture(uint32_t addr, GLuint tex);\n', '''    void note_efb_copy(uint32_t addr, uint32_t copy_value, uint32_t stride, uint32_t width, uint32_t height); // CPU half, at prepare time
    void bind_efb_texture(uint32_t addr, GLuint tex); // GL half, when the copy executes in Render
''', 'header:register')
swap(TEX_H, 'struct Entry { GLuint tex;', 'struct Entry { int slot;', 'header:entry')
swap(TEX_H, '    Image decode_texture(CPUContext& ctx, const gx::TexStage& stage);\n',
     '    Image decode_texture(CPUContext& ctx, const gx::TexStage& stage, const uint8_t* tlut);\n', 'header:decode')
swap(TEX_H, '    void decode_paletted(CPUContext& ctx, const gx::TexStage& stage, Color* out);\n',
     '    void decode_paletted(CPUContext& ctx, const gx::TexStage& stage, const uint8_t* tlut, Color* out);\n', 'header:paletted')
swap(TEX_H, '    std::unordered_map<uint32_t, GLuint> efb_textures;\n', '''    std::unordered_map<uint32_t, GLuint> efb_textures;
    // NATIVE_TEXTURE_CPU_GL_SPLIT_V1: CPU bookkeeping, and the queue of GL work for the references it hands out.
    struct TextureOp {
        bool upload = false; // false: delete the slot's GL name
        bool reuse = false, dxt = false;
        int slot = -1;
        uint32_t width = 0, height = 0;
        Image img{};
        std::vector<uint8_t> dxt_bytes;
    };
    std::unordered_set<uint32_t> efb_addrs; // EFB copy destinations, as of prepare time
    std::vector<GLuint> slot_names;         // GL name per slot; written only by stage (b)
    std::vector<int> free_slots;
    int slot_count = 0;
    std::vector<TextureOp> ops;
    size_t ops_next = 0;
    int alloc_slot();
    void release_slot(int slot);
    void run_ops(size_t end);
    void execute(TextureOp& op);
    TextureRef slot_reference(int slot) { TextureRef r; r.kind = TextureRef::Slot; r.slot = slot; r.ops_end = ops.size(); return r; }
    TextureRef efb_reference(uint32_t addr) { TextureRef r; r.kind = TextureRef::Efb; r.efb_addr = addr; r.ops_end = ops.size(); return r; }
    TextureRef direct_reference(GLuint tex) { TextureRef r; r.kind = TextureRef::Direct; r.direct = tex; r.ops_end = ops.size(); return r; }
''', 'header:members')

# ---- texture_cache_gl.cpp ---------------------------------------------------------------------------------------
PLUMBING = '''// NATIVE_TEXTURE_CPU_GL_SPLIT_V1: stage (b) plumbing. prepare_texture queues ops; resolve_texture runs them in order.
int TextureCache::alloc_slot() {
    if (!free_slots.empty()) {
        const int slot = free_slots.back();
        free_slots.pop_back();
        return slot;
    }
    return slot_count++;
}

void TextureCache::release_slot(int slot) {
    TextureOp op; // upload == false: the GL name of this slot is deleted when the op runs
    op.slot = slot;
    ops.push_back(std::move(op));
    free_slots.push_back(slot); // safe: a later upload into this slot is queued after the delete
}

bool TextureCache::efb_known(uint32_t addr) const { return efb_addrs.count(addr & 0x01FFFFFF) != 0; }

void TextureCache::run_ops(size_t end) {
    while (ops_next < end && ops_next < ops.size()) {
        execute(ops[ops_next]);
        ++ops_next;
    }
}

void TextureCache::flush_ops() {
    run_ops(ops.size());
    ops.clear();
    ops_next = 0;
}

GLuint TextureCache::resolve_texture(const TextureRef& ref) {
    run_ops(ref.ops_end);
    switch (ref.kind) {
        case TextureRef::Efb: return find_efb_texture(ref.efb_addr);
        case TextureRef::Direct: return ref.direct;
        default: return ref.slot >= 0 && size_t(ref.slot) < slot_names.size() ? slot_names[ref.slot] : 0;
    }
}

void TextureCache::execute(TextureOp& op) {
    if (!op.upload) {
        if (op.slot >= 0 && size_t(op.slot) < slot_names.size() && slot_names[op.slot]) {
            GLuint tex = slot_names[op.slot];
            mip_ready.erase(tex);
            NativeTextureDelete(1, &tex);
            slot_names[op.slot] = 0;
        }
        return;
    }
    if (size_t(op.slot) >= slot_names.size()) slot_names.resize(op.slot + 1, 0);
    native_trace::Scope trace_texture_upload("texture.upload");
    GLuint tex;
    if (op.reuse) { tex = slot_names[op.slot]; mip_ready.erase(tex); }
    else { NativeTextureGenerate(1, &tex); slot_names[op.slot] = tex; }
    GLint previous = 0; // the upload binds on the active unit; put back whatever the draw state had there
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous);
    NativeTextureBind(GL_TEXTURE_2D, tex);
    { native_trace::GPUScope budget_operation("gpu.texture_transfer_us", 1);
      if (op.dxt) glCompressedTexImage2D(GL_TEXTURE_2D, 0, 0x83F1 /* GL_COMPRESSED_RGBA_S3TC_DXT1_EXT */, op.width, op.height, 0, GLsizei(op.dxt_bytes.size()), op.dxt_bytes.data());
      else if (op.reuse) glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, op.width, op.height, GL_RGBA, GL_UNSIGNED_BYTE, op.img.data);
      else glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, op.width, op.height, 0, GL_RGBA, GL_UNSIGNED_BYTE, op.img.data); }
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    static const bool mipmaps = std::getenv("NWII_NO_MIPMAPS") == nullptr; // NATIVE_MIPMAPS_V1
    if (mipmaps) glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, 0);  // NATIVE_MIPMAPS_V1: level 0 until ensure_mips()
    UnloadImage(op.img);
    op.img = Image{};
    NativeTextureBind(GL_TEXTURE_2D, GLuint(previous));
}

'''

swap(TEX, 'GLuint TextureCache::get_texture(CPUContext& ctx, const gx::TexStage& stage) {\n',
     PLUMBING + 'TextureCache::TextureRef TextureCache::prepare_texture(CPUContext& ctx, const gx::TexStage& stage, const uint8_t* tlut) {\n',
     'cpp:get_texture')
swap(TEX, 'GLuint reuse_tex = 0; bool reuse_valid = false;', 'int reuse_slot = -1; bool reuse_valid = false;', 'cpp:reuse')
swap(TEX, '''    if (GLuint efb = find_efb_texture(stage.base_addr)) {
        if (efb_guest_unchanged(ctx, stage.base_addr)) { native_trace::mark("texture.efb_hit"); return efb; }''',
     '''    if (efb_known(stage.base_addr)) {
        if (efb_guest_unchanged(ctx, stage.base_addr)) { native_trace::mark("texture.efb_hit"); return efb_reference(stage.base_addr); }''',
     'cpp:efb_hit')
swap(TEX, '''    if(stage.tlut_offset>=sizeof(gx::RenderState().tlutMem))palette_bytes=0;
    else palette_bytes=std::min(palette_bytes,uint32_t(sizeof(gx::RenderState().tlutMem)-stage.tlut_offset));
    const uint8_t* palette=palette_bytes?gx::RenderState().tlutMem+stage.tlut_offset:nullptr;''',
     '''    if(stage.tlut_offset>=kNativeTlutBytes)palette_bytes=0;
    else palette_bytes=std::min(palette_bytes,uint32_t(kNativeTlutBytes-stage.tlut_offset));
    const uint8_t* palette=palette_bytes?tlut+stage.tlut_offset:nullptr;''', 'cpp:palette')
swap(TEX, 'Image candidate=decode_texture(ctx,stage);', 'Image candidate=decode_texture(ctx,stage,tlut);', 'cpp:candidate')
swap(TEX, 'if(GLuint tex=prompts.texture(glyph)) return tex;', 'if(GLuint direct=prompts.texture(glyph)) return direct_reference(direct);', 'cpp:prompt')
swap(TEX, 'return entry->second.tex;}', 'return slot_reference(entry->second.slot);}', 'cpp:hot')
swap(TEX, '''        if (tl_off < sizeof(gx::RenderState().tlutMem)) {
            uint32_t avail = (uint32_t)sizeof(gx::RenderState().tlutMem) - tl_off;''',
     '''        if (tl_off < kNativeTlutBytes) {
            uint32_t avail = (uint32_t)kNativeTlutBytes - tl_off;''', 'cpp:hashtlut')
swap(TEX, 'const uint8_t* tp = gx::RenderState().tlutMem + tl_off;', 'const uint8_t* tp = tlut + tl_off;', 'cpp:hashtp')
swap(TEX, 'total_bytes-=it->second.bytes;reuse_tex=it->second.tex;reuse_valid=true;',
     'total_bytes-=it->second.bytes;reuse_slot=it->second.slot;reuse_valid=true;', 'cpp:reuse_erase')
swap(TEX, '''        it->second.stamp = tick;
        return it->second.tex;''', '''        it->second.stamp = tick;
        return slot_reference(it->second.slot);''', 'cpp:hit')
swap(TEX, 'Image img = use_dxt ? Image{} : decode_texture(ctx, stage);', 'Image img = use_dxt ? Image{} : decode_texture(ctx, stage, tlut);', 'cpp:decode_call')
region(TEX, '    GLuint tex;\n    native_trace::Scope trace_texture_upload("texture.upload");',
       '    UnloadImage(img);\n', '''    // NATIVE_TEXTURE_CPU_GL_SPLIT_V1: the upload is queued, not performed. The slot is decided here; the bytes move into the
    // op, and stage (b) uploads them when a draw resolves this reference.
    const int slot = reuse_valid ? reuse_slot : alloc_slot();
    TextureOp op;
    op.upload = true;
    op.reuse = reuse_valid;
    op.dxt = use_dxt;
    op.slot = slot;
    op.width = stage.width;
    op.height = stage.height;
    op.img = img;
    op.dxt_bytes = std::move(dxt);
    ops.push_back(std::move(op));''', 'upload')
swap(TEX, 'cache[key] = Entry{tex, entry_bytes, tick};', 'cache[key] = Entry{slot, entry_bytes, tick};', 'cpp:insert')
swap(TEX, '    return tex;\n}\n\nImage TextureCache::decode_texture(CPUContext& ctx, const gx::TexStage& stage) {',
     '    return slot_reference(slot);\n}\n\nImage TextureCache::decode_texture(CPUContext& ctx, const gx::TexStage& stage, const uint8_t* tlut) {',
     'cpp:return')
swap(TEX, 'decode_paletted(ctx, stage, pixels);', 'decode_paletted(ctx, stage, tlut, pixels);', 'cpp:paletted_call')
swap(TEX, 'void TextureCache::decode_paletted(CPUContext& ctx, const gx::TexStage& stage, Color* out) {',
     'void TextureCache::decode_paletted(CPUContext& ctx, const gx::TexStage& stage, const uint8_t* tlut, Color* out) {', 'cpp:paletted_sig')
swap(TEX, 'const size_t offset=std::min(size_t(stage.tlut_offset),sizeof(RenderState().tlutMem));',
     'const size_t offset=std::min(size_t(stage.tlut_offset),kNativeTlutBytes);', 'cpp:paletted_offset')
swap(TEX, 'RenderState().tlutMem+offset,sizeof(RenderState().tlutMem)-offset,', 'tlut+offset,kNativeTlutBytes-offset,', 'cpp:paletted_decode')
swap(TEX, 'if (off + 1 >= sizeof(RenderState().tlutMem)) return MAGENTA;', 'if (off + 1 >= kNativeTlutBytes) return MAGENTA;', 'cpp:paletted_bound')
swap(TEX, 'uint16_t entry = (RenderState().tlutMem[off] << 8) | RenderState().tlutMem[off + 1];',
     'uint16_t entry = (tlut[off] << 8) | tlut[off + 1];', 'cpp:paletted_entry')
swap(TEX, '''void TextureCache::register_efb_texture(uint32_t addr, GLuint tex) {
    efb_textures[addr & 0x01FFFFFF] = tex;
    if (g_ctx_ptr) {''', '''// NATIVE_TEXTURE_CPU_GL_SPLIT_V1: the TLUT-free RAM size of a palette, used by prepare_texture and the paletted decoder.
constexpr size_t kNativeTlutBytes = sizeof(gx::RenderState().tlutMem);
void TextureCache::note_efb_copy(uint32_t addr, uint32_t copy_value, uint32_t stride, uint32_t w, uint32_t h) {
    efb_addrs.insert(addr & 0x01FFFFFF);
    if (g_ctx_ptr) {''', 'cpp:register_head')
swap(TEX, 'NativeEfbCopyBytes(g_native_efb_copy_value, gx::RenderState().efbW, gx::RenderState().efbH, g_native_efb_copy_stride)',
     'NativeEfbCopyBytes(copy_value, w, h, stride)', 'cpp:register_bytes')
swap(TEX, 'bool TextureCache::efb_guest_unchanged(CPUContext& ctx, uint32_t addr) {',
     'void TextureCache::bind_efb_texture(uint32_t addr, GLuint tex) {\n    efb_textures[addr & 0x01FFFFFF] = tex;\n}\n\n'
     'bool TextureCache::efb_guest_unchanged(CPUContext& ctx, uint32_t addr) {', 'cpp:bind')
swap(TEX, 'mip_ready.erase(pair.second.tex); NativeTextureDelete(1, &pair.second.tex);', 'release_slot(pair.second.slot);', 'cpp:clear')
# Eviction, as left by the texture attribution stage (it runs first): the entry's GL name is deleted by its queued release op,
# in order on the GL thread, so the attribution stage's batched NativeTextureDelete is not run on the front end. One
# evict_names entry is still pushed per release, so the TexEvictDeletes count matches the attribution stage exactly.
swap(TEX, 'mip_ready.erase(it->second.tex); evict_names.push_back(it->second.tex);',
     'release_slot(it->second.slot); evict_names.push_back(0); // ' + MARK + ': the GL name goes through the release op', 'cpp:evict_attr')
swap(TEX, 'NativeTextureDelete(GLsizei(evict_names.size()), evict_names.data()); ',
     '/* ' + MARK + ': the batched delete runs from the release ops, on the GL thread */ ', 'cpp:evict_done')

# ---- gx/renderer_gl.cpp -----------------------------------------------------------------------------------------
swap(REN, '#include "runtime/gx/render_state.h" // NATIVE_RENDERER_STATE_SPLIT_V1',
     '#include "runtime/gx/render_state.h" // NATIVE_RENDERER_STATE_SPLIT_V1\n#include "runtime/config.h" // ' + MARK, 'ren:include')
insert_block(REN, 'class RendererGL : public IRenderer {', WALK, 'walk')
swap(REN, '    RenderStateBegin(RenderBatchSnapshot()); // NATIVE_RENDERER_STATE_SPLIT_V1: parse-owned view of this batch\n',
     '    RenderStateBegin(RenderBatchSnapshot()); // NATIVE_RENDERER_STATE_SPLIT_V1: parse-owned view of this batch\n'
     '    s_draw_index = 0; // ' + MARK + '\n', 'ren:render_start')
region(REN, '        if (!s_batch_open && g_ctx_ptr) {', '        DrawPrimitive(cmd);', '''        // NATIVE_TEXTURE_CPU_GL_SPLIT_V1: stage (b). Every draw resolves its texture references in order; the uploads that
        // stage (a) queued run here and restore the binding they disturb. Units are bound only where the batch opens, as before.
        static const NativeDrawTextures no_textures{};
        const NativeDrawTextures& draw_tex = s_draw_index < s_draw_textures.size() ? s_draw_textures[s_draw_index] : no_textures;
        ++s_draw_index;
        for (int i = 0; i < 8; ++i) {
          if (!(draw_tex.present & (1u << i))) continue;
          const GLuint tex = nwii::runtime::hle::TextureCache::get().resolve_texture(draw_tex.ref[i]);
          if (s_batch_open || !g_ctx_ptr) continue;
          const auto &stage = RenderState().texStages[i];
          NativeTextureActive(GL_TEXTURE0 + i);
          NativeTextureBind(GL_TEXTURE_2D, tex);
          NativeApplyTextureSampler(i,RenderState().bp[(i<4?0x80:0xa0)+(i%4)],RenderState().bp[(i<4?0x84:0xa4)+(i%4)]); // NATIVE_MIPMAPS_V1
          { const unsigned mip_filter=(RenderState().bp[(i<4?0x80:0xa0)+(i%4)]>>5)&7u; // NATIVE_MIPMAPS_V1: lazy mip chain
            if((mip_filter==1||mip_filter==2||mip_filter==5||mip_filter==6) && ((RenderState().bp[(i<4?0x84:0xa4)+(i%4)]>>8)&0xffu)!=0 &&
               tex!=nwii::runtime::hle::TextureCache::get().find_efb_texture(stage.base_addr))
              nwii::runtime::hle::TextureCache::get().ensure_mips(tex); }
        }
        DrawPrimitive(cmd);''', 'ren:draw')
swap(REN, 'nwii::runtime::hle::TextureCache::get().register_efb_texture(dest,',
     'nwii::runtime::hle::TextureCache::get().bind_efb_texture(dest,', 'ren:bind_efb')
swap(REN, 'nwii::runtime::hle::g_native_efb_copy_value = bp_value; nwii::runtime::hle::g_native_efb_copy_stride = RenderState().efbCopyStride; // NATIVE_EFB_COPY_GUARD_V1',
     '// NATIVE_EFB_COPY_GUARD_V1: the guard inputs are passed to note_efb_copy at prepare time.', 'ren:guard')
swap(REN, '  }\n\n  void Present() override {}',
     '    nwii::runtime::hle::TextureCache::get().flush_ops(); // ' + MARK + ': queued uploads and deletes are done before Render returns\n'
     '  }\n\n  void Present() override {}', 'ren:flush')
swap(REN, 'void NativePreparePackedLayoutsBridge(nwii::runtime::gx::CommandStream& commands){nwii::runtime::gx::NativePreparePackedLayouts(commands);}',
     'void NativePreparePackedLayoutsBridge(nwii::runtime::gx::CommandStream& commands){nwii::runtime::gx::NativePreparePackedLayouts(commands);}\n'
     'void NativePrepareTexturesBridge(nwii::runtime::gx::CommandStream& commands){nwii::runtime::gx::NativePrepareTextures(commands);} // ' + MARK,
     'ren:bridge')

# ---- gx.cpp: the prepass runs before the early signal, on the same thread as Parse --------------------------------
swap(GX, '    { // NATIVE_EARLY_PE_SIGNAL_V1: the guest sees every token',
     '    { // ' + MARK + ': every texture the batch samples is decided before the guest is released\n'
     '        extern void NativePrepareTexturesBridge(nwii::runtime::gx::CommandStream&);\n'
     '        if (g_window) NativePrepareTexturesBridge(frame);\n'
     '    }\n'
     '    { // NATIVE_EARLY_PE_SIGNAL_V1: the guest sees every token', 'gx:prepass')

print(('restored' if RESTORE else 'applied') + ' ' + MARK)
