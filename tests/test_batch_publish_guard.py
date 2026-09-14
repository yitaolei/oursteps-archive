import _test_config  # Configure synthetic identity before application imports.
import hashlib
import sys
from pathlib import Path
from unittest.mock import patch
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import public_release as pub
import tempfile

class GuardTests(unittest.TestCase):
    def test_pending_and_changed_receipt_stop_before_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data/preview').mkdir(parents=True)
            page=root/'data/preview/123.html';page.write_text('original')
            marker=root/'data/preview-incremental-pending.json';marker.write_text('{}')
            with patch.object(pub,'config_check'),patch.object(pub,'pointer',return_value=None),patch.object(pub,'expected_tids') as expected:
                with self.assertRaisesRegex(ValueError,'pending'):pub.publish(root)
                expected.assert_not_called();marker.unlink()
                with self.assertRaisesRegex(ValueError,'changed'):pub.publish(root,preview_hashes={'123.html':'wrong'})
                expected.assert_not_called()
                with self.assertRaisesRegex(ValueError,'invalid batch'):pub.publish(root,preview_hashes={'../bad':'wrong'})
                expected.assert_not_called()
                with patch.object(pub,'publication_dates',side_effect=RuntimeError('past guard')):
                    with self.assertRaisesRegex(RuntimeError,'past guard'):pub.publish(root,preview_hashes={'123.html':hashlib.sha256(page.read_bytes()).hexdigest()})
