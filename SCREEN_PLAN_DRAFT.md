# Draft: prayer dashboard and Qur'an audio screen

> Review draft only. This has not been merged into `ENHANCEMENT_PLAN.md`.

## Goals and features

Add a local touchscreen dashboard that displays the current date and time, today’s prayer schedule, the next prayer, and a live countdown showing the remaining time. Add a Qur’an audio section with track selection, play/pause/stop controls, playback status, and a configurable stop timer. Display a non-blocking status message while the prayer schedule is being updated, during retry attempts, and after an update fails. When an update fails, clearly indicate whether valid cached prayer times are being used.

## Implementation requirements

- Publish scheduler status through an atomic `cache/status.json` file containing the current state, last successful update, latest error, retry information, and whether cached data is active.
- Use the existing scheduler and `athan-audio.service` as the sources of truth; the dashboard must not duplicate scheduling or create a separate audio playback process.
- Extend the audio-service control protocol as needed to support Qur’an playback, playback status, and a persistent stop timer.
- Keep the dashboard functional when offline and when valid cached schedule data is available.
- Handle missing or malformed status, schedule, and audio responses without crashing; show a recoverable error state instead.
- Calculate countdowns from timezone-aware prayer datetimes and handle midnight rollover and daylight-saving transitions correctly.
- Preserve Athan playback priority and define how active Qur’an playback is stopped or suspended when an Athan is due.
- Start automatically with the Pi services and recover cleanly after reboot or service restart.
- Add tests for ready, updating, retrying, cached, failed, and unavailable-schedule states, plus Qur’an playback controls and stop-timer behavior.
