"""A GX restage must retain/repair the save-state FIFO declarations and busy guard."""
from fix_savestate import GX_BUSY_OLD, GX_BUSY_NEW, GX_LOCAL, GX_TAIL, Skip, gx_state_hook

def main():
    baseline='''static uint32_t s_early_bp_shadow[256];
static uint32_t s_early_bp_mask;
static uint32_t g_wpar_cur;
static NativeCpuFifo g_cpu_fifo;
std::vector<uint8_t> g_hw_fifo;
'''+GX_BUSY_OLD+GX_LOCAL+'}\n'
    complete=gx_state_hook(baseline)
    assert gx_state_hook(complete)==complete
    # Earlier FIFO stages can restore the function without removing the tail.
    replay=baseline+'\n'+GX_TAIL
    repaired=gx_state_hook(replay)
    assert repaired.count('static std::atomic<int> s_native_ss_gx_busy{0};')==1
    assert repaired.count('static std::vector<uint8_t> s_parse_buf;')==1
    assert GX_BUSY_NEW in repaired and GX_LOCAL not in repaired
    assert repaired.count('bool native_ss_gx_sync(int timeout_ms)')==1
    assert gx_state_hook(repaired)==repaired
    try:gx_state_hook(replay.replace(GX_BUSY_OLD,'moved FIFO body\n'))
    except Skip:pass
    else:raise AssertionError('moved FIFO anchor was accepted')
    print('Save-state FIFO staging: initial install, idempotence, partial replay repair and moved-anchor rejection passed.')

if __name__=='__main__':main()
