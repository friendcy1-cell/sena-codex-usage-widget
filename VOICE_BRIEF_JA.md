# Japanese voice alert specification

The display supports English, Korean, Japanese and Simplified Chinese; audio is Japanese only. Three synthetic character voices are supplied, with three fixed recordings each. No cloud TTS or model inference runs during playback.

| File | Meaning | Japanese text |
|---|---|---|
| full.wav | Observed allowance restored to 100% | ありがとう！利用枠が回復したよ。 |
| low.wav | Remaining allowance crossed to 20% or less | 残りが少なくなってきたよ。確認してね。 |
| empty.wav | Included allowance exhausted | 利用枠を使い切ったよ。確認してね。 |

A low reading may already be below 20%, and credits may allow continued use at zero included allowance. Speech does not claim an exact remaining percentage or force work to stop.

Sena is clear and calm, Rina warm and confident, and Nari lively. These are fictional adult characters, not imitations of real voice actors. Source provenance is in VOICE_CREDITS.md; usage restrictions are in LICENSE-ASSETS.md.

Bundled recordings are PCM 16-bit, 48 kHz, mono, approximately 3.58-4.50 seconds. The application accepts supported WAV files within its 8-second/2-MiB limits. Default Sena slots are assets/voices/ja/{full,low,empty}.wav. Optional packs use packs/<id>/voices/ja/.

Automatic alerts are suppressed on initial observations, unchanged state, failed/unknown readings, account changes and observation gaps. A manual Test voice button is separate from the automatic-alert switch. Stop, mute, volume zero, character change and exit cancel playback; overlapping requests are not queued.
