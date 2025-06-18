# YouTube Archival System with Rclone (YT-Archiver)

## Overview
YT-Archiver is a robust, autonomous Python script that continuously archives all videos from a specified YouTube channel to your preferred cloud storage using Rclone. It is designed for long-term, unattended operation and can be set up as a background service on Linux, Windows, or macOS.

---

## Features
- **Modular architecture** with separate components for YouTube, Rclone integration, and state management.
- **Efficient state tracking** using a local SQLite database (no scanning of remote folders).
- **Configurable** via `config.ini` (no hardcoded credentials).
- **Resilient**: Handles errors, retries with exponential backoff, and logs all actions.
- **Efficient polling**: Fetches only recent videos after initial sync.
- **Automatic cleanup**: Deletes local files after successful upload.
- **Log rotation** for easy long-term monitoring.

---

## Prerequisites
- Python 3.8+
- [Rclone](https://rclone.org/) installed and configured with your cloud storage provider
- (Optional) YouTube Data API v3 key for improved reliability

---

## Setup Instructions

### 1. Clone the Repository
```sh
git clone https://github.com/onepurp/YTM-Archiver
cd YTM-Archiver
```

### 2. Install Dependencies

**Install Python 3.10+ (if not already installed)**
- Linux: `sudo apt install python3.10 python3.10-venv`
- macOS: `brew install python@3.10`
- Windows: [Download from python.org](https://www.python.org/downloads/release/python-3100/)

**Install ffmpeg (required for video merging):**
- Linux: `sudo apt install ffmpeg`
- macOS: `brew install ffmpeg`
- Windows: [Download from ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH

**Create and activate a virtual environment:**
```sh
python3.10 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

**Install Python dependencies:**
```sh
pip install -r requirements.txt
```

### 4. Configure the Archiver
1. Copy the template config:
   ```sh
   cp config.ini.template config.ini
   ```
2. Edit `config.ini` and configure:
   - `[YouTube]` section:
     - `channel_url`: YouTube channel URL or ID
     - `youtube_api_key`: (Optional) YouTube API key for better reliability
   - `[Rclone]` section:
     - `remote_name`: Name of your rclone remote (default: 'mega')
     - `remote_path`: Path on the remote storage (e.g., '/YouTubeBackups/ChannelName')
   - `[Archiver]` section:
     - `polling_interval`: How often to check for new videos (in minutes)
     - `download_dir`: Local directory for temporary downloads
     - `log_file`: Path to log file

### 4. First Run
```sh
python3 main.py
```
- The script will perform an initial sync (download/upload all channel videos), then enter a monitoring loop.

---

## Running as a Background Service

### Linux (systemd)
1. Edit `ytm-archiver.service`:
    - Set `WorkingDirectory` and `ExecStart` paths as needed.
    - Set `User` to your username.
2. Copy the file:
    ```sh
    sudo cp ytm-archiver.service /etc/systemd/system/ytm-archiver.service
    sudo systemctl daemon-reload
    sudo systemctl enable ytm-archiver
    sudo systemctl start ytm-archiver
    sudo systemctl status ytm-archiver
    ```

### Windows (Task Scheduler)
- Use Task Scheduler to create a new task:
    - **Action**: Run `pythonw.exe` with the full path to `main.py` as argument.
    - **Trigger**: At startup, repeat every X minutes.
    - **Conditions**: Set to restart on failure.
    - **Working Directory**: Set to the script folder.

### macOS (launchd)
1. Edit `ytm-archiver.plist`:
    - Set all paths to your user directory and Python installation.
2. Copy and load:
    ```sh
    cp ytm-archiver.plist ~/Library/LaunchAgents/com.ytm-archiver.plist
    launchctl load ~/Library/LaunchAgents/com.ytm-archiver.plist
    launchctl start com.ytm-archiver
    ```

---

## Troubleshooting
- Check `archiver.log` for errors and progress.
- Ensure your Mega.nz credentials are correct and not 2FA-protected.
- If using the YouTube API, ensure your key is valid and quota is sufficient.
- For large channels, the initial sync may take several hours.

---

## Security & Best Practices
- **Never share your config.ini**
- Ensure your rclone config is properly secured (usually at `~/.config/rclone/rclone.conf`)
- Use appropriate access controls for your cloud storage account
- Set up the script as a service for maximum reliability.

---

## License
MIT
