"""Offline PCM WAV playback. No TTS, external processes, or system volume changes."""
import array
import io
from pathlib import Path
import sys
import threading
import time
import wave
from wave_output import WaveOutput


def scaled_wave(path, volume):
    path = Path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Voice exceeds 2 MiB")
    with wave.open(str(path), "rb") as sound:
        params = sound.getparams()
        if (params.comptype != "NONE" or params.sampwidth != 2 or params.nchannels not in (1, 2)
                or not 8000 <= params.framerate <= 48000
                or not 0 < params.nframes <= params.framerate * 8):
            raise ValueError("Use 16-bit PCM WAV, mono/stereo, 8-48 kHz, up to 8 seconds")
        data = sound.readframes(params.nframes)
        if len(data) != params.nframes * params.nchannels * 2:
            raise ValueError("Truncated WAV")
    samples = array.array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    factor = max(0, min(100, int(volume))) / 100
    for index, sample in enumerate(samples):
        samples[index] = int(sample * factor)
    if sys.byteorder != "little":
        samples.byteswap()
    output = io.BytesIO()
    with wave.open(output, "wb") as sound:
        sound.setparams(params)
        sound.writeframes(samples.tobytes())
    return output.getvalue()


class VoicePlayer:
    def __init__(self):
        self.busy = threading.Lock()
        self.cancelled = threading.Event()
        self.state_lock = threading.Lock()
        self.output = None
        self.error = ""
        self.error_detail = ""

    def play(self, path, volume):
        if int(volume) <= 0 or not self.busy.acquire(blocking=False):
            return False
        if self.output is not None:
            # Keep failed driver-owned buffers alive; fail closed until restart.
            self.busy.release()
            return False
        self.cancelled.clear()
        self.error = ""
        self.error_detail = ""

        def worker():
            try:
                audio = scaled_wave(path, volume)
                # stop() and start are atomic with respect to each other. A
                # cancelled, still-decoding clip cannot start after stop returns.
                with self.state_lock:
                    if self.cancelled.is_set():
                        return
                    self.output = WaveOutput(audio)
                    self.output.start()
                deadline = time.monotonic() + 10
                while not self.cancelled.wait(.01) and not self.output.done():
                    if time.monotonic() > deadline:
                        raise OSError('Audio device timeout')
            except Exception as exc:
                # Frozen DLL loader failures are ImportError, not always OSError.
                # Keep callbacks alive and expose a non-sensitive diagnostic type.
                if not self.cancelled.is_set():
                    self.error = "voice_unavailable"
                    self.error_detail = type(exc).__name__
            finally:
                with self.state_lock:
                    if self.output is not None:
                        try:
                            self.output.close()
                        except OSError:
                            self.error = 'voice_unavailable'
                        else:
                            self.output = None
                self.busy.release()

        threading.Thread(target=worker, daemon=True).start()
        return True

    def stop(self):
        with self.state_lock:
            self.cancelled.set()
            if self.output is not None:
                try:
                    self.output.reset()
                except OSError:
                    self.error = 'voice_unavailable'
