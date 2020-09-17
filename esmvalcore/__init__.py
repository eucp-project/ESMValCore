"""ESMValTool core package."""
import logging
import os

from ._config import config
from ._locations import locations
from ._version import __version__

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

__all__ = [
    '__version__',
    'cmor',
    'config',
    'locations',
    'preprocessor',
]


def get_script_root():
    """Return the location of the ESMValTool installation."""
    return os.path.abspath(os.path.dirname(__file__))
