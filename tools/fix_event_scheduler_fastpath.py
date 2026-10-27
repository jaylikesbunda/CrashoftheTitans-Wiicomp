"""Inline only EventScheduler's unchanged no-due-event path."""
from pathlib import Path
import argparse

MARKER = 'NATIVE_EVENT_ADVANCE_FASTPATH_V1'
INLINE = '''    // NATIVE_EVENT_ADVANCE_FASTPATH_V1: every accepted clock update remains visible.
    __attribute__((always_inline)) inline void advance(CPUContext& ctx, uint64_t now) {
        if (now < m_now) return;
        m_now = now;
        if (m_firing || m_heap.empty() || m_heap.front().due > m_now) return;
        fire_due(ctx);
    }'''
PREFIX = '''void EventScheduler::advance(CPUContext& ctx, uint64_t now) {
    if (now < m_now) return; 
    m_now = now;
    if (m_firing) return; 
    m_firing = true;'''

def apply_header(source):
    if MARKER in source:
        assert source.count(INLINE) == 1
        assert source.count('    void fire_due(CPUContext& ctx);') == 1
        return source
    declaration = '    void advance(CPUContext& ctx, uint64_t now);'
    assert source.count(declaration) == 1
    source = source.replace(declaration, INLINE, 1)
    anchor = '    void push(Event e);'
    assert source.count(anchor) == 1
    return source.replace(anchor, '    void fire_due(CPUContext& ctx);\n'+anchor, 1)

def apply_cpp(source):
    if MARKER in source:
        assert source.count('void EventScheduler::fire_due(CPUContext& ctx) {') == 1
        return source
    assert source.count(PREFIX) == 1
    return source.replace(PREFIX,
        '// '+MARKER+': due-event loop, ordering and recurrence remain unchanged.\n'
        'void EventScheduler::fire_due(CPUContext& ctx) {\n    m_firing = true;', 1)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, default=Path(__file__).resolve().parents[1]/'build/nwii-full-source/nWiiRuntime')
    args = parser.parse_args()
    for path, transform in [(args.runtime/'include/runtime/event_scheduler.h', apply_header),
                            (args.runtime/'src/core/event_scheduler.cpp', apply_cpp)]:
        source = path.read_text()
        changed = transform(source)
        assert transform(changed) == changed
        if changed != source:
            path.write_text(changed)
    print('No-due event path inlined; clock updates and due callbacks retained.')
