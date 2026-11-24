# voice-recv-dave

Make [discord-ext-voice-recv](https://github.com/imayhaveborkedit/discord-ext-voice-recv) hear people in Discord's end-to-end encrypted voice calls (DAVE), without forking it.

Since Discord turned on DAVE, bots that receive voice with `discord-ext-voice-recv` get `OpusError: corrupted stream`, static, or silent recordings ([#43](https://github.com/imayhaveborkedit/discord-ext-voice-recv/issues/43), [#53](https://github.com/imayhaveborkedit/discord-ext-voice-recv/issues/53), [#61](https://github.com/imayhaveborkedit/discord-ext-voice-recv/issues/61), [#64](https://github.com/imayhaveborkedit/discord-ext-voice-recv/issues/64)). The extension removes the transport encryption, but every frame is still end-to-end encrypted underneath. discord.py 2.7 already keeps a DAVE session to encrypt what your bot **sends**; this package uses that same session to decrypt what it **receives**.

It patches the unmodified PyPI release (`0.5.2a179`, same code as current master) with one `install()` call. It also fixes several other receive bugs that make bots stop hearing people until someone makes them rejoin.

## Install

```bash
pip install "discord.py[voice]>=2.7" "discord-ext-voice-recv>=0.5.2a179" "davey>=0.2.0"
pip install git+https://github.com/omarthailand2231/voice-recv-dave
```

## Use

```python
import voice_recv_dave
from discord.ext import voice_recv

voice_recv_dave.install()  # once, before connecting

vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
await voice_recv_dave.listen(vc, voice_recv_dave.TimeMixSink("call.wav"))
```

Plain `vc.listen(sink)` works too. `voice_recv_dave.listen()` also drops stale audio and waits out the old reader first (see below). A complete recording bot is in [`examples/record.py`](examples/record.py).

## What it fixes

| Problem | What you see | What this does |
|---|---|---|
| DAVE frames are never decrypted | `OpusError: corrupted stream`, static, silent recordings | Decrypts each frame with discord.py's own `dave_session`, per speaker |
| Stale key after a voice reconnect | `CryptoError` and silence until someone makes the bot rejoin | Rebuilds the receive key in place from the voice client's current key |
| RTP padding is never stripped | Some frames look "unencrypted" to davey, ciphertext reaches the decoder | Strips padding (RFC 3550 §5.1) before decrypting |
| One bad frame kills the reader | Listening stops for the rest of the session | That 20 ms frame becomes silence; the reader keeps running |
| Audio queued before you listen | A recording starts with minutes-old speech crammed into the first instant | `drain_stale_audio()` / `listen()` throw it away first |
| Reader handoff race | A new `listen()` right after `stop_listening()` gets killed by the old router thread | `release_reader()` / `listen()` wait for old router threads to exit |
| Stock `WaveSink` timeline | Pauses disappear, overlapping speakers play one after another | `TimeMixSink` keeps real time, mixes speakers, keeps each speaker's bursts in order |

Frames that can't be decrypted become silence instead of noise. Calls without end-to-end encryption are left untouched.

## Is it working?

```python
print(voice_recv_dave.summary())
# example: {'ok': 5120, 'fail': 0, 'nouser': 3, 'bypass': 0, 'passthru': 12, 'reheal': 0,
#  'padded': 0, 'readers': 1, 'undecodable': 0, 'e2ee': True}
```

- `ok` climbing, `fail` near zero: decrypting fine.
- `bypass` climbing: the call isn't end-to-end encrypted (or the session isn't ready yet), so frames are passed through untouched.
- `nouser`: frames that arrived before Discord said which user an SSRC belongs to (the first moments of a new speaker).
- `passthru`: frames that were never DAVE-encrypted (silence/keepalives), passed through.
- `reheal`: times the receive key was re-synced after a reconnect.
- `undecodable`: Opus frames the decoder rejected and that were replaced with silence.

## Gotchas

- **Use davey 0.2.0 or newer.** The PyPI wheel of davey 0.1.6 failed to decrypt most speakers for us ([davey#20](https://github.com/Snazzah/davey/issues/20)); building from source or 0.2.0+ fixes that.
- **Don't try to opt out of DAVE** by setting the max DAVE protocol version to 0. Discord closes the voice connection with code 4017.
- **A server-deafened bot receives nothing.** Discord doesn't send it audio at all.
- **One listener per voice client.** voice_recv has a single reader slot; use `MultiAudioSink` if you need several sinks.

## Status

Extracted from a production Discord bot, where this code has run since September 2026; live decryption there was confirmed with davey built from source. The test suite runs real voice_recv readers over real transport-encrypted RTP packets and a real Opus decoder, but it does not join a live call. Tested with Python 3.11–3.13, discord.py 2.7.1, discord-ext-voice-recv 0.5.2a179 and davey 0.2.0.

If you try it in a live call, please open an issue with your `summary()` output, whether it worked or not.

## How it works

- `install()` wraps `AudioReader.__init__`, so each new reader's `decryptor.decrypt_rtp` becomes: transport decrypt → on `CryptoError`, re-sync the key → strip RTP padding → if `vc._connection.dave_session` is ready, map SSRC → user and call `dave_session.decrypt(user_id, MediaType.audio, frame)`.
- It also wraps `PacketDecoder._decode_packet` so an `OpusError` becomes one frame of silence. Pass `install(decode_guard=False)` to skip that part.
- Several pull requests add DAVE receive to voice_recv itself ([#56](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/56), [#58](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/58), [#62](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/62), [#65](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/65)). If one is merged and released, you won't need the decrypt layer; the other fixes still apply.

## Tests

```bash
pip install -e .
python -m unittest discover -s tests -v
```

The Opus guard tests need libopus (`brew install opus` or `apt install libopus0`); they skip without it.

## License

MIT. Built on [discord.py](https://github.com/Rapptz/discord.py), [discord-ext-voice-recv](https://github.com/imayhaveborkedit/discord-ext-voice-recv) and [davey](https://github.com/Snazzah/davey).
