"""Unit tests for scripts/chezmoi_conflicts.py."""
import importlib
import io
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
chezmoi_conflicts = importlib.import_module('chezmoi_conflicts')

# Status lines captured from chezmoi v2.74.2 against a scratch source and destination.
STATUS = '\n'.join([
    'MM .config/cosmic/com.system76.CosmicTerm/v1/font_name',  # edited locally, prompts
    'MM .config/systemd/user',                                   # dir mode changed locally, prompts
    'DA .cfg/sub/other',                                         # deleted locally, prompts
    ' M .cfg/untouched',                                         # only the source changed
    ' M .cfg/preexisting',                                       # never written by chezmoi
    ' R x.sh',                                                   # script to run
    ' A .cfg/new file with spaces',
    '',
])


class ChezmoiConflictsTests(unittest.TestCase):
    def test_reports_only_entries_changed_on_both_sides(self):
        self.assertEqual(
            chezmoi_conflicts.conflicts(STATUS),
            [
                '.config/cosmic/com.system76.CosmicTerm/v1/font_name',
                '.config/systemd/user',
                '.cfg/sub/other',
            ],
        )

    def test_clean_status_reports_nothing(self):
        self.assertEqual(chezmoi_conflicts.conflicts(''), [])
        self.assertEqual(chezmoi_conflicts.conflicts(' M .bashrc\n R x.sh\n'), [])

    def test_ignores_malformed_lines(self):
        self.assertEqual(chezmoi_conflicts.conflicts('MM\nMMX.bashrc\nchezmoi: warning\n'), [])

    def test_main_prints_one_path_per_line(self):
        out = io.StringIO()
        with mock.patch('sys.stdin', io.StringIO(STATUS)), redirect_stdout(out):
            self.assertEqual(chezmoi_conflicts.main([]), 0)
        self.assertEqual(out.getvalue().splitlines()[0], '.config/cosmic/com.system76.CosmicTerm/v1/font_name')
        self.assertEqual(len(out.getvalue().splitlines()), 3)

    def test_main_rejects_arguments(self):
        with self.assertRaises(SystemExit):
            chezmoi_conflicts.main(['extra'])


if __name__ == '__main__':
    unittest.main()
