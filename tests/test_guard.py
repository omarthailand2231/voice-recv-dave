from __future__ import annotations

import unittest
from types import SimpleNamespace

import discord.opus
from discord.ext.voice_recv.opus import PacketDecoder
from discord.ext.voice_recv.sinks import BasicSink

import voice_recv_dave

discord.opus._load_default()


def setUpModule():
    voice_recv_dave.install()


@unittest.skipUnless(discord.opus.is_loaded(), "libopus is not installed")
class DecodeGuardTest(unittest.TestCase):
    def setUp(self):
        router = SimpleNamespace(sink=BasicSink(lambda user, data: None))
        self.decoder = PacketDecoder(router, 7)

    def test_corrupt_frame_becomes_silence_instead_of_killing_the_router(self):
        bad = b"\xff\xff\xff"
        with self.assertRaises(discord.opus.OpusError):
            self.decoder._decoder.decode(bad, fec=False)
        packet = SimpleNamespace(decrypted_data=bad, sequence=1)
        out_packet, pcm = self.decoder._decode_packet(packet)
        self.assertIs(out_packet, packet)
        self.assertEqual(pcm, b"\x00" * discord.opus.Decoder.FRAME_SIZE)
        self.assertGreaterEqual(voice_recv_dave.summary()["undecodable"], 1)

    def test_good_frame_still_decodes(self):
        frame = discord.opus.Encoder().encode(b"\x01\x00" * 1920, 960)
        packet = SimpleNamespace(decrypted_data=frame, sequence=1)
        _, pcm = self.decoder._decode_packet(packet)
        self.assertEqual(len(pcm), discord.opus.Decoder.FRAME_SIZE)


if __name__ == "__main__":
    unittest.main()
