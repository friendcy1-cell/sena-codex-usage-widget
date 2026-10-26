"""One private Windows PCM output handle. No system-volume changes or temp files.

waveOutWrite/Reset: learn.microsoft.com/windows/win32/api/mmeapi/
The caller serializes start/reset/close; audio memory stays alive until unprepared.
"""
import ctypes as c
import io
import time
import wave


class WaveFormat(c.Structure):
    _pack_ = 2
    _fields_ = [('tag', c.c_uint16), ('channels', c.c_uint16), ('rate', c.c_uint32),
                ('bytes_per_sec', c.c_uint32), ('align', c.c_uint16),
                ('bits', c.c_uint16), ('extra', c.c_uint16)]


class WaveHeader(c.Structure):
    _fields_ = [('data', c.c_void_p), ('length', c.c_uint32), ('recorded', c.c_uint32),
                ('user', c.c_size_t), ('flags', c.c_uint32), ('loops', c.c_uint32),
                ('next', c.c_void_p), ('reserved', c.c_size_t)]


class WaveOutput:
    def __init__(self, audio):
        self.api = c.WinDLL('winmm')
        self.api.waveOutOpen.argtypes = [c.POINTER(c.c_void_p), c.c_uint32,
                                         c.POINTER(WaveFormat), c.c_size_t, c.c_size_t, c.c_uint32]
        for name in ('waveOutPrepareHeader', 'waveOutWrite', 'waveOutUnprepareHeader'):
            getattr(self.api, name).argtypes = [c.c_void_p, c.POINTER(WaveHeader), c.c_uint32]
        for name in ('waveOutReset', 'waveOutClose'):
            getattr(self.api, name).argtypes = [c.c_void_p]
        for name in ('waveOutOpen', 'waveOutPrepareHeader', 'waveOutWrite',
                     'waveOutUnprepareHeader', 'waveOutReset', 'waveOutClose'):
            getattr(self.api, name).restype = c.c_uint32
        with wave.open(io.BytesIO(audio), 'rb') as sound:
            channels, rate = sound.getnchannels(), sound.getframerate()
            pcm = sound.readframes(sound.getnframes())
        self.format = WaveFormat(1, channels, rate, rate * channels * 2, channels * 2, 16, 0)
        self.buffer = c.create_string_buffer(pcm)
        self.header = WaveHeader(c.cast(self.buffer, c.c_void_p), len(pcm))
        self.handle = c.c_void_p()
        self.prepared = False

    @staticmethod
    def checked(result):
        if result:
            raise OSError(f'Windows audio error {result}')

    def start(self):
        self.checked(self.api.waveOutOpen(c.byref(self.handle), 0xffffffff,
                                          c.byref(self.format), 0, 0, 0))
        self.checked(self.api.waveOutPrepareHeader(self.handle, c.byref(self.header), c.sizeof(self.header)))
        self.prepared = True
        self.checked(self.api.waveOutWrite(self.handle, c.byref(self.header), c.sizeof(self.header)))

    def done(self):
        return bool(self.header.flags & 1)  # WHDR_DONE

    def reset(self):
        if self.handle.value:
            self.checked(self.api.waveOutReset(self.handle))

    def close(self):
        if not self.handle.value:
            return
        self.reset()
        if self.prepared:
            # Reset returns queued buffers. Retry transient STILLPLAYING without
            # freeing memory that a driver may still own.
            deadline = time.monotonic() + .5
            while True:
                result = self.api.waveOutUnprepareHeader(self.handle, c.byref(self.header), c.sizeof(self.header))
                if result != 33:  # WAVERR_STILLPLAYING
                    self.checked(result)
                    break
                if time.monotonic() >= deadline:
                    raise OSError('Audio buffer is still owned by the device')
                time.sleep(.01)
            self.prepared = False
        self.checked(self.api.waveOutClose(self.handle))
        self.handle = c.c_void_p()
