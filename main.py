import os
import sys
import time
import logging
import signal
import json
import configparser
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from yt_archiver.logging_setup import setup_logging
from yt_archiver.state_manager import StateManager
from yt_archiver.youtube_handler import YouTubeHandler, VideoInfo
from yt_archiver.rclone_handler import RcloneHandler, UploadResult, UploadProgress

# Configuration
CONFIG_FILE = 'config.ini'
DEFAULT_CONFIG = {
    'Archiver': {
        'log_file': 'archiver.log',
        'polling_interval': '60',  # minutes
        'download_dir': './downloads',
        'max_workers': '4',
        'max_retries': '3',
        'dry_run': 'false',  # Set to 'true' to enable dry-run mode
    },
    'YouTube': {
        'channel_url': '',
        'youtube_api_key': '',  # Optional, falls back to yt-dlp
        'max_results': '5000',
        'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
    },
    'Rclone': {
        'remote_name': 'mega',
        'remote_path': '',
    },
}

# Global flag for graceful shutdown
shutdown_requested = False

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    shutdown_requested = True
    logging.info("Shutdown signal received. Finishing current operations...")


def load_config() -> configparser.ConfigParser:
    """Load and validate configuration."""
    config = configparser.ConfigParser()
    
    # Set defaults
    for section, options in DEFAULT_CONFIG.items():
        if section not in config:
            config[section] = {}
        for key, value in options.items():
            if key not in config[section]:
                config[section][key] = value
    
    # Read config file if it exists
    if os.path.exists(CONFIG_FILE):
        config.read(CONFIG_FILE)
    else:
        # Create default config if it doesn't exist
        with open(CONFIG_FILE, 'w') as f:
            config.write(f)
        print(f"Created default config file: {CONFIG_FILE}")
        print("Please edit the file with your configuration and restart the application.")
        sys.exit(0)
    
    # Validate required settings
    if not config['YouTube'].get('channel_url'):
        print("Error: 'channel_url' is required in the [YouTube] section of the config file.")
        sys.exit(1)
    
    return config


def process_video(
    video_info: VideoInfo,
    yt: YouTubeHandler,
    rclone: RcloneHandler,
    state_db: StateManager,
    download_dir: str,
    max_retries: int = 3,
    dry_run: bool = False
) -> Tuple[bool, str]:
    """Process a single video: download, upload, and clean up."""
    logger = logging.getLogger(__name__)
    video_id = video_info.video_id
    title = video_info.title
    
    for attempt in range(1, max_retries + 1):
        if shutdown_requested:
            return False, "Shutdown requested"
            
        try:
            if dry_run:
                logger.info(f"[DRY RUN] Would download video: {title}")
                video_path = os.path.join(download_dir, f"{title}.mp4")
                # Simulate successful download
                return True, f"[DRY RUN] Successfully processed: {title}"
                
            # Download the video
            logger.info(f"Downloading video (attempt {attempt}/{max_retries}): {title}")
            video_path = yt.download_video(video_id, title, download_dir)
            if not video_path or not os.path.exists(video_path):
                logger.error(f"Failed to download video: {title}")
                continue
                
            # Upload the video
            if dry_run:
                logger.info(f"[DRY RUN] Would upload video: {title} to remote path")
                result = UploadResult(
                    success=True,
                    local_path=video_path,
                    remote_path=f"{remote_name}:{remote_path}/{os.path.basename(video_path)}",
                    message="[DRY RUN] Upload successful"
                )
            else:
                logger.info(f"Uploading video: {title}")
                remote_path = f"{video_info.channel_name or 'uploads'}/{video_info.published_at[:4] if video_info.published_at else 'unknown'}"
                result = rclone.upload_file(video_path, remote_path)
            
            if result.success:
                if not dry_run:
                    # Update state and clean up on success
                    state_db.add_archived(
                        video_id=video_id,
                        title=title,
                        published_at=video_info.published_at or '',
                        remote_path=result.remote_path
                    )
                    try:
                        os.remove(video_path)
                        logger.debug(f"Removed temporary file: {video_path}")
                    except Exception as e:
                        logger.warning(f"Failed to remove temp file {video_path}: {e}")
                return True, f"{'[DRY RUN] ' if dry_run else ''}Successfully processed: {title}"
            else:
                logger.error(f"Upload failed (attempt {attempt}/{max_retries}): {title}")
                
        except Exception as e:
            logger.error(f"Error processing video {title}: {e}", exc_info=True)
            
    return False, f"Failed to process video after {max_retries} attempts: {title}"

def process_videos(
    videos: List[VideoInfo],
    yt: YouTubeHandler,
    rclone: RcloneHandler,
    state_db: StateManager,
    download_dir: str,
    max_workers: int = 4,
    max_retries: int = 3,
    dry_run: bool = False
) -> Tuple[int, int]:
    """Process multiple videos in parallel."""
    logger = logging.getLogger(__name__)
    success_count = 0
    failure_count = 0
    
    # Filter out already archived videos
    videos_to_process = [
        v for v in videos 
        if not state_db.is_archived(v.video_id)
    ]
    
    if not videos_to_process:
        logger.info("No new videos to process.")
        return 0, 0
    
    logger.info(f"Processing {len(videos_to_process)} videos with {max_workers} workers...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                process_video, 
                video, yt, rclone, state_db, download_dir, max_retries
            ): video 
            for video in videos_to_process
        }
        
        for future in as_completed(futures):
            if shutdown_requested:
                logger.info("Shutting down worker threads...")
                executor.shutdown(wait=False, cancel_futures=True)
                break
                
            video = futures[future]
            try:
                success, message = future.result()
                if success:
                    success_count += 1
                    logger.info(message)
                else:
                    failure_count += 1
                    logger.error(message)
                    
                # In dry-run mode, don't process too many videos
                if dry_run and (success_count + failure_count) >= 5:
                    logger.info("[DRY RUN] Processed 5 videos, stopping early")
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
            except Exception as e:
                failure_count += 1
                logger.error(f"Error processing video {video.title}: {e}", exc_info=True)
    
    return success_count, failure_count

def main():
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Load configuration
    config = load_config()
    
    # Set up logging
    log_file = config['Archiver']['log_file']
    dry_run = config['Archiver'].getboolean('dry_run', False)
    
    logger = setup_logging(log_file)
    logger.info(f"YouTube-Mega Sync started{' [DRY RUN]' if dry_run else ''}")
    
    if dry_run:
        logger.info("DRY RUN MODE: No actual downloads or uploads will be performed")
    
    # Load config values
    channel_url = config['YouTube']['channel_url']
    youtube_api_key = config['YouTube'].get('youtube_api_key') or None
    max_results = config['YouTube'].getint('max_results')
    video_format = config['YouTube'].get('format')
    
    # Rclone configuration
    remote_name = config['Rclone']['remote_name']
    remote_path = config['Rclone'].get('remote_path', '')
    
    # Archiver settings
    polling_interval = config['Archiver'].getint('polling_interval')
    download_dir = config['Archiver']['download_dir']
    max_workers = config['Archiver'].getint('max_workers')
    max_retries = config['Archiver'].getint('max_retries')
    
    # Create necessary directories
    os.makedirs(download_dir, exist_ok=True)
    
    try:
        # Initialize modules
        logger.info("Initializing components...")
        state_db = StateManager()
        yt = YouTubeHandler(
            channel_url=channel_url,
            api_key=youtube_api_key,
            format=video_format
        )
        
        rclone = RcloneHandler(
            remote_name=remote_name,
            remote_path=remote_path,
            max_workers=max_workers
        )
        
        # Test rclone connection
        if not rclone.check_connection():
            logger.error("Failed to connect to rclone remote. Please check your rclone configuration.")
            return 1
            
        # Initial sync
        logger.info("Starting initial sync...")
        try:
            videos = yt.fetch_video_list(max_results=max_results)
            logger.info(f"Found {len(videos)} videos on channel")
            
            success, failure = process_videos(
                videos=videos,
                yt=yt,
                rclone=rclone,
                state_db=state_db,
                download_dir=download_dir,
                max_workers=max_workers,
                max_retries=max_retries,
                dry_run=dry_run
            )
            
            logger.info(f"Initial sync complete. Success: {success}, Failed: {failure}")
            
            # Continuous monitoring loop
            logger.info(f"Entering monitoring loop (checking every {polling_interval} minutes)...")
            while not shutdown_requested:
                try:
                    # Check for new videos
                    videos = yt.fetch_video_list(max_results=50)  # Only check recent videos
                    success, failure = process_videos(
                        videos=videos,
                        yt=yt,
                        rclone=rclone,
                        state_db=state_db,
                        download_dir=download_dir,
                        max_workers=max_workers,
                        max_retries=max_retries,
                        dry_run=dry_run
                    )
                    
                    if success or failure:
                        logger.info(f"Processed {success + failure} videos. Success: {success}, Failed: {failure}")
                    
                    # Sleep until next check
                    for _ in range(polling_interval * 60):
                        if shutdown_requested:
                            break
                        time.sleep(1)
                            
                except Exception as e:
                    logger.error(f"Error in monitoring loop: {e}", exc_info=True)
                    time.sleep(60)  # Prevent tight loop on repeated errors
                    
        except Exception as e:
            logger.critical(f"Fatal error during initial sync: {e}", exc_info=True)
            return 1
            
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        return 1
        
    logger.info("Shutting down gracefully...")
    return 0

if __name__ == '__main__':
    main()
