from __future__ import annotations

import array
import io
import unittest
import wave
from types import SimpleNamespace
from unittest import mock

from voice_recv_dave import TimeMixSink, _mix

SAMPLES = 1920


def frame(value: int) -> bytes:
    return array.array("h", [value] * SAMPLES).tobytes()


def voice(ssrc: int, value: int):
    return SimpleNamespace(pcm=frame(value), packet=SimpleNamespace(ssrc=ssrc))


class MixTest(unittest.TestCase):
    def setUp(self):
        self.clock = [0.0]
        patcher = mock.patch.object(_mix, "time", SimpleNamespace(perf_counter=lambda: self.clock[0]))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.buffer = io.BytesIO()
        self.sink = TimeMixSink(self.buffer)

    def at(self, seconds: float, ssrc: int, value: int) -> None:
        self.clock[0] = seconds
        self.sink.write(None, voice(ssrc, value))

    def frames(self) -> list[int]:
        self.sink.cleanup()
        self.buffer.seek(0)
        with wave.open(self.buffer, "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (2, 2, 48000))
            samples = array.array("h")
            samples.frombytes(wav.readframes(wav.getnframes()))
        return [samples[i] for i in range(0, len(samples), SAMPLES)]

    def test_two_speakers_at_once_are_mixed(self):
        self.at(0.0, 1, 1000)
        self.at(0.0, 2, 2000)
        self.assertEqual(self.frames(), [3000])

    def test_one_speakers_burst_stays_in_order(self):
        for value in (1, 2, 3):
            self.at(0.0, 1, value)
        self.assertEqual(self.frames(), [1, 2, 3])

    def test_a_pause_keeps_real_time(self):
        self.at(0.0, 1, 5)
        self.at(1.0, 1, 6)
        out = self.frames()
        self.assertEqual(len(out), 51)
        self.assertEqual((out[0], out[-1]), (5, 6))
        self.assertEqual(set(out[1:-1]), {0})

    def test_loud_overlap_is_clipped_not_wrapped(self):
        self.at(0.0, 1, 30000)
        self.at(0.0, 2, 30000)
        self.assertEqual(self.frames(), [32767])

    def test_stats_and_closed_flag(self):
        self.at(0.0, 1, 7)
        self.at(0.0, 2, 0)
        self.sink.cleanup()
        self.assertTrue(self.sink.closed.is_set())
        stats = self.sink.stats()
        self.assertEqual(stats["voiced_frames"], 1)
        self.assertEqual(stats["frames_written"], 1)
        self.sink.cleanup()
        self.at(0.1, 1, 9)
        self.assertEqual(self.sink.stats()["frames_written"], 1)


if __name__ == "__main__":
    unittest.main()
