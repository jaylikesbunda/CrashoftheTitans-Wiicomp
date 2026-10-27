"""Keep the dispatcher recovery frame across routine returns, reenter on longjmp."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];f=p/'build/crash-generated/main_output.cpp';s=f.read_text()
marker='NATIVE_DISPATCH_RECOVERY_SCOPE_V1'
if marker not in s:
    (p/'runtime-overlays/dispatch-scope-before.cpp').write_text(s)
    a=s.index('#if defined(__MINGW64__)\n      if (_setjmp(ctx.exception_jmp_buf, nullptr) == 0) {',s.index('extern "C" void run_game'))
    b=s.index('        process_pending_callbacks(ctx);',a)
    setup=s[a:b]
    replacement='''    // NATIVE_DISPATCH_RECOVERY_SCOPE_V1: one live recovery frame per run_game.
    // No locals modified across longjmp live outside the loop; mutable state
    // stays in CPUContext/static history. A jump resumes at the loop head.
#if defined(__MINGW64__)
    (void)_setjmp(ctx.exception_jmp_buf, nullptr);
#else
    (void)_setjmp(ctx.exception_jmp_buf);
#endif
'''
    s=s[:a]+s[b:]
    anchor='    while (ctx.is_running) {';assert s.count(anchor)==1;s=s.replace(anchor,replacement+anchor,1)
    ending='''      } else {
          continue;
      }
    }
    std::cout << "[DEBUG] PC History'''
    assert s.count(ending)==1;s=s.replace(ending,'''    }
    std::cout << "[DEBUG] PC History''',1)
    f.write_text(s)
print('Dispatcher exception recovery spans the dispatch loop; longjmp resumes its head.')
