# Minimal optional character pack format (version 1)

The basic edition ships with Sena only. An additional pack is a local data folder,
not executable code. Rina and Nari preview packs are supplied separately on itch.io.
They are not needed to run Sena. Asset terms are in LICENSE-ASSETS.md.
Both follow the original Sena three-frame/750 ms timing and use matching original
training-outfit success/fail images. New poses, costume swaps and timing changes
must be approved by the owner first, not introduced as incidental bug fixes.

Extract an optional pack ZIP into the executable/source folder, preserving its
`packs/rina/` or `packs/nari/` folder (not into `assets/voices/ja/`). Inspect before
replacing an existing same-ID pack. Never overwrite settings or history. Restart
the widget and choose its name in Character pack. An ID should use letters, numbers,
hyphens or underscores. At most 20 local pack directories are examined.

`pack.json` (UTF-8, max 8 KiB):

```json
{
  "version": 1,
  "name": "Example character",
  "images": {
    "idle_alt": "idle.png",
    "full": "full.png",
    "low": "low.png",
    "empty": "empty.png",
    "frame_1": "dance_1.png",
    "frame_2": "dance_2.png",
    "frame_3": "dance_3.png"
  }
}
```

Only `idle_alt` is required. Missing other poses fall back to that same character's
idle image, not Sena's image. Each file must be a valid PNG, max 8 MiB and 4096×4096;
transparent square artwork with consistent placement works best. The app renders
it at 196×196. Animation is a small three-frame loop, not a general animation system.

Japanese audio slots inside a pack are `voices/ja/full.wav`, `voices/ja/low.wav`,
and `voices/ja/empty.wav`. There is no cross-character or cross-language voice
fallback. If a pack omits audio, it is silent. See `VOICE_BRIEF_JA.md`.
This external `packs/<id>/` location is unchanged for the single-EXE edition;
only base Sena assets are embedded. Packs are never installed into its temporary
extraction folder, and the single-EXE option does not change character design.
The voice team's delivery-only `Optional_packs/<id>/assets/voices/ja/` layout is
mapped to this app's `packs/<id>/voices/ja/`; the three characters are not merged.

Unknown manifest fields, external/absolute/traversal paths and unsupported formats
are rejected. Symlink escapes are not accepted. Packs do not contain scripts or
network instructions, and must never contain credentials or user history. A failed
image decode falls back to the bundled Sena character with a warning.

Pack authors must separately document image/audio rights and any required credits.
Installing a pack is not a license to redistribute its assets. No downloader,
marketplace, remote URL import or automatic pack updater is implemented.
