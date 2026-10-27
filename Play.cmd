@echo off
rem Plain launch (no frame trace). Any NWII_* variable you set first is passed to the game, for example:
rem     set NWII_HUD=1                  dev stats overlay on at start (F3 toggles, F4 exports)
rem     set NWII_STATE_CENSUS=1         writes state-census.txt (GX draw states in the scene)
rem     set NWII_IR_INVERT_Y=1          flip the pointer vertically   (NWII_IR_RANGE_X/Y, NWII_IR_BAR_Y tune it)
rem     set NWII_SYNC_GPU=1             old behaviour: guest waits for the renderer at each GPU sync
rem To do it in one line from a normal command prompt:  set NWII_HUD=1 && Play.cmd
rem Uses the last tools\build_native.py output; pass --exe and --game to point elsewhere.
cd /d "%~dp0"
python "%~dp0tools\run_startup.py" --window --clock wall --seconds 86400 --name play %*
pause
