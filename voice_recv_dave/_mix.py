from __future__ import annotations

import array
import logging
import threading
import time
import wave

from discord.ext.voice_recv.sinks import AudioSink
from discord.opus import Decoder

log = logging.getLogger("voice_recv_dave")

_FRAME_BYTES = Decoder.FRAME_SIZE
_CHANNELS = Decoder.CHANNELS
_SAMPWIDTH = Decoder.SAMPLE_SIZE // Decoder.CHANNELS
_RATE = Decoder.SAMPLING_RATE
_FRAME_SECONDS = 0.02
_SILENCE = b"\x00" * _FRAME_BYTES


def _mix(a: bytes, b: bytes) -> bytes:
    left = array.array("h")
    left.frombytes(a)
    right = array.array("h")
    right.frombytes(b)
    for i in range(min(len(left), len(right))):
        s = left[i] + right[i]
        left[i] = 32767 if s > 32767 else (-32768 if s < -32768 else s)
    return left.tobytes()


class TimeMixSink(AudioSink):
    FLUSH_LAG = 150
    RESYNC = 10

    def __init__(self, destination) -> None:
        super().__init__()
        self._file = wave.open(destination, "wb")
        self._file.setnchannels(_CHANNELS)
        self._file.setsampwidth(_SAMPWIDTH)
        self._file.setframerate(_RATE)
        self._t0: float | None = None
        self._written = 0
        self._last_voice = -1
        self._voiced = 0
        self._pending: dict[int, bytes] = {}
        self._next: dict = {}
        self._lock = threading.Lock()
        self.closed = threading.Event()

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data) -> None:
        pcm = getattr(data, "pcm", None)
        if not pcm:
            return
        if len(pcm) != _FRAME_BYTES:
            pcm = (pcm + _SILENCE)[:_FRAME_BYTES]
        now = time.perf_counter()
        voiced = pcm != _SILENCE
        speaker = getattr(getattr(data, "packet", None), "ssrc", None) or getattr(user, "id", None)
        with self._lock:
            if self.closed.is_set():
                return
            if voiced:
                self._voiced += 1
            if self._t0 is None:
                self._t0 = now
            arrived = int((now - self._t0) / _FRAME_SECONDS)
            slot = arrived
            following = self._next.get(speaker)
            if following is not None and arrived <= following + self.RESYNC:
                slot = following
            if slot < self._written:
                slot = self._written
            if speaker is not None:
                self._next[speaker] = slot + 1
            previous = self._pending.get(slot)
            self._pending[slot] = pcm if previous is None else _mix(previous, pcm)
            if slot > self._last_voice:
                self._last_voice = slot
            self._flush_locked(arrived - self.FLUSH_LAG)

    def _flush_locked(self, up_to: int) -> None:
        while self._written <= up_to:
            self._file.writeframes(self._pending.pop(self._written, _SILENCE))
            self._written += 1

    def stats(self) -> dict:
        with self._lock:
            return {
                "seconds": self._written * _FRAME_SECONDS,
                "frames_written": self._written,
                "voiced_frames": self._voiced,
                "voiced_seconds": self._voiced * _FRAME_SECONDS,
            }

    def cleanup(self) -> None:
        with self._lock:
            if self.closed.is_set():
                return
            try:
                if self._t0 is not None and self._last_voice >= self._written:
                    self._flush_locked(self._last_voice)
            except Exception:
                log.warning("TimeMixSink: final flush failed", exc_info=True)
            finally:
                try:
                    self._file.close()
                except Exception:
                    log.warning("TimeMixSink: close failed", exc_info=True)
                self.closed.set()
