# Public preview verification status

Version 1.1.0-rc5. This is a release candidate, not a stable-release certification.

## Verified locally
- 94 automated tests pass after public-document updates.
- Source file allowlist and common credential/private-path patterns checked.
- Five original distribution ZIP checksums match the supplied manifest.
- Public ZIP changes are documentation only, plus the source infographic. Unchanged members, including executables, runtime files, PNGs and WAVs, are hash-compared with the originals.
- Application code is MIT; character artwork and audio have separate restricted terms.
- No developer credentials, account history or personal balances are included.

## Prior packaging verification
- Single-EXE resources were checked without external assets/_internal.
- Optional packs and nine Japanese WAV files passed local resource/playback diagnostics.
- These diagnostics are not proof of subjective voice quality or clean-PC compatibility.

## Not yet verified
- Clean Windows PC and another Codex account.
- Actual Plus 5-hour quota behavior and all UI languages on different machines.
- Installation and launch through the itch desktop app.
- Independent native-speaker listening review on multiple playback devices.

## Distribution
GitHub is for source/git clone. itch.io hosts the Windows ZIPs and optional packs.
Use the per-file published checksums; do not replace an already published ZIP silently.
The EXE is unsigned. Investigate warnings rather than disabling protection or requesting administrator access.
No endorsement by OpenAI is claimed. Report bugs without account details or runtime data.
