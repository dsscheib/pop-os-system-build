"""Unit tests for scripts/merge_lazy_lock.py."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
mll = importlib.import_module('merge_lazy_lock')


def _git(cwd, *args):
    return subprocess.run(
        ['git', '-C', str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


class MergeLazyLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pop-lazylock-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_merge_keeps_local_when_upstream_unchanged_and_counts_conflicts(self):
        base = {
            'a': {'branch': 'main', 'commit': '1'},
            'b': {'branch': 'main', 'commit': '1'},
            'removed': {'branch': 'main', 'commit': '1'},
        }
        upstream = {
            'a': {'branch': 'main', 'commit': '1'},
            'b': {'branch': 'main', 'commit': '2-upstream'},
            'added-upstream': {'branch': 'main', 'commit': 'u1'},
        }
        local = {
            'a': {'branch': 'main', 'commit': '2-local'},
            'b': {'branch': 'main', 'commit': '2-local'},
            'removed': {'branch': 'main', 'commit': '1'},
            'added-local': {'branch': 'main', 'commit': 'l1'},
        }
        merged, conflicts = mll.merge_lazy_lock(base, upstream, local)
        self.assertEqual(conflicts, 1)
        self.assertEqual(merged['a']['commit'], '2-local')
        self.assertEqual(merged['b']['commit'], '2-upstream')
        self.assertIn('added-upstream', merged)
        self.assertIn('added-local', merged)
        self.assertNotIn('removed', merged)

    def test_format_lazy_lock_round_trips_json(self):
        data = {'alpha': {'branch': 'main', 'commit': 'abc'}, 'zeta': {'commit': 'def'}}
        formatted = mll.format_lazy_lock(data)
        self.assertEqual(json.loads(formatted), data)
        self.assertIn('  "alpha": { "branch": "main", "commit": "abc" },\n', formatted)

    def test_sync_repo_clones_and_merges_dirty_lazy_lock(self):
        remote = self.root / 'remote.git'
        work = self.root / 'work'
        subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
        subprocess.run(['git', 'clone', str(remote), str(work)], check=True, capture_output=True)
        _git(work, 'config', 'user.name', 'Test')
        _git(work, 'config', 'user.email', 'test@example.com')

        (work / 'lazy-lock.json').write_text(mll.format_lazy_lock({
            'a': {'commit': 'base-a'},
            'b': {'commit': 'base-b'},
        }))
        _git(work, 'add', 'lazy-lock.json')
        _git(work, 'commit', '-m', 'initial')
        _git(work, 'push', 'origin', 'HEAD')

        dest = self.root / 'nvim'
        res1 = mll.sync_repo(str(remote), str(dest))
        self.assertTrue(res1['changed'])
        self.assertFalse(res1['merged'])

        # Advance upstream plugin b
        (work / 'lazy-lock.json').write_text(mll.format_lazy_lock({
            'a': {'commit': 'base-a'},
            'b': {'commit': 'upstream-b'},
        }))
        _git(work, 'commit', '-am', 'update b')
        _git(work, 'push', 'origin', 'HEAD')

        # Locally update plugin a without committing
        (dest / 'lazy-lock.json').write_text(mll.format_lazy_lock({
            'a': {'commit': 'local-a'},
            'b': {'commit': 'base-b'},
        }))

        # Check mode must not modify disk
        check_res = mll.sync_repo(str(remote), str(dest), check_mode=True)
        self.assertTrue(check_res['changed'])
        self.assertEqual(json.loads((dest / 'lazy-lock.json').read_text())['b']['commit'], 'base-b')

        res2 = mll.sync_repo(str(remote), str(dest))
        self.assertTrue(res2['changed'])
        self.assertTrue(res2['merged'])
        self.assertEqual(res2['conflicts'], 0)
        final_lock = json.loads((dest / 'lazy-lock.json').read_text())
        self.assertEqual(final_lock['a']['commit'], 'local-a')
        self.assertEqual(final_lock['b']['commit'], 'upstream-b')

    def test_sync_repo_rejects_other_dirty_files(self):
        remote = self.root / 'remote.git'
        work = self.root / 'work'
        subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
        subprocess.run(['git', 'clone', str(remote), str(work)], check=True, capture_output=True)
        _git(work, 'config', 'user.name', 'Test')
        _git(work, 'config', 'user.email', 'test@example.com')
        (work / 'init.lua').write_text('-- init\n')
        _git(work, 'add', 'init.lua')
        _git(work, 'commit', '-m', 'init')
        _git(work, 'push', 'origin', 'HEAD')

        dest = self.root / 'nvim'
        mll.sync_repo(str(remote), str(dest))
        (dest / 'init.lua').write_text('-- dirty\n')
        with self.assertRaises(RuntimeError):
            mll.sync_repo(str(remote), str(dest))

    def test_failed_update_restores_local_lazy_lock(self):
        remote = self.root / 'remote.git'
        work = self.root / 'work'
        subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
        subprocess.run(['git', 'clone', str(remote), str(work)], check=True, capture_output=True)
        _git(work, 'config', 'user.name', 'Test')
        _git(work, 'config', 'user.email', 'test@example.com')
        (work / 'lazy-lock.json').write_text(mll.format_lazy_lock({'a': {'commit': 'base'}}))
        _git(work, 'add', 'lazy-lock.json')
        _git(work, 'commit', '-m', 'init')
        _git(work, 'push', 'origin', 'HEAD')

        dest = self.root / 'nvim'
        mll.sync_repo(str(remote), str(dest))
        local_text = mll.format_lazy_lock({'a': {'commit': 'local-edit'}})
        (dest / 'lazy-lock.json').write_text(local_text)

        with self.assertRaises(subprocess.CalledProcessError):
            mll.sync_repo(str(remote), str(dest), ref='nonexistent-ref')
        self.assertEqual((dest / 'lazy-lock.json').read_text(), local_text)

    def test_check_mode_handles_commit_sha_annotated_tag_and_non_default_branch(self):
        remote = self.root / 'remote.git'
        work = self.root / 'work'
        subprocess.run(['git', 'init', '--bare', '-b', 'main', str(remote)], check=True, capture_output=True)
        subprocess.run(['git', 'clone', str(remote), str(work)], check=True, capture_output=True)
        _git(work, 'config', 'user.name', 'Test')
        _git(work, 'config', 'user.email', 'test@example.com')

        (work / 'lazy-lock.json').write_text(mll.format_lazy_lock({'a': {'commit': 'c1'}}))
        _git(work, 'add', 'lazy-lock.json')
        _git(work, 'commit', '-m', 'c1')
        _git(work, 'push', 'origin', 'main')
        c1 = _git(work, 'rev-parse', 'HEAD').stdout.strip()
        _git(work, 'tag', '-a', 'v1.0', '-m', 'release 1.0')
        _git(work, 'push', 'origin', 'v1.0')

        _git(work, 'checkout', '-b', 'feat')
        (work / 'lazy-lock.json').write_text(mll.format_lazy_lock({'a': {'commit': 'c2'}}))
        _git(work, 'commit', '-am', 'c2')
        _git(work, 'push', '-u', 'origin', 'feat')
        c2 = _git(work, 'rev-parse', 'HEAD').stdout.strip()

        dest = self.root / 'nvim-sha'
        mll.sync_repo(str(remote), str(dest), ref=c1)
        self.assertFalse(mll.sync_repo(str(remote), str(dest), ref=c1, check_mode=True)['changed'])
        self.assertFalse(mll.sync_repo(str(remote), str(dest), ref=c1[:10], check_mode=True)['changed'])
        self.assertTrue(mll.sync_repo(str(remote), str(dest), ref=c2, check_mode=True)['changed'])
        self.assertFalse(mll.sync_repo(str(remote), str(dest), ref='v1.0', check_mode=True)['changed'])

        _git(work, 'tag', '-a', 'v2.0', '-m', 'release 2.0')
        _git(work, 'push', 'origin', 'v2.0')
        self.assertTrue(mll.sync_repo(str(remote), str(dest), ref='v2.0', check_mode=True)['changed'])

        dest_feat = self.root / 'nvim-feat'
        subprocess.run(['git', 'clone', '-b', 'feat', str(remote), str(dest_feat)], check=True, capture_output=True)
        self.assertFalse(mll.sync_repo(str(remote), str(dest_feat), check_mode=True)['changed'])

        (work / 'lazy-lock.json').write_text(mll.format_lazy_lock({'a': {'commit': 'c3'}}))
        _git(work, 'commit', '-am', 'c3')
        _git(work, 'push', 'origin', 'feat')
        self.assertTrue(mll.sync_repo(str(remote), str(dest_feat), check_mode=True)['changed'])
