# YouTube-to-Mega Archival System (YTM-Archiver)

## Overview
YTM-Archiver is a robust, autonomous Python script that continuously archives all videos from a specified YouTube channel to a chosen Mega.nz folder. It is designed for long-term, unattended operation and can be set up as a background service on Linux, Windows, or macOS.

---

## Features
- **Modular architecture** with separate components for YouTube, Mega.nz, and state management.
- **Efficient state tracking** using a local SQLite database (no scanning of Mega folders).
- **Configurable** via `config.ini` (no hardcoded credentials).
- **Resilient**: Handles errors, retries with exponential backoff, and logs all actions.
- **Efficient polling**: Fetches only recent videos after initial sync.
- **Automatic cleanup**: Deletes local files after successful upload.
- **Log rotation** for easy long-term monitoring.

---

## Prerequisites
- Python 3.8+
- Mega.nz account
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
- Linux: `sudo apt install python3.10 python3.10-venv` _(Note: If python3.10 is not available in the default repositories, you can add the 'deadsnakes' PPA which contains multiple versions of python: `sudo add-apt-repository ppa:deadsnakes/ppa`)_
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

### 3. Configure the Archiver
- Copy the template config and fill in your details:
  ```sh
  cp config.ini.template config.ini
  ```
- Edit `config.ini` and fill in:
  - YouTube channel URL or ID
  - (Optional) YouTube API key
  - Mega.nz email and password
  - Target Mega folder
  - Polling interval (in minutes)

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
- **Never share your config.ini** (contains credentials).
- Use a dedicated Mega.nz account or app password for automation.
- Set up the script as a service for maximum reliability.

---

## License
MIT
