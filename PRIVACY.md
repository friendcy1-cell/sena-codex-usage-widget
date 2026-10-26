# Privacy and connection details

This is an independent, unofficial LumiStage utility. It is not made, endorsed,
or supported by OpenAI. It cannot increase or bypass a Codex usage limit.

The widget starts the Codex executable installed on the user's own Windows PC.
It requests only `initialize`, `initialized`, `account/read` (without forcing a
token refresh), and `account/rateLimits/read` over a
local stdio connection, then closes that connection. Codex uses its normal local
authentication and contacts OpenAI to read that account's usage limits. Codex may
perform its usual authentication refresh and logging. We therefore do NOT claim
that no information ever leaves the PC, or that no files are written by Codex.

The widget does not read or distribute an auth.json, API key, password or token;
it does not open a model turn, conversation or project; and it does not send usage
to a LumiStage server, use analytics, purchase credits or consume reset coupons.
There is no developer-account credential included in a release archive.

Widget history stores bounded quota observations, timestamps, a hashed account
marker, plan and reset-coupon counts/expiry. Monetary credit balances are display
only and excluded from history. The account read may provide an email/account or
workspace identifier; the widget hashes this in memory and does not store or send
the original identity to a developer service. An unverified account fails closed
rather than combining multiple users under one unknown history marker.
The marker is pseudonymous, not guaranteed
anonymous. Windows portable builds save settings/history under the current user's
local application-data folder, LumiStage/SenaUsageWidget; source runs use data/.
Do not include either runtime folder in bug reports or public source uploads.
The single-EXE edition uses the same settings/history location and additionally
extracts bundled runtime/assets into a Windows temporary folder while running.
It does not place credentials or settings inside that embedded asset bundle.

Character packs are local data files; no pack download, script execution or cloud
TTS is implemented. Japanese voice playback reads provided PCM WAV files locally.
Volume scaling happens in memory and does not alter the Windows system volume.

Preview mode uses synthetic numbers, never reads an account or writes preferences,
and marks the display as a preview. Use it for screenshots and demonstration clips.
