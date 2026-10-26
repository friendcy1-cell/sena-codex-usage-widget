from pathlib import Path
import tempfile
import unittest
import zipfile

from make_release import ROOT, source_files, archive_files


class ReleaseTests(unittest.TestCase):
    def test_allowlist_excludes_runtime_and_private_documents(self):
        files = source_files()
        for name in files:
            self.assertFalse(any(part in ('data', 'qa-data', 'backups', '.codex', '__pycache__') for part in Path(name).parts), name)
            self.assertNotIn('auth.json', name)
            self.assertNotIn('QA_REPORT', name)
            self.assertNotIn('preview.png', name)
        self.assertTrue({'README.md', 'PRIVACY.md', 'LICENSE-CODE.md', 'VOICE_BRIEF_JA.md'} <= files.keys())
        self.assertNotIn('frien', files['README.md'].read_text(encoding='utf8'))
        self.assertIn('candidate', files['README.md'].read_text(encoding='utf8').lower())

    def test_archive_contains_only_selected_paths_and_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'candidate.zip'
            archive_files(archive, 'candidate', {'VERSION': ROOT / 'VERSION'})
            with zipfile.ZipFile(archive) as saved:
                self.assertEqual(saved.namelist(), ['candidate/VERSION'])
            with self.assertRaises(FileExistsError): archive_files(archive, 'candidate', {})


if __name__ == '__main__':
    unittest.main()
