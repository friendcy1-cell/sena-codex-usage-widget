import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from bundle_check import check_bundle
from bundle_paths import application_paths
from character_packs import discover_packs
from make_release import ROOT, ASSETS, embedded_files, runtime_files, source_files, license_destination
import widget


class BundlePathTests(unittest.TestCase):
    def test_source_uses_source_directory(self):
        base, resources = application_paths('other/python.exe', ROOT / 'widget.py')
        self.assertEqual((base, resources), (ROOT, ROOT))

    def test_folder_build_keeps_external_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); internal = root / '_internal'; internal.mkdir()
            base, resources = application_paths(root / 'Widget.exe', internal / 'widget.py', True, internal)
            self.assertEqual((base, resources), (root, root))

    def test_onefile_embedded_assets_do_not_change_external_pack_location(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); external = root / 'portable space 한글'; internal = root / '_MEI-fixture'
            (internal / 'assets').mkdir(parents=True); external.mkdir()
            (internal / 'assets' / 'idle_alt.png').write_bytes(b'path-fixture')
            base, resources = application_paths(external / 'Widget.exe', internal / 'widget.py', True, internal)
            self.assertEqual((base, resources), (external, internal))
            pack = external / 'packs' / 'fixture'; pack.mkdir(parents=True)
            shutil.copy2(ROOT / 'assets' / 'idle_alt.png', pack / 'idle.png')
            (pack / 'pack.json').write_text(json.dumps({'version': 1, 'name': 'Fixture',
                                                      'images': {'idle_alt': 'idle.png'}}), encoding='utf8')
            found = discover_packs(base, resources)
            self.assertEqual([p['id'] for p in found], ['sena', 'fixture'])
            self.assertEqual(found[0]['root'], internal / 'assets')
            self.assertEqual(found[1]['root'], pack)


class BundleFileTests(unittest.TestCase):
    def test_readable_license_paths_avoid_deep_dist_info_folders(self):
        self.assertEqual(license_destination('charset-normalizer', 'charset_normalizer-3.5.2.dist-info/licenses/LICENSE'),
                         Path('charset-normalizer/LICENSE'))
        self.assertEqual(license_destination('Pillow', 'pillow-12.3.0.dist-info/licenses/LICENSE'),
                         Path('Pillow/LICENSE'))

    def test_onefile_embeds_only_selected_original_assets_and_voices(self):
        selected = embedded_files(source_files())
        expected = {f'assets/{name}' for name in ASSETS}
        expected.update(f'assets/voices/ja/{event}.wav' for event in ('full', 'low', 'empty'))
        self.assertEqual({name for name in selected if name.startswith('assets/')}, expected)
        self.assertIn('VERSION', selected)
        self.assertNotIn('assets/low.png', selected)
        self.assertNotIn('assets/empty.png', selected)
        self.assertFalse(any(name.startswith('packs/') or name.endswith('.py') for name in selected))

    def test_onefile_zip_support_files_are_docs_not_runtime_dependencies(self):
        files = runtime_files(source_files(), onefile=True)
        self.assertIn('INSTALL_KO.md', files)
        self.assertIn('VOICE_CREDITS.md', files)
        self.assertFalse(any(name.startswith('assets/') or name.endswith('.py') for name in files))
        self.assertIn('assets/idle_alt.png', runtime_files(source_files(), onefile=False))


class BundleDiagnosticTests(unittest.TestCase):
    def test_embedded_sena_check_works_without_assets_next_to_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); embedded = root / 'internal'; base = root / 'exe-only'; base.mkdir()
            for name, source in embedded_files(source_files()).items():
                path = embedded / name; path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, path)
            report = root / 'report.json'
            with patch('usage_core.QuotaClient.fetch', side_effect=AssertionError('No account calls')):
                self.assertEqual(check_bundle(base, embedded, report), 0)
            result = json.loads(report.read_text(encoding='utf8'))
            self.assertTrue(result['ok']); self.assertTrue(result['embedded_resources'])
            self.assertFalse(result['external_assets_present']); self.assertFalse(result['account_accessed'])
            self.assertEqual([p['id'] for p in result['packs']], ['sena'])
            self.assertEqual(len(result['packs'][0]['voices']), 3)
            self.assertNotIn(str(root), report.read_text(encoding='utf8'))

    def test_missing_resources_report_failure_without_gui_or_account(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); report = root / 'report.json'
            self.assertEqual(check_bundle(root, root, report), 1)
            result = json.loads(report.read_text(encoding='utf8'))
            self.assertFalse(result['ok']); self.assertFalse(result['account_accessed'])
            self.assertTrue(result['errors'])

    def test_diagnostic_cli_never_constructs_a_window_or_quota_client(self):
        with patch.object(sys, 'argv', ['widget.py', '--bundle-check', 'fixture-report.json']), \
                patch('bundle_check.check_bundle', return_value=0) as check, \
                patch('widget.tk.Tk') as window, patch('widget.QuotaClient') as quota:
            with self.assertRaises(SystemExit) as exit_result:
                widget.main()
        self.assertEqual(exit_result.exception.code, 0)
        window.assert_not_called(); quota.assert_not_called()
        check.assert_called_once_with(widget.BASE, widget.RESOURCES, Path('fixture-report.json'), audio=False)


if __name__ == '__main__': unittest.main()
