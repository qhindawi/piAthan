# Dashboard implementation instructions

This document is the implementation contract for the dashboard work. The dashboard must run as a graphical client of the existing scheduler and audio service; it must not replace either service or duplicate their responsibilities.

## Runtime and desktop/VNC behavior

Verified on the user's Pi: the graphical session is `wayland` / `labwc:wlroots`, labwc reports version `0.8.4-dirty`, and physical output `HDMI-A-1` is enabled at 1024×600, scale 1, position 0,0. Raspberry Pi Connect runs wayvnc with control socket `/run/user/1000/rpi-connect-wayvnc-ctl.sock`; its output list currently contains only `HDMI-A-1`. `PyQt5` is installed. The existing user labwc config maps `yldzkj USB2IIC_CTP_CONTROL` touch to `HDMI-A-1`; preserve that mapping. Actual simultaneous VNC/touch behavior and the virtual output have not yet been verified.

Implement the dashboard as a Qt Quick application in the logged-in `qais` graphical session. The target is an extended desktop: the physical touchscreen displays the dashboard while a separate virtual output is captured by VNC for desktop work. Fullscreen on the physical output is permitted; do not use global keyboard or mouse grabs.

Start it with a dedicated `systemd --user` service after the graphical session is available. Use `Restart=always`, a short restart delay, and the graphical session environment (`DISPLAY`/`WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, and the user D-Bus session). The service must not start `athan.service` or `athan-audio.service`; those remain independent system services.

The dashboard must have a stable application ID and explicitly select the configured physical output, never the VNC virtual output. Show the dashboard at 1024×600 on the physical touchscreen; the central prayer region occupies approximately half its height, with the clock/weather and Qur’an regions above and below. Do not repeatedly steal focus. VNC applications should open and remain on the virtual output, so normal remote work does not cover the dashboard. A dashboard crash or restart must not stop audio playback or alter the schedule.

Provide a manual launch command and a user-service command for diagnostics. Test the physical display and a VNC connection at the same time: the dashboard remains visible on the touchscreen while ordinary remote applications open on the virtual output. The two outputs belong to the same desktop session and may share focus, keyboard and pointer: this setup does not guarantee simultaneous independent input or prevent a deliberately moved/fullscreen window from covering the dashboard. Verify touch mapping to the physical output and that the dashboard remains responsive while VNC is used.

### Extended desktop with a virtual VNC output

These steps are for a compatible **labwc Wayland session and wayvnc**. First inspect the actual Pi; do not assume the installed session, output name, available commands, or VNC server:

```bash
echo "$XDG_SESSION_TYPE"
echo "$XDG_CURRENT_DESKTOP"
labwc --version
wlr-randr
wayvncctl --help
wayvncctl output-list
```

Run graphical commands in the logged-in user's session with its Wayland environment. If these commands are run over SSH, import/use that session's `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR` rather than starting another compositor. If the Pi uses X11, wayfire, RealVNC, or an older labwc/wayvnc without the required features, determine the appropriate supported setup for that installation; do not apply the labwc commands blindly.

1. Preserve the existing labwc configuration and add the following key binding inside the existing `<keyboard>` element of `~/.config/labwc/rc.xml`. If no user configuration exists, start from the installed system configuration so existing desktop settings and bindings are retained. Use an unused key combination if `Super+V` is already assigned:

   ```xml
   <keybind key="W-v">
     <action name="VirtualOutputAdd" output_name="NOOP-remote" />
   </keybind>
   ```

2. Reload the configuration with `labwc --reconfigure`, then press `Super+V` once in the graphical session. Check with `wlr-randr` that `NOOP-remote` exists. The binding creates a new output each time, so do not invoke it repeatedly. This manual procedure is for initial verification; implement startup automation only after confirming the installed labwc version supports creating the named output. `LABWC_FALLBACK_OUTPUT` alone is insufficient because it creates an output only when no physical outputs remain.
3. Arrange the outputs side by side, not mirrored. For example, if the physical display is already at `(0,0)` with 1024×600 logical pixels:

   ```bash
   wlr-randr --output NOOP-remote --on --custom-mode 1280x720 --pos 1024,0
   ```

   Use the actual physical output's position, scale, rotation and logical dimensions from `wlr-randr`; change the example position if they differ. Confirm that the physical screen is still active. Never copy an assumed connector name into the configuration.
4. With the existing authenticated wayvnc server running, inspect the output list and select the virtual output. This Pi's Raspberry Pi Connect wayvnc uses the custom socket `/run/user/1000/rpi-connect-wayvnc-ctl.sock`; pass it with `--socket=...`. Its installed version accepts the output name as a positional argument. For example, after verifying that the virtual output exists:

   ```bash
   wayvncctl --socket=/run/user/1000/rpi-connect-wayvnc-ctl.sock output-list
   wayvncctl --socket=/run/user/1000/rpi-connect-wayvnc-ctl.sock output-set NOOP-remote
   ```

   Reconnect the VNC client if necessary. Switching the captured output may affect existing clients. Keep the current authentication and network configuration; do not start an unauthenticated public server or overwrite VNC credentials.
5. Verify that VNC shows the remote output, that a terminal and other windows open there, and that the dashboard remains fully visible and touchable on the physical output. Test keyboard/pointer focus, the screen's touch mapping, disconnect/reconnect, and reboot. If desktop panels or launched applications appear on the physical output, configure placement with the installed compositor and re-test rather than claiming isolation.
6. Once verified, implement idempotent session startup that creates the virtual output once, sets its geometry, waits for the existing VNC server and selects `NOOP-remote`. Order startup after the compositor and avoid duplicate outputs, races and changing the physical display mode. Pin the dashboard to the physical output by its configured name. Log and expose clear errors if creation, capture selection or output discovery fails. Keep the working VNC access path available for recovery.

Reference: [labwc virtual output actions](https://labwc.github.io/labwc-actions.5.html), [wayvnc multi-output documentation](https://github.com/any1/wayvnc/blob/master/wayvnc.scd), [Raspberry Pi VNC documentation](https://www.raspberrypi.com/documentation/computers/remote-access.html).

## Main screen layout

The main screen is a touch-accessible dashboard with sub-screens opened by touching their respective areas. It must also provide visible navigation buttons or links so that swipe is not the only navigation method.

### Upper area: clock, calendars, and weather

The upper area shows:

- current local time;
- Hijri date above the Gregorian date;
- current weather on the right;
- location name below the weather, initially `Wuppertal`;
- a weather icon occupying the height of the weather block;
- current temperature and short condition text beside the icon.

Touching the weather block opens the weather screen with today’s hourly forecast.

### Prayer area

The prayer area uses two columns:

- left: remaining time to the next prayer, followed by the next prayer name and time;
- right: a continuously updating list of the five prayers.

Each prayer row must show the relative day in smaller text, for example `today’s Dhuhr` or `tomorrow’s Dhuhr`, with the prayer name and time larger. When a prayer time passes, the row must advance to the next occurrence from the rolling calendar rather than remaining marked as passed. The Fajr row must show the optional first-Fajr reminder time in its right corner when `config/Fajr_first_athan.json` enables it. The reminder display preference must not change whether the reminder is enabled.

Add the prayer-volume icon at the left of the next-prayer countdown. Touching it opens the prayer-volume popup.

### Qur’an summary area

The lower area shows the current Qur’an album/reciter, sura/track name, playback state, and album artwork or a deterministic placeholder when artwork is unavailable. It does not show the full playback control set on the main screen.

User-approved revision: the main screen also shows Previous, Play/Pause and Next controls, with the elapsed and total track time in small text directly beneath them. The main-screen timer button starts or cancels the configured timer; the timer duration is set on the Qur'an track-control screen.

Touching the artwork or Qur’an summary opens the Qur’an screen. The main screen must continue to show the selected album and sura after navigation.

## Qur’an folder and playback workflow

Treat immediate subfolders below the configured Qur’an root as albums/reciters. The expected structure is:

```text
music/
└── Qur'an/
    ├── Hafs/
    │   ├── 001 Al-Fatiha.mp3
    │   ├── 002 Al-Baqarah.mp3
    │   └── ...
    ├── Husari/
    │   ├── 001 Al-Fatiha.mp3
    │   └── ...
    └── AbdulBasit/
        ├── 001 Al-Fatiha.mp3
        └── ...
```

:codex-annotation{index="1" id="88d0203f-5199-461e-88e6-a0a5d2c7d96d"}

The implementation must:

1. discover albums by scanning immediate child directories;
2. discover tracks by scanning supported audio files inside the selected album;
3. ignore unsupported files and reject paths outside the configured Qur’an root;
4. sort albums naturally and tracks by numeric filename prefix when present;
5. read ID3 metadata when available, with filename fallback for title and track number;
6. use embedded artwork when available, with a bundled placeholder otherwise;
7. persist the last selected album, track, and playback position in a small config/state file;
8. rescan on startup and on explicit refresh, without interrupting current playback unless the selected file disappears.

The Qur’an track-control screen must provide two dropdowns: the first lists immediate folders under the configured Qur'an root (for example `Al-Husary - Hafs`); the second lists tracks in the selected folder. It provides sliding hour and minute controls to set the stop-timer duration, the Qur'an-volume control, and a Refresh button to rescan folders and tracks. Playback controls are on the main screen. One tap on Previous restarts the current track; a double tap within a defined short interval selects and plays the previous track. Next selects and plays the next track. Both screens must expose the current folder and track clearly.

Use the existing Unix audio socket and one continuous PipeWire stream. Do not start a second player process from the dashboard. Extend the protocol with explicit Qur’an actions and status, for example `quran_library`, `quran_play`, `quran_pause`, `quran_stop`, `quran_next`, `quran_previous`, `quran_timer_set`, `quran_timer_cancel`, and `quran_status`.

## Separate Athan and Qur’an volume levels

The current socket controls the configured sink volume globally. Extend it with two persisted logical profiles:

- `athan_volume`: volume used while an Athan or first-Fajr reminder is playing;
- `quran_volume`: volume used while Qur’an playback is active.

The audio service must apply `quran_volume` when Qur’an playback starts or resumes. Before an Athan starts, it must switch the existing playback stream to `athan_volume`; after the Athan finishes, it must restore `quran_volume` and resume or restart Qur’an playback according to the selected resume policy. Do not create a second PipeWire stream.

Expose profile status and updates through the Unix socket, for example:

```json
{"action":"volume_profiles_status"}
{"action":"volume_profile_set","profile":"athan","value":0.70}
{"action":"volume_profile_set","profile":"quran","value":0.35}
```

Keep the existing `volume_status`, `volume_set`, `volume_step`, and `volume_mute` commands working. Store profile values separately from the physical sink selection, validate values from 0.0 to 1.0, and return the active profile in status responses. The two volume icons must open separate popups: prayer volume from the countdown area and Qur’an volume from the Qur’an screen.

The implementation must define and test the resume policy. The default may be to resume from the saved track position, but a simpler restart-from-beginning policy is acceptable only if it is documented and exposed consistently.

## Schedule, sunrise, and Hijri calendar

The existing Mawaqit yearly fetch already contains the six daily values: Fajr, Shuruq, Dhuhr, Asr, Maghrib, and Isha. Preserve Shuruq as `sunrise` in the rolling calendar and in `cache/today_prayer_times.json`; do not issue a second sunrise request.

The daily cache should include:

```json
{
  "date": "YYYY-MM-DD",
  "gregorian": {"date": "YYYY-MM-DD", "weekday": "..."},
  "hijri": {"day": 22, "month": "Rabi al-Thani", "year": 1448, "method": "..."},
  "times": {
    "Fajr": "05:58",
    "sunrise": "07:31",
    "Dhuhr": "13:18",
    "Asr": "16:02",
    "Maghrib": "18:47",
    "Isha": "20:12"
  }
}
```

Hijri data must be fetched or recalculated during the schedule refresh and saved with the same date key. When a daily or scheduled refresh succeeds, replace the cached Hijri value because the date may be adjusted by the source. When the network is unavailable, continue showing the last valid cached value and mark it as cached.

The user requested published Hijri dates directly from Mawaqit, without choosing a separate calculation method. Fetch the mosque's public annual calendar PDF, extract its published date rows, and merge them by Gregorian date into the rolling yearly prayer calendar. Also save today's value in `today_prayer_times.json`. Refresh the PDF independently from prayer times, preserve valid cached Hijri values on failure, and report Hijri failures separately. The PDF contains estimated future dates and the mosque may subsequently adjust its settings, so refresh rather than treating future dates as authoritative. Do not call Mawaqit's restricted API from this project.

## Weather and location

Use the Mawaqit mosque configuration as the first location source. During a successful mosque fetch, inspect `confData` for latitude, longitude, and location name. If coordinates are not available, use a persisted configured location such as Wuppertal; do not repeatedly guess the location from the network.

Optional automatic fallback may use IP geolocation once, save the result, and require confirmation before replacing a configured location. IP geolocation is approximate and must not silently change the prayer location.

Weather data must be fetched by a backend/service component, not directly from the QML client. Save it atomically in `cache/weather.json` with provider, coordinates, fetched time, current conditions, hourly values for today, and the next update time. Keep the last valid weather data when the network is unavailable and show its age on the weather screen.

The Weather Company/weather.com API supports hourly forecasts by geocode, but it requires an API key and the forecast duration depends on the account/license. The key must be stored outside GitHub code/configuration and requests must be made only by the backend. Do not scrape weather.com pages. [Weather Company authentication](https://developer.weather.com/docs/authentication) and [hourly forecast API](https://developer.weather.com/docs/openapi/hourly-forecast-3-0).

If a Weather Company key is not configured, use a provider adapter such as Open-Meteo for development or fallback. Open-Meteo provides hourly temperature and WMO weather-code fields by latitude/longitude. The provider must be configurable so that switching providers does not change the dashboard data model. [Open-Meteo documentation](https://open-meteo.com/en/docs).

Suggested refresh policy:

- current conditions: every 15 minutes;
- hourly forecast: every 60 minutes;
- retry failures with bounded backoff;
- keep cached weather until replaced by a valid response.

## Status and storage

Publish separate atomic JSON files:

- `cache/status.json` for schedule/Hijri state;
- `cache/weather.json` for weather state;
- `cache/quran_library.json` for the discovered album/track index;
- `config/quran_settings.json` for selected album/track and playback preferences;
- `config/audio_volume_profiles.json` for Athan and Qur’an levels.

The dashboard must tolerate missing, malformed, or partially replaced files. Use temporary-file-then-replace writes. Do not make the UI import scheduler or audio implementation modules.

## Tests and acceptance criteria

Test without the physical screen, internet, or speaker using fake cache files and a fake Unix socket. Cover:

- normal schedule, cached schedule, retrying update, final failure, and recovery;
- sunrise and Hijri values across midnight, year rollover, and daylight-saving transitions;
- Hijri provider failure without losing prayer times;
- location discovery, configured-location override, weather cache, stale weather, and provider failure;
- album discovery, natural track ordering, metadata fallback, missing artwork, empty albums, and rescan;
- single-tap Previous replay, double-tap Previous, Next, timer expiry, and playback restart;
- separate Athan/Qur’an volume switching and restoration;
- physical-dashboard and virtual-VNC output placement, touch mapping, VNC reconnect, reboot, dashboard restart, and service independence.

The dashboard is complete when it can remain visible on the physical touchscreen during normal VNC work on the virtual output, show cached data during outages, and use only the existing audio service for playback and volume routing.

## Existing references

- `src/athan.py` — scheduler and audio socket client.
- `src/calendar_manager.py` — Mawaqit fetch and rolling calendar.
- `src/athan_audio.py` — continuous PipeWire service and current volume protocol.
- `src/audio_device.py` — PipeWire sink discovery and volume operations.
- `src/setting_audio_device.py` — configured output selection.
- `src/set_volume.py` — command-line volume controls.
- `config/Fajr_first_athan.json` — optional first-Fajr reminder.
