import array
import copy
import io
import json
from pathlib import Path
import string
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
import wave

from character_packs import discover_packs, image_path, voice_path
from i18n import CATALOGS, EN, LANGUAGES, tr
from presentation import TransitionTracker, display_windows, quota_state
from voice_player import scaled_wave
from widget import read_preferences
from usage_core import UsageError, QuotaClient, account_marker, normalize


def snapshot(remaining, at=100, secondary=None):
    value = {'account': 'synthetic', 'plan': 'plus', 'at': at, 'windows': [
        {'slot': 'primary', 'remaining': remaining, 'minutes': 300, 'reset': 5000}]}
    if secondary is not None:
        value['windows'].append({'slot': 'secondary', 'remaining': secondary, 'minutes': 10080, 'reset': 90000})
    return value


class DisplayTests(unittest.TestCase):
    def test_account_marker_works_when_quota_response_omits_account_id(self):
        a = account_marker({'type': 'chatgpt', 'email': 'fixture-a@example.test'})
        b = account_marker({'type': 'chatgpt', 'email': 'fixture-b@example.test'})
        self.assertNotEqual(a, b)
        for value in (None, {}, {'type': 'apiKey'}):
            with self.assertRaises(UsageError): account_marker(value)

    def test_modes_use_real_windows_only(self):
        value = snapshot(80, secondary=30)
        self.assertEqual(display_windows(value)[0]['remaining'], 30)
        self.assertEqual(display_windows(value, 'five_hour')[0]['remaining'], 80)
        self.assertEqual(display_windows(value, 'weekly')[0]['remaining'], 30)
        self.assertEqual(len(display_windows(value, 'both')), 2)
        value['windows'] = value['windows'][1:]
        self.assertEqual(display_windows(value, 'five_hour'), [])
        self.assertEqual(quota_state([], True), 'unknown')

    def test_unknown_never_means_zero(self):
        for remaining, expected in [(100, 'full'), (21, 'normal'), (20, 'low'), (1, 'low'), (0, 'empty')]:
            windows = snapshot(remaining)['windows']
            self.assertEqual(quota_state(windows, True), expected)
            self.assertEqual(quota_state(windows, False), 'unknown')

    def test_new_defaults_english_voice_motion_on_explicit_old_settings_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            self.assertEqual(read_preferences(path)['language'], 'en')
            self.assertTrue(read_preferences(path)['voice_enabled'])
            self.assertTrue(read_preferences(path)['animation'])
            path.write_text('{"x":-400,"y":200,"interval":300,"animation":true,"voice_enabled":false}', encoding='utf8')
            prefs = read_preferences(path)
            self.assertEqual((prefs['language'], prefs['x'], prefs['interval'], prefs['animation']), ('ko', -400, 300, True))
            self.assertFalse(prefs['voice_enabled'])

    def test_settings_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            path.write_text(json.dumps({'language': 'bad', 'display': 'bad', 'volume': True,
                                        'voice_enabled': 'true', 'character': '../other'}), encoding='utf8')
            prefs = read_preferences(path)
            self.assertEqual((prefs['language'], prefs['display'], prefs['volume'], prefs['character']), ('ko', 'auto', 50, 'sena'))
            self.assertTrue(prefs['voice_enabled'])


class TranslationTests(unittest.TestCase):
    def test_languages_have_identical_keys_and_placeholders(self):
        fields = lambda text: {name for _, name, _, _ in string.Formatter().parse(text) if name}
        for language in LANGUAGES:
            self.assertEqual(set(CATALOGS[language]), set(EN))
            for key in EN:
                self.assertEqual(fields(CATALOGS[language][key]), fields(EN[key]), (language, key))
                self.assertTrue(tr(language, key, **{f: 'test' for f in fields(EN[key])}))


class RpcTests(unittest.TestCase):
    def test_only_account_reads_and_identity_is_not_in_saved_snapshot(self):
        class CapturedInput(io.BytesIO):
            def close(self):
                self.sent = self.getvalue()
                super().close()
        process = MagicMock()
        process.stdin = CapturedInput()
        quota = {'rateLimitsByLimitId': {'codex': {'planType': 'plus', 'primary': {
            'usedPercent': 10, 'windowDurationMins': 300, 'resetsAt': 5000}}}}
        replies = [ {'id': 1, 'result': {}},
                    {'id': 2, 'result': {'account': {'type': 'chatgpt', 'email': 'synthetic@example.test'}}},
                    {'id': 3, 'result': quota} ]
        process.stdout = io.BytesIO(b''.join(json.dumps(r).encode() + b'\n' for r in replies))
        process.poll.return_value = None
        with patch('usage_core.codex_path', return_value='codex-fixture'), patch('usage_core.subprocess.Popen', return_value=process) as launch:
            value = QuotaClient().fetch()
        methods = [json.loads(line)['method'] for line in process.stdin.sent.splitlines()]
        self.assertEqual(methods, ['initialize', 'initialized', 'account/read', 'account/rateLimits/read'])
        self.assertNotIn('synthetic@example.test', json.dumps(value))
        self.assertEqual(value['windows'][0]['remaining'], 90)
        self.assertEqual(launch.call_args.args[0], ['codex-fixture', 'app-server', '--listen', 'stdio://'])


class TransitionTests(unittest.TestCase):
    def test_first_sample_and_repeated_reads_are_silent(self):
        for value in (100, 20, 0):
            tracker = TransitionTracker()
            self.assertIsNone(tracker.update(snapshot(value)))
            self.assertIsNone(tracker.update(snapshot(value, 110)))

    def test_each_transition_once_and_zero_takes_priority(self):
        tracker = TransitionTracker()
        events = [tracker.update(snapshot(value, 100 + index)) for index, value in enumerate((40, 20, 20, 0, 0, 100, 100))]
        self.assertEqual(events, [None, 'low', None, 'empty', None, 'full', None])
        tracker = TransitionTracker()
        tracker.update(snapshot(30, secondary=30))
        self.assertEqual(tracker.update(snapshot(20, 110, secondary=0)), 'empty')

    def test_low_rearms_after_recovery(self):
        tracker = TransitionTracker()
        values = [tracker.update(snapshot(value, 100 + index)) for index, value in enumerate((30, 20, 30, 20))]
        self.assertEqual(values, [None, 'low', None, 'low'])

    def test_failure_account_plan_and_gap_are_silent(self):
        for change in ('failure', 'account', 'plan', 'gap', 'shape'):
            tracker = TransitionTracker()
            tracker.update(snapshot(40))
            value = snapshot(0, 110)
            if change == 'failure': tracker.update(None)
            if change == 'account': value['account'] = 'different'
            if change == 'plan': value['plan'] = 'pro'
            if change == 'gap': value['at'] = 1100
            if change == 'shape': value['windows'][0]['minutes'] = 10080
            self.assertIsNone(tracker.update(value), change)

    def test_full_requires_all_limits_and_cycle_changes_are_not_low(self):
        tracker = TransitionTracker()
        tracker.update(snapshot(30, secondary=60))
        self.assertIsNone(tracker.update(snapshot(100, 110, secondary=60)))
        self.assertEqual(tracker.update(snapshot(100, 120, secondary=100)), 'full')
        value = snapshot(20, 130, secondary=100)
        value['windows'][0]['reset'] += 1
        self.assertIsNone(tracker.update(value))


class PackTests(unittest.TestCase):
    def test_builtin_and_optional_pack_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            child = base / 'packs' / 'fixture'
            child.mkdir(parents=True)
            (child / 'idle.png').write_bytes(b'fixture-only')
            manifest = {'version': 1, 'name': 'Fixture', 'images': {'idle_alt': 'idle.png'}}
            (child / 'pack.json').write_text(json.dumps(manifest), encoding='utf8')
            packs = discover_packs(base, base)
            self.assertEqual([p['id'] for p in packs], ['sena', 'fixture'])
            self.assertEqual(image_path(packs[1], 'empty').name, 'idle.png')
            manifest['images']['empty'] = '../../private.png'
            (child / 'pack.json').write_text(json.dumps(manifest), encoding='utf8')
            self.assertEqual(len(discover_packs(base, base)), 1)

    def test_executable_and_missing_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            child = base / 'packs' / 'bad'
            child.mkdir(parents=True)
            (child / 'pack.json').write_text('{"version":1,"name":"Bad","images":{},"script":"evil.py"}', encoding='utf8')
            self.assertEqual(len(discover_packs(base, base)), 1)

    def test_only_japanese_voices_selected_by_app(self):
        source = Path('widget.py').read_text(encoding='utf8')
        self.assertIn("voice_path(self.pack, 'ja', event)", source)
        self.assertNotIn('import pyttsx3', source)


class WaveTests(unittest.TestCase):
    def wav(self, path, channels=1, width=2, duration=0.01):
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(channels); stream.setsampwidth(width); stream.setframerate(16000)
            data = array.array('h', [10000, -10000] * int(16000 * duration * channels / 2))
            stream.writeframes(data.tobytes())

    def test_volume_scales_pcm_in_memory_without_changing_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'full.wav'
            self.wav(path)
            original = path.read_bytes()
            for volume, expected in ((0, 0), (50, 5000), (100, 10000)):
                with wave.open(io.BytesIO(scaled_wave(path, volume)), 'rb') as sound:
                    samples = array.array('h'); samples.frombytes(sound.readframes(1))
                    self.assertEqual(samples[0], expected)
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_missing_and_long_recordings_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.wav'
            for options in ({'duration': 9}, {'width': 1}):
                self.wav(path, **options)
                with self.assertRaises(ValueError): scaled_wave(path, 50)
            path.write_bytes(b'not wav')
            with self.assertRaises((wave.Error, EOFError)): scaled_wave(path, 50)


if __name__ == '__main__':
    unittest.main()
