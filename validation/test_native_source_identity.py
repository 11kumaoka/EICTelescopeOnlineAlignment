"""Provenance of a reviewed candidate before its first Git commit."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from calibration import native_controller as controller


class UnbornRepositoryIdentity(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        subprocess.run(['git', 'init', '--quiet', '--initial-branch=main', str(self.root)], check=True)
        (self.root / 'source.py').write_text('VALUE = 1\n')
        self.repo = patch.object(controller, 'REPO', self.root)
        self.repo.start(); self.addCleanup(self.repo.stop)

    def test_unborn_head_has_no_fabricated_commit(self):
        result = controller.repository_identity()
        self.assertIsNone(result['head'])
        self.assertEqual(result['git_head_state'], 'unborn')
        expected = {'source.py': hashlib.sha256(b'VALUE = 1\n').hexdigest()}
        fingerprint = hashlib.sha256(json.dumps(expected, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(result['source_tree_sha256'], fingerprint)
        self.assertNotEqual(subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'],
            cwd=self.root, capture_output=True).returncode, 0)

    def test_source_edit_changes_identity_and_is_refused(self):
        first = controller.repository_identity()
        manifest = {'input': {}, 'sources': controller.source_identity(), 'build': {'files': {}}}
        controller.validate_run_inputs(manifest)
        (self.root / 'source.py').write_text('VALUE = 2\n')
        self.assertNotEqual(first['source_tree_sha256'], controller.repository_identity()['source_tree_sha256'])
        with self.assertRaisesRegex(RuntimeError, 'Implementation source set or contents changed'):
            controller.validate_run_inputs(manifest)

    def test_new_source_is_not_silently_ignored(self):
        manifest = {'input': {}, 'sources': controller.source_identity(), 'build': {'files': {}}}
        (self.root / 'extra.py').write_text('VALUE = 3\n')
        with self.assertRaisesRegex(RuntimeError, 'Implementation source set or contents changed'):
            controller.validate_run_inputs(manifest)

    def test_parent_repository_is_refused(self):
        child = self.root / 'child'; child.mkdir()
        with patch.object(controller, 'REPO', child):
            with self.assertRaisesRegex(RuntimeError, 'own Git repository'):
                controller.repository_identity()


if __name__ == '__main__':
    unittest.main()
