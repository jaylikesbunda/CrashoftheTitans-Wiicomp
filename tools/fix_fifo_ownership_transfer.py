"""Transfer complete FIFO buffers when destination is empty; retain all boundaries."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp';s=f.read_text()
if 'NATIVE_FIFO_OWNERSHIP_TRANSFER_V1' not in s:
    baseline=p/'runtime-overlays/fifo-transfer-before.cpp'
    if not baseline.exists():baseline.write_text(s)
    helper='''// NATIVE_FIFO_OWNERSHIP_TRANSFER_V1: callers retain the original FIFO mutex.
static void NativeAppendFifo(std::vector<uint8_t>& destination,std::vector<uint8_t>& source){
    if(destination.empty())destination.swap(source);
    else {destination.insert(destination.end(),source.begin(),source.end());source.clear();}
}
'''
    s=s.replace('void ProcessGXFifo() {',helper+'void ProcessGXFifo() {',1)
    old='''            s_parse_buf.insert(s_parse_buf.end(), g_hw_fifo.begin(),
                               g_hw_fifo.end());
            g_hw_fifo.clear();'''
    assert s.count(old)==1;s=s.replace(old,'            NativeAppendFifo(s_parse_buf,g_hw_fifo);',1)
    old='''        g_hw_fifo.insert(g_hw_fifo.end(), g_cpu_fifo.begin(), g_cpu_fifo.end());
        g_gx_pending_bytes.fetch_add(g_cpu_fifo.size(), std::memory_order_relaxed);'''
    new='''        const size_t published=g_cpu_fifo.size();
        NativeAppendFifo(g_hw_fifo,g_cpu_fifo);
        g_gx_pending_bytes.fetch_add(published, std::memory_order_relaxed);'''
    assert s.count(old)==1;s=s.replace(old,new,1);f.write_text(s)
# NATIVE_FIFO_DRAIN_UNLOCKED_V1: swap the whole pending FIFO under the lock
# (O(1)) and append it to the render-owned parse buffer after unlocking, so the
# guest publish never blocks on the render thread's copy.
marker='NATIVE_FIFO_DRAIN_UNLOCKED_V1'
if marker not in s:
    old='''    {
        std::lock_guard<std::mutex> lock(g_fifo_mutex);
        if (!g_hw_fifo.empty()) {
            NativeAppendFifo(s_parse_buf,g_hw_fifo);
        }
        g_gx_pending_bytes.store(0, std::memory_order_relaxed);
    }
'''
    new='''    std::vector<uint8_t> native_drain_buf; // NATIVE_FIFO_DRAIN_UNLOCKED_V1
    {
        std::lock_guard<std::mutex> lock(g_fifo_mutex);
        if (!g_hw_fifo.empty()) native_drain_buf.swap(g_hw_fifo);
        g_gx_pending_bytes.store(0, std::memory_order_relaxed);
    }
    if (!native_drain_buf.empty()) NativeAppendFifo(s_parse_buf,native_drain_buf);
'''
    assert s.count(old)==1,('fifo drain anchor',s.count(old))
    s=s.replace(old,new,1);f.write_text(s)
print('CPU -> hardware -> parser FIFO handoffs swap ownership when empty; ordering/locks preserved.')
