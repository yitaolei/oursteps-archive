"""Support package-based runners as well as unittest discover -s tests."""
import sys
from . import _test_config

sys.modules['_test_config'] = _test_config
