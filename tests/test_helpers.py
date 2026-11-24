from __future__ import annotations

import asyncio
import os
import socket
import time
import unittest

from discord.ext.voice_recv.reader import AudioReader
from discord.ext.voice_recv.sinks import BasicSink

import voice_recv_dave
from _util import FakeVoiceClient


def udp_pair():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.setblocking(False)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return receiver, sender


class DrainTest(unittest.TestCase):
    def test_drops_everything_queued_before_listening(self):
        receiver, sender = udp_pair()
        try:
            for n in range(5):
                sender.sendto(bytes([n]) * 20, receiver.getsockname())
            time.sleep(0.05)
            vc = FakeVoiceClient(os.urandom(32))
            vc._connection.socket = receiver
            self.assertEqual(voice_recv_dave.drain_stale_audio(vc), 5)
            self.assertEqual(voice_recv_dave.drain_stale_audio(vc), 0)
            with self.assertRaises(BlockingIOError):
                receiver.recv(4096)
        finally:
            receiver.close()
            sender.close()

    def test_no_socket_is_a_no_op(self):
        self.assertEqual(voice_recv_dave.drain_stale_audio(FakeVoiceClient(os.urandom(32))), 0)


class ReleaseReaderTest(unittest.TestCase):
    def start_reader(self, vc, *, attach: bool):
        reader = AudioReader(BasicSink(lambda user, data: None), vc)
        reader.packet_router.start()
        reader.active = True
        if attach:
            vc._reader = reader
        return reader

    def test_waits_for_the_router_thread_to_exit(self):
        vc = FakeVoiceClient(os.urandom(32))
        reader = self.start_reader(vc, attach=True)
        self.assertEqual(voice_recv_dave.router_threads(vc), [reader.packet_router])
        asyncio.run(voice_recv_dave.release_reader(vc))
        self.assertEqual(voice_recv_dave.router_threads(vc), [])
        self.assertFalse(reader.is_listening())

    def test_stops_an_orphaned_reader(self):
        vc = FakeVoiceClient(os.urandom(32))
        orphan = self.start_reader(vc, attach=False)
        asyncio.run(voice_recv_dave.release_reader(vc))
        self.assertEqual(voice_recv_dave.router_threads(vc), [])
        self.assertFalse(orphan.is_listening())

    def test_other_voice_clients_are_untouched(self):
        mine, other = FakeVoiceClient(os.urandom(32)), FakeVoiceClient(os.urandom(32))
        theirs = self.start_reader(other, attach=True)
        try:
            asyncio.run(voice_recv_dave.release_reader(mine))
            self.assertTrue(theirs.is_listening())
            self.assertEqual(voice_recv_dave.router_threads(other), [theirs.packet_router])
        finally:
            asyncio.run(voice_recv_dave.release_reader(other))


class ListenTest(unittest.TestCase):
    def test_releases_drains_then_listens(self):
        receiver, sender = udp_pair()
        try:
            vc = FakeVoiceClient(os.urandom(32))
            vc._connection.socket = receiver
            for _ in range(3):
                sender.sendto(b"old" * 10, receiver.getsockname())
            time.sleep(0.05)
            sink = BasicSink(lambda user, data: None)
            asyncio.run(voice_recv_dave.listen(vc, sink))
            self.assertEqual(vc.listened, [(sink, None)])
            with self.assertRaises(BlockingIOError):
                receiver.recv(4096)
        finally:
            receiver.close()
            sender.close()


if __name__ == "__main__":
    unittest.main()
