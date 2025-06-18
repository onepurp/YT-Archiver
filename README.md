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

## Rclone Setup

### 1. Install Rclone
Follow the official installation guide for your operating system:
- **Linux/macOS**: `curl https://rclone.org/install.sh | sudo bash`
- **Windows**: Download and install from [rclone.org/downloads](https://rclone.org/downloads/)
- **macOS (Homebrew)**: `brew install rclone`

### 2. Configure Rclone for Your Cloud Storage
Run the interactive configuration:
```bash
rclone config
```

#### Example: Configuring Mega.nz
1. Select `n` to create a new remote
2. Enter a name (e.g., `mega`)
3. Choose storage type: `mega` (type the number or name)
4. Enter your Mega.nz email and password when prompted
5. Leave other settings as default by pressing Enter
6. Type `y` to confirm
7. Type `q` to quit the configuration

### 3. Verify Your Setup
Test the connection to your remote:
```bash
# List root directories
rclone lsd remote:

# Create a test directory
rclone mkdir remote:/test

# Copy a test file
rclone copy /path/to/local/file.txt remote:/test

# List the test directory
rclone ls remote:/test

# Clean up
rclone rmdir remote:/test
```

### 4. Important Notes
- Rclone configuration is stored in `~/.config/rclone/rclone.conf` (Linux/macOS) or `%APPDATA%\rclone\rclone.conf` (Windows)
- For production use, consider using environment variables or rclone's built-in password manager for credentials
- Ensure your remote storage has enough space for the archived videos

---

## Setup Instructions

### 1. Clone the Repository
```sh
git clone https://github.com/onepurp/YT-Archiver
cd YT-Archiver
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

### 5. Configure the Archiver
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

### 6. First Run
```sh
python3 main.py
```
- The script will perform an initial sync (download/upload all channel videos), then enter a monitoring loop.

---

## Performance Tips

- **For large channels**: The initial sync might take a long time. Be patient.
- **Bandwidth control**: Add `--bwlimit 1M` to the rclone commands in `rclone_handler.py` if you need to limit bandwidth usage.
- **Concurrent transfers**: The script is set up for single-file transfers. For better performance, you might want to increase concurrency in the rclone configuration.

## Running as a Background Service

### Linux (systemd)
1. Edit `yt-archiver.service`:
    - Set `WorkingDirectory` and `ExecStart` paths as needed.
    - Set `User` to your username.
2. Copy the file:
    ```sh
    sudo cp yt-archiver.service /etc/systemd/system/yt-archiver.service
    sudo systemctl daemon-reload
    sudo systemctl enable yt-archiver
    sudo systemctl start yt-archiver
    sudo systemctl status yt-archiver
    ```

### Windows (Task Scheduler)
- Use Task Scheduler to create a new task:
    - **Action**: Run `pythonw.exe` with the full path to `main.py` as argument.
    - **Trigger**: At startup, repeat every X minutes.
    - **Conditions**: Set to restart on failure.
    - **Working Directory**: Set to the script folder.

### macOS (launchd)
1. Edit `yt-archiver.plist`:
    - Set all paths to your user directory and Python installation.
2. Copy and load:
    ```sh
    cp ytm-archiver.plist ~/Library/LaunchAgents/com.yt-archiver.plist
    launchctl load ~/Library/LaunchAgents/com.yt-archiver.plist
    launchctl start com.yt-archiver
    ```

---

## Troubleshooting

### Common Issues

#### Rclone Connection Issues
- **Error: Remote not found**
  - Verify the remote name in `config.ini` matches your rclone configuration
  - Run `rclone config` to check your configured remotes

#### Upload Failures
- **Permission Denied**
  - Ensure your rclone remote has write permissions
  - Check if the remote path exists and is writable

#### YouTube API Issues
- **Quota Exceeded**
  - The script will automatically fall back to yt-dlp if the API key is not set or quota is exceeded
  - Consider getting a YouTube API key for better reliability

#### Logs and Debugging
- Check the log file specified in your `config.ini` (default: `archiver.log`)
- For more verbose logging, modify the logging level in `logging_setup.py`
- Run the script with `-v` or `--verbose` for more detailed output

### Maintenance
- The script maintains a local SQLite database (`archiver.db`) to track uploaded videos
- Periodically check the log file for any warnings or errors
- Monitor your cloud storage usage to ensure you don't run out of space

---

## Security & Best Practices

### Configuration Security
- **Never share your `config.ini`** - It may contain sensitive information
- **Secure rclone config** - Your rclone configuration contains credentials:
  ```bash
  chmod 600 ~/.config/rclone/rclone.conf
  ```
- **Use app passwords** where possible instead of your main account credentials
- **Environment variables** - Consider using environment variables for sensitive data

### Operational Security
- **Run as a dedicated user** - Don't run the script as root
- **Log rotation** - Implement log rotation for the log file
- **Backup your database** - The `archiver.db` file contains your sync state
- **Monitor disk space** - Ensure your system has enough space for temporary downloads

### Cloud Storage Best Practices
- **Use separate accounts** for automation if possible
- **Set up notifications** for storage limits
- **Regularly verify** that uploads are working as expected
- **Consider versioning** in your cloud storage to prevent accidental data loss

---

## Getting Help

If you encounter any issues or have questions:

1. Check the [rclone documentation](https://rclone.org/docs/)
2. Review the log files for error messages
3. Search for similar issues in the [GitHub issues](https://github.com/yourusername/yt-archiver/issues)
4. Open a new issue with detailed information about your problem

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License

