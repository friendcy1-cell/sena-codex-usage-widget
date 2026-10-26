# Contributor guidance

- Application source code is MIT-licensed. Character artwork and audio have separate terms in LICENSE-ASSETS.md; the code license is not permission to reuse or redesign those assets.
- Preserve user settings and history when fixing bugs. Never commit credentials, account details, live usage data, local data/ folders or personal screenshots.
- The supplied character behavior is three frames at 750 ms, the original low/empty reaction, and a recovery reaction lasting 15 seconds. Tests cover that contract.
- Unknown or failed quota readings must never become an invented zero. Do not trigger recovery speech on first launch or unchanged polling.
- New settings enable voice/motion, while explicitly saved preferences must be respected. Keep mute, stop, volume and overlap protection intact.
- Use account-free preview mode for demonstrations. Do not bypass Windows security controls. Run the documented tests after behavioral changes.
