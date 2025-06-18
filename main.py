import os
import sys
import time
import logging
import configparser
from pathlib import Path
from ytm_archiver.logging_setup import setup_logging
from ytm_archiver.state_manager import StateManager
from ytm_archiver.youtube_handler import YouTubeHandler
from ytm_archiver.rclone_handler import RcloneHandler

CONFIG_FILE = 'config.ini'


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
    rclone = RcloneHandler(remote_name=remote_name, remote_path=remote_path)
    if not rclone.check_connection():
        logger.error("Failed to connect to rclone remote. Please check your rclone configuration.")
        sys.exit(1)

    # Initial Sync
    logger.info("Starting initial sync (full channel scan)...")
    videos = yt.fetch_video_list(max_results=5000)
    logger.info(f"Found {len(videos)} videos on channel.")
    for video in videos:
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
    while True:
        try:
            videos = yt.fetch_video_list(max_results=50)
            for video in videos:
                video_id = video['video_id']
                title = video['title']
                published_at = video.get('publishedAt', '')
                if not state_db.is_archived(video_id):
                    logger.info(f"New video detected: {title} ({video_id})")
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
            logger.info(f"Sleeping for {polling_interval} minutes...")
            time.sleep(polling_interval * 60)
        except Exception as e:
            logger.error(f"Fatal error in monitoring loop: {e}")
            time.sleep(60)

if __name__ == '__main__':
    main()
