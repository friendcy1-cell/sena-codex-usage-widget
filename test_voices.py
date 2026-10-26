"""Audio integration/GUI-handler tests, without operating a desktop or account."""
import array
import hashlib
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import wave

from character_packs import animation_interval, discover_packs, image_path, voice_path
from make_release import ROOT, optional_pack_files, source_files
from presentation import TransitionTracker, character_motion
from test_features import snapshot
from voice_player import VoicePlayer, scaled_wave
from widget import Widget, read_preferences


class FakeOutput:
    def __init__(self, audio):
        self.finished = threading.Event()
        self.closed = False
    def start(self): pass
    def done(self): return self.finished.is_set()
    def reset(self): self.finished.set()
    def close(self): self.closed = True


def wait_idle(player):
    deadline = time.monotonic() + 2
    while player.busy.locked() and time.monotonic() < deadline:
        time.sleep(.005)
    if player.busy.locked():
        raise AssertionError('Audio worker did not finish')


class PlaybackTests(unittest.TestCase):
    def test_overlap_dropped_and_stop_cleans_private_handle(self):
        backend = FakeOutput(b'')
        with patch('voice_player.scaled_wave', return_value=b'fixture'), patch('voice_player.WaveOutput', return_value=backend):
            player = VoicePlayer()
            self.assertFalse(player.play('fixture', 0))
            self.assertTrue(player.play('fixture', 50))
            self.assertFalse(player.play('fixture', 50))
            player.stop()
            wait_idle(player)
            self.assertEqual(player.error, '')
            self.assertIsNone(player.output)

    def test_stop_during_decode_cannot_start_stale_voice(self):
        entered, release = threading.Event(), threading.Event()
        def decode(*args):
            entered.set()
            release.wait(2)
            return b'fixture'
        with patch('voice_player.scaled_wave', side_effect=decode), patch('voice_player.WaveOutput') as backend:
            player = VoicePlayer()
            player.play('fixture', 50)
            self.assertTrue(entered.wait(1))
            player.stop()
            release.set()
            wait_idle(player)
            backend.assert_not_called()

    def test_natural_finish_allows_next_clip_and_error_is_nonfatal(self):
        backend = FakeOutput(b''); backend.finished.set()
        with patch('voice_player.scaled_wave', return_value=b'fixture'), patch('voice_player.WaveOutput', return_value=backend):
            player = VoicePlayer()
            self.assertTrue(player.play('fixture', 50)); wait_idle(player)
            self.assertTrue(backend.closed)
            self.assertTrue(player.play('fixture', 50)); wait_idle(player)
        with patch('voice_player.scaled_wave', side_effect=OSError('missing')):
            self.assertTrue(player.play('fixture', 50)); wait_idle(player)
            self.assertEqual(player.error, 'voice_unavailable')

    def test_mute_stops_without_replaying(self):
        app = Widget.__new__(Widget)
        app.prefs = {'volume': 50}
        app.voices, app.store_preferences = MagicMock(), MagicMock()
        app.update_details = MagicMock()
        app.volume_changed('0')
        self.assertEqual(app.prefs['volume'], 0)
        app.voices.stop.assert_called_once()
        app.voices.play.assert_not_called()

    def test_packaged_backend_import_failure_is_reported_not_hidden(self):
        with patch('voice_player.scaled_wave', return_value=b'fixture'), patch('voice_player.WaveOutput', side_effect=ImportError('fixture loader failure')):
            player = VoicePlayer()
            player.play('fixture', 50); wait_idle(player)
            self.assertEqual(player.error, 'voice_unavailable')
            self.assertIn('ImportError', player.error_detail)


class HandlerTests(unittest.TestCase):
    def app(self):
        app = Widget.__new__(Widget)
        app.preview = False
        app.prefs = {'voice_enabled': True, 'language': 'en'}
        app.transitions = TransitionTracker()
        app.notified = set()
        app.is_fresh = MagicMock(return_value=True)
        app.notify, app.play_voice = MagicMock(), MagicMock()
        return app

    def test_live_threshold_callbacks_once_not_per_poll(self):
        app = self.app()
        for index, value in enumerate((100, 100, 40, 20, 20, 0, 0, 100, 100)):
            app.snapshot = snapshot(value, 100 + index)
            app.check_alerts(None)
        self.assertEqual([c.args[0] for c in app.play_voice.call_args_list], ['low', 'empty', 'full'])

    def test_unknown_breaks_baseline_and_no_recovery_sound(self):
        app = self.app()
        app.snapshot = snapshot(40); app.check_alerts(None)
        app.is_fresh.return_value = False
        app.check_alerts(None)
        app.is_fresh.return_value = True
        app.snapshot = snapshot(100, 110); app.check_alerts(None)
        app.play_voice.assert_not_called()

    def test_preview_and_muted_do_not_automatically_play(self):
        for preview, enabled in ((True, True), (False, False)):
            app = self.app(); app.preview = preview; app.prefs['voice_enabled'] = enabled
            for index, value in enumerate((40, 20, 0, 100)):
                app.snapshot = snapshot(value, 100 + index); app.check_alerts(None)
            app.play_voice.assert_not_called()

    def test_manual_test_routes_all_three_events_and_preview_pose(self):
        for index, event in enumerate(('full', 'low', 'empty')):
            app = self.app(); app.preview = True
            app.voice_test_box = MagicMock(); app.voice_test_box.current.return_value = index
            app.demo_box, app.demo_changed, app.update_details = MagicMock(), MagicMock(), MagicMock()
            app.test_voice()
            app.play_voice.assert_called_once_with(event)
            app.demo_box.current.assert_called_once_with(index + 1)

    def test_explicit_voice_test_works_with_automatic_alerts_off(self):
        app = self.app(); app.prefs['voice_enabled'] = False
        app.voice_test_box, app.update_details = MagicMock(), MagicMock()
        app.voice_test_box.current.return_value = 1
        app.test_voice()
        app.play_voice.assert_called_once_with('low')

    def test_preferences_callback_keeps_test_button_enabled(self):
        app = self.app()
        app.vars = {'voice_enabled': MagicMock()}; app.vars['voice_enabled'].get.return_value = False
        app.interval, app.display_box, app.volume = MagicMock(), MagicMock(), MagicMock()
        app.interval.current.return_value = 1; app.display_box.current.return_value = 0
        app.volume.get.return_value = 50
        app.prefs['topmost'] = True
        app.voices, app.voice_test_button = MagicMock(), MagicMock()
        app.root, app.panel = MagicMock(), MagicMock()
        app.last_attempt = 0
        app.store_preferences, app.draw, app.update_details = MagicMock(), MagicMock(), MagicMock()
        app.options_changed()
        app.voice_test_button.configure.assert_called_once_with(state='normal')
        app.voices.stop.assert_called_once()

    def test_original_recovery_reaction_lasts_15_seconds_without_extra_voice(self):
        app = self.app()
        previous = snapshot(40)
        app.snapshot = snapshot(60, 110)
        with patch('widget.time.time', return_value=200):
            app.check_alerts(previous)
        self.assertEqual(app.success_until, 215)
        app.play_voice.assert_not_called()

    def test_preview_recovery_has_original_temporary_success_pose(self):
        app = self.app(); app.preview = True
        app.snapshot = snapshot(40)
        app.demo_box, app.draw, app.update_details = MagicMock(), MagicMock(), MagicMock()
        app.demo_box.get.return_value = 'full'
        with patch('widget.time.time', return_value=200):
            app.demo_changed()
        self.assertEqual(app.success_until, 215)
        self.assertEqual(character_motion('full', True, 1, 214 < app.success_until)[0], 'full')
        self.assertEqual(character_motion('full', True, 1, 216 < app.success_until)[0], 'frame_2')
        app.play_voice.assert_not_called()

    def test_character_change_stops_old_voice_preserves_settings(self):
        app = self.app()
        app.prefs.update(volume=37, interval=300, x=-120, animation=True)
        app.packs = [{'id': 'sena'}, {'id': 'rina'}, {'id': 'nari'}]
        app.character_box = MagicMock(); app.character_box.current.return_value = 2
        app.voices = MagicMock()
        for name in ('options_changed', 'load_character', 'store_preferences', 'draw', 'update_details'):
            setattr(app, name, MagicMock())
        app.character_changed()
        self.assertEqual(app.prefs['character'], 'nari')
        self.assertEqual((app.prefs['volume'], app.prefs['interval'], app.prefs['x']), (37, 300, -120))
        app.voices.stop.assert_called_once(); app.play_voice.assert_not_called()


class MotionTests(unittest.TestCase):
    def test_normal_cycles_three_distinct_character_frames(self):
        poses = [character_motion('normal', True, frame)[0] for frame in range(6)]
        self.assertEqual(poses, ['frame_1', 'frame_2', 'frame_3'] * 2)

    def test_original_pose_priority_without_artificial_bobbing(self):
        names = {'idle_alt': 'idle_alt', 'full': 'success', 'low': 'fail', 'empty': 'fail',
                 **{f'frame_{i}': f'frame_{i}' for i in (1, 2, 3)}}
        for state, remaining, fresh in (('normal', 50, True), ('full', 100, True),
                                         ('low', 20, True), ('empty', 0, True), ('unknown', 50, False)):
            for enabled in (False, True):
                for recovering in (False, True):
                    for frame in range(3):
                        # Exact priority of the preserved original Sena draw routine.
                        expected = 'idle_alt'
                        if fresh and remaining <= 20: expected = 'fail'
                        elif fresh and recovering: expected = 'success'
                        elif enabled and fresh: expected = f'frame_{frame + 1}'
                        pose, offset = character_motion(state, enabled, frame, recovering)
                        self.assertEqual((names[pose], offset), (expected, 0))

    def test_explicit_motion_off_is_fully_static(self):
        for state in ('normal', 'full', 'low', 'empty', 'unknown'):
            self.assertEqual(len({character_motion(state, False, frame) for frame in range(12)}), 1)

    def test_actual_draw_restores_original_frames_and_fixed_anchor(self):
        app = Widget.__new__(Widget)
        app.canvas = MagicMock()
        app.images = {name: name for name in ('idle_alt', 'full', 'low', 'empty', 'frame_1', 'frame_2', 'frame_3')}
        app.prefs = {'animation': True, 'language': 'en'}
        app.busy = app.preview = False
        for state, remaining in (('normal', 50), ('full', 100), ('low', 20), ('empty', 0), ('unknown', 50)):
            app.snapshot = snapshot(remaining, time.time())
            app.is_fresh = lambda: state != 'unknown'
            positions = []
            images = []
            for frame in range(3):
                app.frame_index = frame; app.draw()
                positions.append(app.canvas.create_image.call_args.args)
                images.append(app.canvas.create_image.call_args.kwargs['image'])
            self.assertEqual(set(positions), {(108, 156)})
            if state in ('normal', 'full'):
                self.assertEqual(images, ['frame_1', 'frame_2', 'frame_3'])
            else:
                self.assertEqual(images, [state if state != 'unknown' else 'idle_alt'] * 3)

    def test_original_timer_and_frame_cycle_for_all_three_characters(self):
        for character in ('sena', 'rina', 'nari'):
            app = Widget.__new__(Widget)
            app.closed = False; app.prefs = {'animation': True}; app.frame_index = 0
            app.pack = {'id': character}
            app.draw, app.root = MagicMock(), MagicMock()
            indices = []
            for _ in range(3):
                app.animate(); indices.append(app.frame_index)
            self.assertEqual(indices, [1, 2, 0])
            self.assertEqual([c.args[0] for c in app.root.after.call_args_list], [750, 750, 750])


class DeliveredAssetTests(unittest.TestCase):
    def test_all_delivered_voices_validate_and_characters_do_not_share_audio(self):
        hashes = set()
        packs = discover_packs(ROOT, ROOT)
        self.assertIn('sena', [p['id'] for p in packs])
        for pack in packs:
            if pack['id'] not in ('sena', 'rina', 'nari'): continue
            for event in ('full', 'low', 'empty'):
                path = voice_path(pack, 'ja', event)
                original = path.read_bytes()
                hashes.add(hashlib.sha256(original).hexdigest())
                with wave.open(str(path), 'rb') as sound:
                    self.assertEqual((sound.getsampwidth(), sound.getframerate(), sound.getnchannels()), (2, 48000, 1))
                    self.assertTrue(3.5 < sound.getnframes() / 48000 < 4.6)
                for volume in (0, 20, 50, 100):
                    with wave.open(io.BytesIO(scaled_wave(path, volume)), 'rb') as sound:
                        samples = array.array('h', sound.readframes(sound.getnframes()))
                        self.assertLess(max(map(abs, samples)), 32767)
                        if volume == 0: self.assertEqual(max(map(abs, samples)), 0)
                self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(hashes), len(packs) * 3)

    def test_matching_images_and_same_original_cadence(self):
        from PIL import Image
        packs = discover_packs(ROOT, ROOT)
        for pack in packs:
            for pose in ('idle_alt', 'full', 'low', 'empty', 'frame_1', 'frame_2', 'frame_3'):
                with Image.open(image_path(pack, pose)) as image:
                    self.assertEqual(image.format, 'PNG')
                    image.verify()
        rates = {p['id']: animation_interval(p) for p in packs}
        self.assertTrue(all(value == 750 for value in rates.values()))

    def test_original_training_outfit_reaction_mapping_for_every_character(self):
        for pack in discover_packs(ROOT, ROOT):
            if pack['id'] not in ('sena', 'rina', 'nari'): continue
            self.assertEqual(image_path(pack, 'full').name, 'success.png')
            self.assertEqual(image_path(pack, 'low').name, 'fail.png')
            self.assertEqual(image_path(pack, 'empty').name, 'fail.png')
            self.assertNotEqual(image_path(pack, 'idle_alt').read_bytes(), image_path(pack, 'low').read_bytes())
            self.assertEqual(image_path(pack, 'low').read_bytes(), image_path(pack, 'empty').read_bytes())

    def test_base_archives_exclude_optional_packs_and_pack_archives_are_isolated(self):
        self.assertFalse(any(p.startswith('packs/') for p in source_files()))
        self.assertNotIn('assets/low.png', source_files())
        self.assertNotIn('assets/empty.png', source_files())
        for pack_id in ('rina', 'nari'):
            if not (ROOT / 'packs' / pack_id).is_dir(): continue
            files = optional_pack_files(pack_id)
            self.assertEqual(sum(p.endswith('.wav') for p in files), 3)
            self.assertFalse(any(p.startswith('assets/') for p in files))
            self.assertFalse(any(p.startswith('packs/') and not p.startswith(f'packs/{pack_id}/') for p in files))
            self.assertFalse(any(p.endswith('/low.png') or p.endswith('/empty.png') for p in files))


if __name__ == '__main__':
    unittest.main()
