from __future__ import annotations

import os
import unittest

import davey
from discord.ext.voice_recv.reader import AudioReader
from discord.ext.voice_recv.rtp import OPUS_SILENCE
from discord.ext.voice_recv.sinks import BasicSink

import voice_recv_dave
from voice_recv_dave._patch import _readers
from _util import FakeSession, FakeVoiceClient, rtp_packet


def setUpModule():
    assert voice_recv_dave.install()


class DecryptTest(unittest.TestCase):
    def setUp(self):
        self.key = os.urandom(32)
        self.session = FakeSession()
        self.vc = FakeVoiceClient(self.key, self.session, users={7: 42})
        self.reader = AudioReader(BasicSink(lambda user, data: None), self.vc)

    def decrypt(self, payload: bytes, *, key: bytes | None = None, **kwargs) -> bytes:
        packet = rtp_packet(key or self.key, payload, **kwargs)
        return self.reader.decryptor.decrypt_rtp(packet)

    def counters(self) -> dict:
        return _readers[self.reader]

    def test_install_is_idempotent(self):
        init = AudioReader.__init__
        self.assertTrue(voice_recv_dave.install())
        self.assertIs(AudioReader.__init__, init)
        self.assertIn(self.reader, _readers)

    def test_e2ee_frame_is_decrypted_with_the_sender_id(self):
        self.assertEqual(self.decrypt(b"DAVE-frame-1"), b"OPUS-frame-1")
        self.assertEqual(self.session.seen, [(42, b"DAVE-frame-1")])
        self.assertEqual(self.counters()["ok"], 1)

    def test_plain_call_is_left_alone(self):
        self.vc._connection.dave_session = None
        self.assertEqual(self.decrypt(b"plain-opus"), b"plain-opus")
        self.vc._connection.dave_session = self.session
        self.vc._connection.dave_protocol_version = 0
        self.assertEqual(self.decrypt(b"plain-opus"), b"plain-opus")
        self.assertEqual(self.session.seen, [])
        self.assertEqual(self.counters()["bypass"], 2)

    def test_session_not_ready_is_left_alone(self):
        self.session.ready = False
        self.assertEqual(self.decrypt(b"DAVE-x"), b"DAVE-x")
        self.assertEqual(self.session.seen, [])

    def test_unencrypted_frame_passes_through(self):
        self.assertEqual(self.decrypt(b"PLAIN-opus"), b"PLAIN-opus")
        self.assertEqual(self.counters()["passthru"], 1)

    def test_failed_frame_becomes_silence(self):
        self.assertEqual(self.decrypt(b"BROKEN-frame"), OPUS_SILENCE)
        self.assertEqual(self.counters()["fail"], 1)

    def test_unknown_speaker_becomes_silence(self):
        self.assertEqual(self.decrypt(b"DAVE-x", ssrc=99), OPUS_SILENCE)
        self.assertEqual(self.session.seen, [])
        self.assertEqual(self.counters()["nouser"], 1)

    def test_silence_keepalive_skips_the_session(self):
        self.assertEqual(self.decrypt(OPUS_SILENCE), OPUS_SILENCE)
        self.assertEqual(self.session.seen, [])

    def test_rtp_padding_is_stripped_before_dave(self):
        self.assertEqual(self.decrypt(b"DAVE-padded", padding=7), b"OPUS-padded")
        self.assertEqual(self.session.seen, [(42, b"DAVE-padded")])
        self.assertEqual(self.counters()["padded"], 1)

    def test_padding_only_packet_is_silence(self):
        self.assertEqual(self.decrypt(b"", padding=4), OPUS_SILENCE)

    def test_stale_key_after_reconnect_heals_without_rejoin(self):
        new_key = os.urandom(32)
        self.vc.secret_key = list(new_key)
        self.assertEqual(self.decrypt(b"DAVE-a", key=new_key, seq=1), OPUS_SILENCE)
        self.assertEqual(self.counters()["reheal"], 1)
        self.assertEqual(self.decrypt(b"DAVE-b", key=new_key, seq=2), b"OPUS-b")

    def test_unchanged_key_is_not_rebuilt_on_every_failure(self):
        other = os.urandom(32)
        for seq in range(1, 4):
            self.assertEqual(self.decrypt(b"DAVE-x", key=other, seq=seq), OPUS_SILENCE)
        self.assertEqual(self.counters()["reheal"], 0)
        self.assertEqual(self.counters()["xerr"], 3)
        self.assertEqual(self.decrypt(b"DAVE-ok", seq=4), b"OPUS-ok")
        self.assertEqual(self.counters()["xerr"], 0)

    def test_real_davey_errors_become_silence(self):
        real = davey.DaveSession(1, 1, 2)

        class Ready:
            ready = True

            @staticmethod
            def decrypt(user_id, media_type, data):
                return real.decrypt(user_id, media_type, data)

        self.vc._connection.dave_session = Ready()
        self.assertEqual(self.decrypt(b"\x01\x02\x03\x04"), OPUS_SILENCE)
        self.assertEqual(self.counters()["fail"], 1)

    def test_summary_adds_up_readers(self):
        self.decrypt(b"DAVE-1")
        total = voice_recv_dave.summary()
        self.assertGreaterEqual(total["readers"], 1)
        self.assertGreaterEqual(total["ok"], 1)
        self.assertTrue(total["e2ee"])


class PaddingTest(unittest.TestCase):
    def test_strip(self):
        self.assertEqual(voice_recv_dave.strip_rtp_padding(b"abc\x00\x00\x03"), b"abc")
        self.assertEqual(voice_recv_dave.strip_rtp_padding(b"abc\x09"), b"abc\x09")
        self.assertEqual(voice_recv_dave.strip_rtp_padding(b""), b"")


if __name__ == "__main__":
    unittest.main()
