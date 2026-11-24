"""Unit tests for scripts/toolchain_links.py."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
toolchain_links = importlib.import_module('toolchain_links')


class ToolchainLinksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pop-toolchain-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin_dir = self.root / 'bin'
        self.dest_dir = self.root / 'local-bin'
        self.bin_dir.mkdir()

    def make_binaries(self, major, names=('gcc', 'g++', 'gcc-ar')):
        for name in names:
            path = self.bin_dir / f'{name}-{major}'
            path.write_text('#!/bin/sh\nexit 0\n')
            path.chmod(0o755)

    def test_select_latest_major_chooses_highest_complete_set(self):
        pkgnames = '\n'.join([
            'gcc-12', 'g++-12',
            'gcc-14', 'g++-14',
            'gcc-16', 'g++-16',
            'gcc-99-aarch64-linux-gnu', 'gcc-20',
        ])
        policies = {
            'gcc-16': 'gcc-16:\n  Candidate: 16.2.0\n',
            'g++-16': 'g++-16:\n  Candidate: (none)\n',
            'gcc-14': 'gcc-14:\n  Candidate: 14.2.0\n',
            'g++-14': 'g++-14:\n  Candidate: 14.2.0\n',
        }
        major = toolchain_links.select_latest_major(
            ['gcc', 'g++'],
            pkgnames,
            lambda pkg: toolchain_links.parse_apt_candidate(policies.get(pkg, ''), pkg),
        )
        self.assertEqual(major, '14')

    def test_parse_apt_candidate_ignores_pattern_matches_for_other_packages(self):
        policy = 'unrelated-package:\n  Candidate: 99.0\n'
        self.assertIsNone(toolchain_links.parse_apt_candidate(policy, 'gcc-99'))

    def test_select_latest_major_raises_when_unavailable(self):
        with self.assertRaises(RuntimeError):
            toolchain_links.select_latest_major(['gcc', 'g++'], '', lambda _pkg: None)

    def test_sync_links_creates_upgrades_and_is_idempotent(self):
        self.make_binaries('14')
        self.make_binaries('16')
        specs = ['gcc', 'g++', 'cc=gcc', 'c++=g++', 'gcc-ar']

        # Check mode reports change without mutating disk
        self.assertTrue(
            toolchain_links.sync_links('14', self.bin_dir, self.dest_dir, specs, check=True)
        )
        self.assertFalse(self.dest_dir.exists())

        # Initial creation reports changed
        self.assertTrue(toolchain_links.sync_links('14', self.bin_dir, self.dest_dir, specs))
        self.assertEqual(os.readlink(self.dest_dir / 'cc'), str(self.bin_dir / 'gcc-14'))

        # Second pass reports unchanged
        self.assertFalse(toolchain_links.sync_links('14', self.bin_dir, self.dest_dir, specs))

        # Upgrade to major 16 reports changed and updates links
        self.assertTrue(toolchain_links.sync_links('16', self.bin_dir, self.dest_dir, specs))
        self.assertEqual(os.readlink(self.dest_dir / 'cc'), str(self.bin_dir / 'gcc-16'))
        self.assertEqual(os.readlink(self.dest_dir / 'c++'), str(self.bin_dir / 'g++-16'))

    def test_sync_links_refuses_foreign_files_or_links_before_mutating(self):
        self.make_binaries('14')
        self.dest_dir.mkdir()
        (self.dest_dir / 'g++').write_text('custom wrapper')
        with self.assertRaises(RuntimeError):
            toolchain_links.sync_links('14', self.bin_dir, self.dest_dir, ['gcc', 'g++'])
        self.assertFalse((self.dest_dir / 'gcc').exists())

        (self.dest_dir / 'g++').unlink()
        (self.dest_dir / 'g++').symlink_to('/opt/custom/g++')
        with self.assertRaises(RuntimeError):
            toolchain_links.sync_links('14', self.bin_dir, self.dest_dir, ['gcc', 'g++'])
        self.assertFalse((self.dest_dir / 'gcc').exists())

    def test_sync_links_requires_target_executable(self):
        self.make_binaries('14', names=('gcc',))
        with self.assertRaises(RuntimeError):
            toolchain_links.sync_links('14', self.bin_dir, self.dest_dir, ['gcc', 'g++'])
        self.assertFalse(self.dest_dir.exists())
