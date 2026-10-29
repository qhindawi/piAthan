# Athan on Raspberry Pi

## Purpose and how the project works

This project turns a Raspberry Pi into an automatic prayer-time announcer. It downloads the yearly prayer calendar from a Mawaqit mosque page, stores a local rolling cache, and plays the appropriate Athan at Fajr, Dhuhr, Asr, Maghrib, and Isha.

There are two long-running services:

1. `athan.service` runs `src/athan.py`. It fetches and validates the calendar, keeps the cache current, handles midnight rollover and the 00:01 verification, and tells the audio service when a prayer is due.
2. `athan-audio.service` runs `src/athan_audio.py`. It opens one continuous PipeWire PCM output stream, feeds it silence when idle, and inserts decoded MP3 audio when commanded. This persistent sound tunnel avoids opening a new audio stream for every prayer.

The services communicate through `cache/athan-audio.sock`. Fajr uses `audio/Adhan Alfajr.mp3`; the other prayers use `audio/Adhan.mp3`. The default output can be a Bluetooth, USB, wired, or other PipeWire-managed sound device.

An optional first-Fajr reminder is configured independently in `config/Fajr_first_athan.json`. When enabled, `athan.service` plays `audio/Adhan.mp3` the configured number of minutes before the official Fajr Athan. The setting is reloaded automatically when the file changes.

## Project layout

```text
piAthan/
├── README.md
├── ENHANCEMENT_PLAN.md
├── deploy_project.py         # compile/install/restart/report utility
├── audio/
│   ├── Adhan Alfajr.mp3
│   └── Adhan.mp3
├── config/
│   ├── mosque_url.txt          # optional; created by set_mosque_url.py
│   └── Fajr_first_athan.json   # optional; created by set_Fajr_first_athan.py
├── src/
│   ├── athan.py                # scheduler and prayer triggers
│   ├── athan_audio.py          # continuous PCM audio service
│   ├── calendar_manager.py     # Mawaqit parser and cache management
│   ├── set_mosque_url.py       # command-line mosque URL editor
│   └── set_Fajr_first_athan.py # first-Fajr reminder configuration
├── athan.services/
│   ├── athan.service
│   └── athan-audio.service
└── test/
    └── test_athan.py
```

Runtime-created `cache/`, `logs/`, and test report files are intentionally not required in the downloaded project. The programs create their directories when needed.

## Requirements

On Raspberry Pi OS or another Debian-based Pi system, install Python, FFmpeg, PipeWire, and the PipeWire command-line tools:

```bash
sudo apt update
sudo apt install -y python3 ffmpeg pipewire pipewire-pulse wireplumber pipewire-bin libspa-0.2-bluetooth
```

The Pi must have internet access, a working PipeWire session for the service user, the two MP3 files in `audio/`, and a configured default PipeWire output device. No third-party Python packages are required.

## Install on the Pi

The project is designed to be copied as one folder into the target user’s home directory. These service files use `/home/qais/piAthan`; replace `qais` and the folder name in both service files if your deployment differs.

From Windows PowerShell, copy the complete project folder to the Pi. Replace `PI_IP` with the Pi’s hostname or IP address:

```powershell
scp -r "C:\Users\qais_\Documents\ChatGPT\piAthan" qais@PI_IP:/home/qais/
```

Then connect to the Pi and enter the copied project:

```bash
cd /home/qais/piAthan
chmod +x src/*.py
ls -l audio/Adhan.mp3 "audio/Adhan Alfajr.mp3"
```

Enable the `qais` user session to remain available for the system services:

```bash
id -u qais
sudo loginctl enable-linger qais
```

The service files currently use `/run/user/1000`. If `id -u qais` prints a value other than `1000`, replace `1000` with that value in both files before installing them.

### Select the mosque URL

The default URL is for the project’s original mosque. To use another Mawaqit page, pass the full page URL to the helper script:

```bash
python3 src/set_mosque_url.py \
  https://mawaqit.net/en/mosque-name-city-country
```

A valid URL looks like:

```text
https://mawaqit.net/en/haus-des-friedens-dr-lslm-wuppertal-42103-germany
```

It must use `https://mawaqit.net/en/` followed by the mosque’s page slug. The command saves the URL in `config/mosque_url.txt`; restart `athan.service` after changing it. The scheduler reads this file at fetch time, so no Python source edit is needed.

### Configure the optional first-Fajr reminder

Run the helper without arguments to answer both prompts:

```bash
python3 src/set_Fajr_first_athan.py
```

Or pass the two values directly:

```bash
python3 src/set_Fajr_first_athan.py on 30
python3 src/set_Fajr_first_athan.py off 0
```

The first value enables or disables the reminder. The second value is the number of minutes before official Fajr. The helper writes `config/Fajr_first_athan.json`; `athan.py` notices the change automatically, so a service restart is not required.

### Configure the output and permanent sound tunnel

Log in as `qais` locally or through a session that starts PipeWire, then inspect available outputs:

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
wpctl status
```

If necessary, select the desired output by its numeric ID:

```bash
wpctl set-default OUTPUT_ID
```

The `athan-audio.service` unit starts `pw-cat` once and keeps it alive. It continuously sends silence and therefore provides the permanent sound tunnel. At prayer time, `athan.py` sends the MP3 path over the local Unix socket; `athan_audio.py` decodes it with FFmpeg and writes PCM into the already-open stream. If the stream exits, systemd restarts the service.

The audio service monitors PipeWire every two seconds. It prefers the sink named by `ATHAN_AUDIO_SINK_NAME` and moves the existing `pw-cat` stream to that sink when it appears or changes. The current unit sets this to `Echo Dot-2CW`. If the preferred Bluetooth sink is temporarily unavailable, the service logs that state and moves the stream when the sink returns. A new stream is not created for each prayer.

For a Bluetooth output, pair and connect it using the normal PipeWire/Bluetooth tools before enabling the services. For a wired or USB output, make it the default PipeWire sink. The application uses whichever output PipeWire reports as the default.

For a future wired speaker, remove or change `Environment=ATHAN_AUDIO_SINK_NAME=Echo Dot-2CW` in `athan.services/athan-audio.service` to the wired sink name, then reload the unit.

### Install and enable the systemd services

The service files contain the deployment path and service user. If the project is not `/home/qais/piAthan`, edit these values in both files before installing:

```text
User=qais
WorkingDirectory=/home/qais/piAthan
Environment=HOME=/home/qais
ExecStart=/usr/bin/python3 /home/qais/piAthan/src/...
```

Install and enable both services:

```bash
sudo install -m 644 athan.services/athan-audio.service /etc/systemd/system/athan-audio.service
sudo install -m 644 athan.services/athan.service /etc/systemd/system/athan.service
sudo systemctl daemon-reload
sudo systemctl enable --now athan-audio.service
sudo systemctl enable --now athan.service
```

Verify startup:

```bash
systemctl is-active athan-audio.service athan.service
sudo journalctl -u athan-audio.service -n 30 --no-pager
sudo journalctl -u athan.service -n 30 --no-pager
```

The scheduler creates `cache/` and `logs/` automatically. It first attempts a fresh yearly fetch. If the network is temporarily unavailable, it uses a valid current-day cache when one exists and retries in the background. At midnight it removes past dates from the rolling calendar; at 00:01 it checks today’s times again.

## Testing

### Deploy and diagnose the complete project

Run this on the Pi from the project directory:

```bash
cd /home/qais/piAthan
python3 deploy_project.py
```

The utility compiles every Python file, installs every unit in `athan.services/`, reloads systemd, restarts the services with the audio service first, checks service activity, prints the last 40 audio-service journal lines, and prints `wpctl status`. The complete result is also saved under `logs/deploy_report_*.log`.

To compile without changing services:

```bash
python3 deploy_project.py --compile-only
```

The test performs a real Mawaqit fetch, exercises cache rollover and verification, tests deterministic retry success and failure paths, and then sends a real playback command to `athan-audio.service`:

```bash
cd /home/qais/piAthan
python3 test/test_athan.py Fajr
```

Choose `Dhuhr`, `Asr`, `Maghrib`, or `Isha` to test the other audio file. The test asks whether the Athan was heard and writes a timestamped report under `test/Logs/`.

Use `first-fajr` to test the reminder audio path, which intentionally uses the regular `Adhan.mp3` track:

```bash
python3 test/test_athan.py first-fajr
```

Useful checks:

```bash
cat cache/today_prayer_times.json
wpctl status
systemctl status athan-audio.service athan.service
```

## Cache and logs

The scheduler writes a rolling `cache/<year>_prayer_times.json` containing today and future dates, plus `cache/today_prayer_times.json` for the active schedule. It also writes `logs/athan.log` and `logs/athan-audio.log`. These are runtime data and should normally remain out of version control.

## Project history and future work

See [ENHANCEMENT_PLAN.md](ENHANCEMENT_PLAN.md) for the work history, design decisions, known limitations, and proposed screen/status enhancements.
