# Athan on Raspberry Pi

## Purpose and how the project works

This project turns a Raspberry Pi into an automatic prayer-time announcer. It downloads the yearly prayer calendar from a Mawaqit mosque page, stores a local rolling cache, and plays the appropriate Athan at Fajr, Dhuhr, Asr, Maghrib, and Isha.

There are two long-running services:

1. `athan.service` runs `src/athan.py`. It fetches and validates the calendar, keeps the cache current, handles midnight rollover and the 00:01 verification, and tells the audio service when a prayer is due.
2. `athan-audio.service` runs `src/athan_audio.py`. It opens one continuous PipeWire PCM output stream, feeds it silence when idle, and inserts decoded MP3 audio when commanded. This persistent sound tunnel avoids opening a new audio stream for every prayer.

The services communicate through `cache/athan-audio.sock`. Fajr uses `audio/Adhan Alfajr.mp3`; the other prayers use `audio/Adhan.mp3`. The default output can be a Bluetooth, USB, wired, or other PipeWire-managed sound device.

## Project layout

```text
piAthan/
├── README.md
├── ENHANCEMENT_PLAN.md
├── audio/
│   ├── Adhan Alfajr.mp3
│   └── Adhan.mp3
├── config/
│   └── mosque_url.txt          # optional; created by set_mosque_url.py
├── src/
│   ├── athan.py                # scheduler and prayer triggers
│   ├── athan_audio.py          # continuous PCM audio service
│   ├── calendar_manager.py     # Mawaqit parser and cache management
│   └── set_mosque_url.py       # command-line mosque URL editor
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

For a Bluetooth output, pair and connect it using the normal PipeWire/Bluetooth tools before enabling the services. For a wired or USB output, make it the default PipeWire sink. The application uses whichever output PipeWire reports as the default.

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

The test performs a real Mawaqit fetch, exercises cache rollover and verification, tests deterministic retry success and failure paths, and then sends a real playback command to `athan-audio.service`:

```bash
cd /home/qais/piAthan
python3 test/test_athan.py Fajr
```

Choose `Dhuhr`, `Asr`, `Maghrib`, or `Isha` to test the other audio file. The test asks whether the Athan was heard and writes a timestamped report under `test/Logs/`.

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
