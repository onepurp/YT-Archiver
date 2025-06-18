import os
import sys
import time
import logging
import signal
from concurrent.futures import ThreadPoolExecutor, as_completed
import configparser
from pathlib import Path
from yt_archiver.logging_setup import setup_logging
from yt_archiver.state_manager import StateManager
from yt_archiver.youtube_handler import YouTubeHandler
from yt_archiver.rclone_handler import RcloneHandler

CONFIG_FILE = 'config.ini'

# Global flag for graceful shutdown
shutdown_requested = False

def signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM gracefully by setting a global flag."""
    global shutdown_requested
    shutdown_requested = True
    logging.getLogger(__name__).info("Shutdown signal received. Waiting for current operations to finish...")


def load_config():
    config = configparser.ConfigParser()
    if not config.read(CONFIG_FILE):
        print(f"Config file '{CONFIG_FILE}' not found or invalid. Please copy 'config.ini.template' and fill in your details.")
        sys.exit(1)
    return config


def main():
    config = load_config()
    log_file = config.get('Archiver', 'log_file', fallback='archiver.log')
    logger = setup_logging(log_file)
    logger.info("YTM-Archiver started.")

    # Load config values
    channel_url = config['YouTube']['channel_url']
    youtube_api_key = config['YouTube'].get('youtube_api_key', None)
    
    # Rclone configuration
    remote_name = config['Rclone'].get('remote_name', 'mega')
    remote_path = config['Rclone'].get('remote_path', '')

    polling_interval = config.getint('Archiver', 'polling_interval', fallback=60)
    download_dir = config.get('Archiver', 'download_dir', fallback='./downloads')
    os.makedirs(download_dir, exist_ok=True)

    # Initialize modules
    state_db = StateManager()
    yt = YouTubeHandler(channel_url, api_key=youtube_api_key)
    
    # Initialize Rclone handler
    # Register signal handlers only after logging is configured
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    rclone = RcloneHandler(remote_name=remote_name, remote_path=remote_path)
    if not rclone.check_connection():
        logger.error("Failed to connect to rclone remote. Please check your rclone configuration.")
        sys.exit(1)

    # Thread pool settings
    max_workers = config.getint('Archiver', 'max_workers', fallback=4)
    max_retries = config.getint('Archiver', 'max_retries', fallback=3)

    def archive_video(video_dict):
        """Download, upload and record a single video with retry logic."""
        vid = video_dict['video_id']
        tit = video_dict['title']
        pub = video_dict.get('publishedAt', '')
        if state_db.is_archived(vid):
            return f"Already archived {vid}", True
        retries = 0
        while retries < max_retries and not shutdown_requested:
            logger.info(f"Archiving {tit} ({vid}) [attempt {retries+1}/{max_retries}]")
            video_path = yt.download_video(vid, tit, download_dir)
            ok = video_path and rclone.upload_file(video_path)
            if ok:
                state_db.add_archived(vid, tit, pub)
                try:
                    os.remove(video_path)
                except Exception as e:
                    logger.warning(f"Could not delete temp file {video_path}: {e}")
                return f"Archived {vid}", True
            retries += 1
        return f"Failed to archive {vid} after {max_retries} retries", False

    # Initial Sync
    logger.info("Starting initial sync (full channel scan)...")
    videos = yt.fetch_video_list(max_results=5000)
    logger.info(f"Found {len(videos)} videos on channel.")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(archive_video, v): v for v in videos}
        for fut in as_completed(futures):
            msg, success = fut.result()
            (logger.info if success else logger.error)(msg)
        video_id = video['video_id']
        title = video['title']
        published_at = video.get('publishedAt', '')
        if not state_db.is_archived(video_id):
            logger.info(f"Archiving new video: {title} ({video_id})")
            video_path = yt.download_video(video_id, title, download_dir)
            if video_path and rclone.upload_file(video_path):
                state_db.add_archived(video_id, title, published_at)
                try:
                    os.remove(video_path)
                    logger.debug(f"Removed temporary file: {video_path}")
                except Exception as e:
                    logger.warning(f"Failed to remove temp file {video_path}: {e}")
            else:
                logger.error(f"Failed to archive video {title} ({video_id}). Will retry on next run.")
                # Keep the downloaded file for retry if it exists
                if video_path and os.path.exists(video_path):
                    logger.info(f"Keeping downloaded file for retry: {video_path}")
    logger.info("Initial sync complete.")

    # Continuous Monitoring Loop
    logger.info(f"Entering monitoring loop (interval: {polling_interval} min)...")
    while not shutdown_requested:
        try:
            videos = yt.fetch_video_list(max_results=50)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(archive_video, v): v for v in videos}
                for fut in as_completed(futures):
                    msg, success = fut.result()
                    (logger.info if success else logger.error)(msg)
            logger.info(f"Sleeping for {polling_interval} minutes or until shutdown...")
            # Sleep in small increments so we can react quickly to shutdown
            sleep_secs = polling_interval * 60
            slept = 0
            while slept < sleep_secs and not shutdown_requested:
                time.sleep(min(10, sleep_secs - slept))
                slept += 10
        except Exception as e:
            logger.error(f"Fatal error in monitoring loop: {e}")
            time.sleep(60)

if __name__ == '__main__':
    main()
