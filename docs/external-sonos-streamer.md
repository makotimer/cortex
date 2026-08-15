# External Sonos streamer — setup notes

Notes from 2026-08-14. Not a spec and not implemented. The goal is a
**host-side play path** that an LLM (or anything else) can call without
going through cortex.

Cortex keeps the hourly chime. This path only plays (or streams) a clip
on the same speaker group.

## Why not cortex

- Cortex Sonos is a scheduled interrupt (`modules.sonos`, `action=chime`),
  not a speaker API.
- The LLM already lives on the host (Grok TUI, `/srv/docker/audio`,
  internet TTS). Crossing into the 512 MB `mailnet` container via
  `docker exec` / IMAP `RUN` is extra machinery for “play this URL.”
- SoCo talks to the coordinator on the LAN. Any process on r2d2 can do
  that. Cortex is not special here.

Steal the **pattern** from `modules/sonos/lib/playback.py`, not the
process.

## What the speaker actually does

Sonos is a **pull player**. Nothing pushes audio bytes at it.

1. Snapshot queue + position + volume (`soco.snapshot.Snapshot`).
2. Skip if `is_playing_tv` (TV input cannot be paused cleanly).
3. `play_uri(uri=…, title=…)` on the **group coordinator**.
4. The speaker HTTP-GETs that URI itself.
5. Poll `current_transport_state` until it has been `PLAYING` and then
   left `PLAYING` (or hit a timeout).
6. Restore snapshot + previous volume.

A local file, a pipe, or MCP voice output will not play unless it is
first sitting at a URL the speaker can GET.

Reference implementation: `modules/sonos/lib/playback.py`
(`play_uri_with_snapshot`). Live smoke:
`tests/manual_live_runs/test_sonos_live.py`.

## Known LAN facts (this house)

| Role | Value |
|---|---|
| Coordinator | `COORDINATOR_IP=192.168.0.208` (from cortex `.env`) |
| Chime files | `http://192.168.0.190/grandfather_clock_chime_HH.wav` |
| NAS HTTP | Synology Web Station on `192.168.0.190` (nginx, range requests, WAV works) |
| Host NFS | r2d2 mounts `192.168.0.190:/volume1/Plex` and `…/Storage` only — **not** the Web Station docroot |
| Cortex ports | none published; the speaker cannot fetch from the container |

Chime schedule (America/Chicago), all at `:00`, wait cap ~75 s:

- Mon–Sat quiet 08:00–09:00 and 19:00–20:00 (vol 25)
- Mon–Sat loud 10:00–18:00 (vol 40)
- Sun quiet 07:00–08:00 and 18:00–19:00 (vol 25)
- Sun loud 09:00–17:00 (vol 40)

## Recommended first shape: play a finished clip

Do not start with a live stream. A spoken reply is a short MP3/WAV.

```
TTS (or any encoder)
  → write file to a LAN HTTP path the speaker can GET
  → host CLI: snapshot / play_uri / wait / restore
```

Suggested CLI (host venv, `soco` only):

```bash
sonos-play --uri http://<lan-host>/<file>.mp3
sonos-play --file /path/to/clip.mp3   # only if that path is also HTTP-reachable
```

Flags worth having from day one: `--volume`, `--title`, `--timeout`,
`--coordinator`, `--force` (ignore TV skip). Default timeout should be
clip-length + slack, not the chime’s 75 s.

The CLI should not know about LLMs or TTS. It plays a URI.

### Where to put the file

The speaker must be able to GET it. Options, cheapest first:

1. **Tiny static HTTP server on r2d2** (e.g. bind a high port to
   `0.0.0.0`, serve a scratch dir). Sonos on `.208` can reach the host.
   No NAS write access required. Fine for short-lived clips.
2. **SCP/SFTP to the NAS Web Station docroot** as `panda@192.168.0.190`,
   then play `http://192.168.0.190/<name>`. Same path the chimes already
   use. Docroot is not NFS-mounted here.
3. **NFS-export the Web Station root** later if (2) gets annoying.

Do not publish a port on the cortex container for this.

Formats that already work in this house: WAV over HTTP. MP3 over HTTP
is the usual SoCo `play_uri` case and is what xAI TTS emits by default.
Prefer a finished file with a `Content-Length` (range requests help
Sonos). HLS is a maybe; raw PCM/WebSocket is a no.

### xAI TTS (likely producer, not part of the player)

```
POST https://api.x.ai/v1/tts
  { "text": "…", "voice_id": "eve", "language": "en",
    "output_format": { "codec": "mp3" } }   # or "wav"
→ raw audio bytes
```

Write those bytes to the HTTP dir, then call the CLI. Local Kokoro
(`/srv/docker/audio`) can produce MP3 too but it is a GPU hog and must
not stay up — wrong for on-demand replies.

## Streaming (later, only if first-audio matters)

Sonos will play a continuous HTTP audio stream (icy / `audio/mpeg`) the
same way it plays a file: it pulls the URL. You would:

1. Run a small LAN streamer (icecast, or a chunked `audio/mpeg` HTTP
   response).
2. Pipe TTS chunks into it.
3. `play_uri` that stream URL.
4. **Stop and restore yourself** when the utterance ends. The cortex
   wait loop assumes a clip that leaves `PLAYING`. A stream does not.

Costs vs a file:

- Extra process and format constraints (continuous MPEG, not discrete
  MP3 files glued poorly).
- Speaker-side buffer, so “start as tokens arrive” still lags.
- Same TV-skip and hijack-the-group behavior.
- Restore is no longer “wait until idle.”

Only build this if hearing the first sentence before TTS finishes is
worth that.

## Collision with the chime

Two independent snapshot/restore owners can corrupt each other:

1. Streamer snapshots.
2. Chime snapshots.
3. Streamer restores the chime’s state.
4. Chime restores stale music.

The chime owns roughly `:00`–`:01` in the windows above. First version:
document it, or refuse to start a play in that minute. A shared lock
only pays off if this actually happens — and putting the lock in the
chime **is** a cortex change, which this path is trying to avoid.

## What not to do

- Do not add `action=play` to `modules.sonos` unless you later decide
  cortex should be the single speaker lock. That is a different design.
- Do not `docker exec` cortex to play a clip.
- Do not expect the Grok voice MCP to produce a file. This session only
  sees `list_voices`; TUI speech does not land on Sonos.
- Do not stream from a URL the speaker cannot route to (container
  localhost, `mailnet`-only, public-only hosts the LAN cannot reach).

## First build checklist

- [ ] Host venv + `soco`; confirm `SoCo("192.168.0.208").player_name`.
- [ ] Scratch HTTP dir reachable from the speaker (`curl -I` the URL
      from another LAN box, not just from r2d2).
- [ ] Drop a known-good WAV/MP3 there and play it with snapshot/restore
      while music is queued; confirm the queue comes back.
- [ ] Confirm TV-active skip (or `--force`) matches what you want.
- [ ] Confirm a play that overlaps `:00` is acceptable, or gate it.
- [ ] Point TTS (or a canned file) at the same CLI. Keep TTS out of the
      player.
