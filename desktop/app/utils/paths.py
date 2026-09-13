"""Cross-platform path utilities for DEPRESSION-PLEX.

Handles resource location and user data directories for both
development (unfrozen) and production (PyInstaller frozen) contexts.
"""

import sys
from pathlib import Path


APP_NAME = "DEPRESSION-PLEX"
ORG_NAME = "Gene&I"


def is_frozen() -> bool:
    """Check if running as a PyInstaller frozen executable."""
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def resource_path(relative_path: str) -> Path:
    """Get absolute path to a resource, works for dev and frozen modes.

    In development: resolves relative to desktop/ directory.
    In frozen mode: resolves relative to PyInstaller's temporary folder.

    Args:
        relative_path: Path relative to the desktop/ directory

    Returns:
        Absolute Path object
    """
    if is_frozen():
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = Path(sys._MEIPASS)
    else:
        # Development: this file is desktop/app/utils/paths.py
        # So parent.parent.parent gives us desktop/
        base_path = Path(__file__).resolve().parent.parent.parent

    return base_path / relative_path


def user_data_dir() -> Path:
    """Get platform-specific user data directory.

    Creates the directory if it doesn't exist.

    Returns:
        Path object for user data directory
    """
    if sys.platform == 'win32':
        base = Path.home() / 'AppData' / 'Local' / ORG_NAME / APP_NAME
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support' / APP_NAME
    else:  # Linux and others
        base = Path.home() / '.local' / 'share' / APP_NAME.lower()

    base.mkdir(parents=True, exist_ok=True)
    return base


def user_log_dir() -> Path:
    """Get platform-specific user log directory.

    Creates the directory if it doesn't exist.

    Returns:
        Path object for log directory
    """
    if sys.platform == 'win32':
        # On Windows, logs go in the same place as data
        base = user_data_dir() / 'logs'
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Logs' / APP_NAME
    else:  # Linux
        base = Path.home() / '.local' / 'share' / APP_NAME.lower() / 'logs'

    base.mkdir(parents=True, exist_ok=True)
    return base


def user_cache_dir() -> Path:
    """Get platform-specific user cache directory.

    Creates the directory if it doesn't exist.

    Returns:
        Path object for cache directory
    """
    if sys.platform == 'win32':
        base = Path.home() / 'AppData' / 'Local' / ORG_NAME / APP_NAME / 'Cache'
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Caches' / APP_NAME
    else:  # Linux
        base = Path.home() / '.cache' / APP_NAME.lower()

    base.mkdir(parents=True, exist_ok=True)
    return base
