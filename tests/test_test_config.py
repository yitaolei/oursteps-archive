import _test_config
import unittest
from oursteps.config import UID, USERNAME, DISPLAY_NAME
from oursteps.deployment import deployment


class TestBootstrap(unittest.TestCase):
    def test_synthetic_configuration(self):
        self.assertEqual((UID, USERNAME, DISPLAY_NAME),
                         (424242, 'archive_test_user', 'Test Archive User'))
        self.assertEqual(deployment(), ('test-user@nas.example.invalid', '/srv/oursteps-archive'))
