# Enhancement plan and work history

The implementation lives in `src/`, systemd units live in `athan.services/`,
and the project is installed as one folder in the Pi user's home directory.

## Work history

1. The Mawaqit page was inspected and the embedded `confData` JSON was selected as the data source. It contains the daily times and the yearly calendar.
2. The original daily cache was found to be vulnerable to stale values. The implementation was changed to parse the yearly calendar and derive the current day from it.
3. Scheduler and audio responsibilities were separated into `src/athan.py` and `src/athan_audio.py`.
4. MPV-based playback was replaced by one persistent PipeWire playback stream. FFmpeg decodes each MP3 to PCM, while `pw-cat` remains alive and receives silence between announcements.
5. The test program was changed to use the real audio-service socket rather than creating a second playback process.
6. Option 2 was selected: the yearly cache is a rolling remainder of the current calendar. At midnight, yesterday and earlier entries are deleted.
7. The current implementation now refreshes on service startup, rolls the cache at midnight, verifies the current day at 00:01, and retries network operations five times at ten-minute intervals.
8. A non-leap-year compatibility issue was found: Mawaqit includes a February 29 template entry in 2026. The parser now skips only that invalid optional date during non-leap years.

## Next enhancement: screen/status integration

When a screen is added, implement a status publisher without coupling the screen to the scheduler internals.

### Required work

1. Add a small status JSON file, for example `cache/status.json`, updated after every fetch attempt.
2. Include `state`, `last_success`, `last_error`, `attempt`, `max_attempts`, `next_retry`, and `using_cache`.
3. Publish states such as `starting`, `fetching`, `ready`, `retrying`, `using_cached_times`, and `failed`.
4. Display a clear message when the first fetch fails, including the next retry time.
5. After the fifth failure, display that the fetch failed completely and that the system will continue using valid cached times when available.
6. Clear the error state after the next successful fetch.
7. Add a screen test that simulates each state without requiring internet access.

## Other planned improvements

- Add a service health check that confirms the Unix socket is responsive and the `pw-cat` process is alive.
- Add atomic JSON writes (`temporary file` then `replace`) so a power loss cannot leave a partially written cache.
- Add a small command-line status tool for the current schedule, last fetch, retry state, and audio state.
- Add optional alert logging when no valid cached schedule exists.
- Add tests for December 31 to January 1 rollover and daylight-saving-time transitions.
- Consider a configurable location/timezone instead of project constants.
- Add an installation/update script once the Pi deployment layout is stable.

## Known limitations

- The exact Bluetooth announcement behavior is controlled by the Bluetooth/audio stack. The continuous stream prevents the application from creating a new playback stream for every Athan, but a genuine boot or Bluetooth reconnect may still produce one speaker announcement.
- Option 2 intentionally removes past dates. The yearly JSON is not an archival calendar.
- The retry simulation in `test/test_athan.py` is deterministic and does not disable the Pi’s network interface; it exercises the same retry count and success/failure paths without risking the Pi’s connectivity.
