"""Default locations of the build inputs and outputs. Every default can be overridden on the command line.

    NWII_BUILDS_DIR   where builds and clean-room trees go (default: build/out in this repository)
    NWII_DOL          the game's main.dol (default: game/sys/main.dol)
    NWII_GAME_FILES   the game's extracted files/ folder (default: game/files)
"""
from pathlib import Path
import os

PORT = Path(__file__).resolve().parents[1]
ROOT = PORT.parent
INDEX = PORT / 'data/function-index.tsv'


def builds_dir():
    if os.environ.get('NWII_BUILDS_DIR'):
        return Path(os.environ['NWII_BUILDS_DIR'])
    return PORT / 'build/out'


def default_storage():
    return Path(os.environ['NWII_BUILD_STORAGE']) if os.environ.get('NWII_BUILD_STORAGE') else builds_dir() / 'CrashTitans-RQJP7D'


def _first(env, *candidates):
    if os.environ.get(env):
        return Path(os.environ[env])
    return next((c for c in candidates if c.exists()), candidates[0])


def default_dol():
    return _first('NWII_DOL', PORT / 'game/sys/main.dol', ROOT / 'orig/main.dol')


def default_game_files():
    return _first('NWII_GAME_FILES', PORT / 'game/files', ROOT / 'dump/files')


def winlibs_bin():
    """MinGW-w64 bin folder of a WinGet WinLibs install, if present; otherwise rely on PATH."""
    bin_dir = Path.home() / 'AppData/Local/Microsoft/WinGet/Packages/BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe/mingw64/bin'
    return bin_dir if bin_dir.is_dir() else None
