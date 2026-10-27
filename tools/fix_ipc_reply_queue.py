"""Keep asynchronous IOS completions until PPC acknowledges each reply."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hw/hw_ipc.cpp';s=p.read_text()
if 'IPC_REPLY_QUEUE_V1' not in s:
    s=s.replace('#include <algorithm>','#include <algorithm>\n#include <deque>',1)
    s=s.replace('uint32_t ipc_ppc_msg = 0;', '''uint32_t ipc_ppc_msg = 0;
// IPC_REPLY_QUEUE_V1: asynchronous completions may arrive during a request.
static std::deque<uint32_t> ipc_replies;
static void enqueue_ipc_reply(uint32_t req_addr) {
  ipc_replies.push_back(req_addr & 0x1FFFFFFF);
  if (ipc_replies.size() == 1) ipc_arm_msg = ipc_replies.front();
  ipc_ppc_ctrl |= 0x04;
  g_ipc_interrupt_delay = 50;
}''',1)
    s=s.replace('  ipc_arm_msg  = virt_addr & 0x1FFFFFFF;','  if (result != -0x70000001) enqueue_ipc_reply(virt_addr);',1)
    old='''  ipc_arm_msg = req_addr & 0x1FFFFFFF;
  ipc_ppc_ctrl &= ~0x01;
  ipc_ppc_ctrl |= 0x26; 
  g_ipc_interrupt_delay = 50;'''
    if old not in s:raise SystemExit('Async IPC reply queue context changed')
    s=s.replace(old,'  enqueue_ipc_reply(req_addr);',1)
    old='                if (val & 0x04) ipc_ppc_ctrl &= ~0x04; '
    new='''                if (val & 0x04) {
                    ipc_ppc_ctrl &= ~0x04;
                    if (!ipc_replies.empty()) ipc_replies.pop_front();
                    if (!ipc_replies.empty()) {
                        ipc_arm_msg = ipc_replies.front();
                        ipc_ppc_ctrl |= 0x04;
                        g_ipc_interrupt_delay = 50;
                    }
                }'''
    if old not in s:raise SystemExit('IPC reply acknowledge context changed')
    s=s.replace(old,new,1)
    p.write_text(s)
s=p.read_text()
old='ipc_ppc_ctrl &= ~0x05; // clear send and reply flags'
new='ipc_ppc_ctrl &= ~0x01; // clear send; retain unrelated asynchronous replies\n                        if (ipc_replies.empty()) ipc_ppc_ctrl &= ~0x04;'
if old in s:p.write_text(s.replace(old,new,1))
print('Asynchronous IPC reply queue staged.')
