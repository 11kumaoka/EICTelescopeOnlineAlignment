"""Shared dependency selection must not fall back to a stale installation."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import research_common
from calibration.reconstruction.operations import environment


class DependencyPrefixTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name).resolve()
        self.repo_patch = patch.object(research_common, 'REPO', self.repo)
        self.repo_patch.start()
        self.addCleanup(self.repo_patch.stop)
        self.env_patch = patch.dict(os.environ)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        os.environ.pop('MILLEPEDE_PREFIX', None)

    def install(self, relative):
        root = self.repo / relative
        for name in ('cmake/MilleConfig.cmake', 'lib/libMille2.so', 'lib/libMilleC.so', 'bin/pede'):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        return root

    def test_fresh_clone_uses_standard_install(self):
        selected = self.install('external/install')
        self.assertEqual(research_common.prefix(), selected)

    def test_local_current_takes_precedence_over_old_install(self):
        self.install('external/install')
        selected = self.install('external/current')
        self.assertEqual(research_common.prefix(), selected)

    def test_explicit_relative_override_is_independent_of_cwd(self):
        self.install('external/current')
        selected = self.install('external/replacement/install')
        os.environ['MILLEPEDE_PREFIX'] = 'external/replacement/install'
        self.assertEqual(research_common.prefix(), selected)
        os.environ['LD_LIBRARY_PATH'] = '/old/libraries'
        runtime = environment(self.repo / 'build/tracking')
        self.assertEqual(runtime['LD_LIBRARY_PATH'].split(':')[:2],
                         [str(selected / 'lib'), str(self.repo / 'build/tracking')])

    def test_incomplete_current_does_not_fall_back(self):
        self.install('external/install')
        (self.repo / 'external/current').mkdir()
        with self.assertRaises(FileNotFoundError):
            research_common.prefix()

    def test_missing_explicit_override_does_not_fall_back(self):
        self.install('external/install')
        os.environ['MILLEPEDE_PREFIX'] = 'missing'
        with self.assertRaises(FileNotFoundError):
            research_common.prefix()

    def test_broken_current_link_does_not_fall_back(self):
        self.install('external/install')
        (self.repo / 'external/current').symlink_to('missing')
        with self.assertRaises(FileNotFoundError):
            research_common.prefix()


if __name__ == '__main__':
    unittest.main()
