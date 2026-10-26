"""Explicit local bundle diagnostic. No GUI, login, model or quota requests."""
import hashlib
import json
from pathlib import Path
import sys
import time
import wave

from PIL import Image
from character_packs import discover_packs, image_path, voice_path, animation_interval
from voice_player import VoicePlayer, scaled_wave


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_bundle(base, resources, report_path, audio=False):
    base, resources = Path(base), Path(resources)
    result = dict(frozen=bool(getattr(sys, 'frozen', False)),
                  embedded_resources=base.resolve() != resources.resolve(),
                  external_assets_present=(base / 'assets').is_dir(),
                  account_accessed=False, settings_accessed=False,
                  gui_interaction_tested=False, audio_requested=bool(audio), packs=[], errors=[])
    try:
        version_path = resources / 'VERSION'
        result['version'] = version_path.read_text(encoding='utf8').strip()
        for pack in discover_packs(base, resources):
            row = dict(id=pack['id'], interval_ms=animation_interval(pack), images={}, voices={})
            for pose in ('idle_alt', 'full', 'low', 'empty', 'frame_1', 'frame_2', 'frame_3'):
                path = image_path(pack, pose)
                with Image.open(path) as image:
                    if image.format != 'PNG' or max(image.size) > 4096:
                        raise ValueError('Unsupported bundled image')
                    image.verify()
                row['images'][pose] = sha(path)
            for event in ('full', 'low', 'empty'):
                path = voice_path(pack, 'ja', event)
                scaled_wave(path, 100)
                with wave.open(str(path), 'rb') as sound:
                    voice = dict(sha256=sha(path), sample_rate=sound.getframerate(),
                                 channels=sound.getnchannels(), bits=sound.getsampwidth() * 8,
                                 seconds=round(sound.getnframes() / sound.getframerate(), 5),
                                 native_playback='not_requested')
                if audio:
                    player = VoicePlayer()
                    if not player.play(path, 10):
                        raise RuntimeError('Diagnostic playback not started')
                    deadline = time.monotonic() + 12
                    while player.busy.locked() and time.monotonic() < deadline:
                        time.sleep(.02)
                    if player.busy.locked():
                        player.stop()
                        raise RuntimeError('Diagnostic playback timed out')
                    if player.error:
                        raise RuntimeError('Diagnostic audio device failure')
                    voice['native_playback'] = 'completed_at_volume_10'
                row['voices'][event] = voice
            result['packs'].append(row)
    except Exception as exc:
        # Never write raw account data, user paths, credentials or exception text.
        result['errors'].append(type(exc).__name__)
    result['ok'] = not result['errors'] and bool(result['packs'])
    Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    return 0 if result['ok'] else 1
