import re
import yt_dlp
import logging
import time
import random
from typing import List, Dict, Optional, Tuple
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta

@dataclass
class VideoInfo:
    video_id: str
    title: str
    published_at: str
    duration: Optional[int] = None
    filesize: Optional[int] = None
    resolution: Optional[str] = None

class YouTubeHandler:
    def __init__(self, channel_url: str, api_key: Optional[str] = None, max_workers: int = 3):
        """
        Initialize YouTube handler.
        
        Args:
            channel_url: YouTube channel URL or ID
            api_key: Optional YouTube Data API v3 key
            max_workers: Maximum number of concurrent downloads (1-5)
        """
        self.channel_url = channel_url
        self.api_key = api_key
        self.max_workers = max(1, min(max_workers, 5))  # Limit max workers to 5
        self.logger = logging.getLogger(__name__)
        self._last_api_call = datetime.min
        self._rate_limit_delay = 1.0  # Start with 1 second delay between API calls
        self._session = requests.Session()
        self._session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

    def _rate_limit(self):
        """Enforce rate limiting between API calls."""
        now = datetime.now()
        time_since_last_call = (now - self._last_api_call).total_seconds()
        if time_since_last_call < self._rate_limit_delay:
            sleep_time = self._rate_limit_delay - time_since_last_call
            time.sleep(max(0, sleep_time))
        self._last_api_call = datetime.now()

    def fetch_video_list(self, max_results: int = 50) -> List[VideoInfo]:
        """
        Fetch list of videos from the channel.
        
        Args:
            max_results: Maximum number of videos to return
            
        Returns:
            List of VideoInfo objects
        """
        try:
            if self.api_key:
                videos = self._fetch_videos_api(max_results)
            else:
                videos = self._fetch_videos_yt_dlp(max_results)
            
            # Sort by published date (newest first)
            return sorted(videos, key=lambda x: x.published_at, reverse=True)
            
        except Exception as e:
            self.logger.error(f"Error fetching video list: {e}")
            return []

    def _fetch_videos_api(self, max_results: int) -> List[VideoInfo]:
        """Fetch videos using YouTube Data API v3."""
        try:
            self._rate_limit()
            
            # Extract channel ID if given a URL
            channel_id = self.channel_url
            if 'youtube.com' in self.channel_url:
                if '/channel/' in self.channel_url:
                    channel_id = self.channel_url.split('/channel/')[1].split('/')[0]
                elif '/user/' in self.channel_url or '/c/' in self.channel_url:
                    # Use yt-dlp fallback for user/c URLs
                    return self._fetch_videos_yt_dlp(max_results)
            
            # First, get the uploads playlist ID
            channels_url = (
                f"https://www.googleapis.com/youtube/v3/channels"
                f"?part=contentDetails&id={channel_id}&key={self.api_key}"
            )
            
            response = self._session.get(channels_url, timeout=30)
            response.raise_for_status()
            
            uploads_playlist_id = response.json()['items'][0]['contentDetails']['relatedPlaylists']['uploads']
            
            # Now get the videos from the uploads playlist
            videos = []
            next_page_token = None
            
            while len(videos) < max_results:
                self._rate_limit()
                
                playlist_url = (
                    f"https://www.googleapis.com/youtube/v3/playlistItems"
                    f"?part=snippet,contentDetails&playlistId={uploads_playlist_id}"
                    f"&maxResults={min(50, max_results - len(videos))}&key={self.api_key}"
                )
                
                if next_page_token:
                    playlist_url += f"&pageToken={next_page_token}"
                
                response = self._session.get(playlist_url, timeout=30)
                response.raise_for_status()
                data = response.json()
                
                # Process video items
                for item in data.get('items', []):
                    if item['snippet']['resourceId']['kind'] == 'youtube#video':
                        video_id = item['contentDetails']['videoId']
                        videos.append(VideoInfo(
                            video_id=video_id,
                            title=item['snippet']['title'],
                            published_at=item['contentDetails'].get('videoPublishedAt', '')
                        ))
                
                next_page_token = data.get('nextPageToken')
                if not next_page_token or len(videos) >= max_results:
                    break
            
            return videos
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"YouTube API request failed: {e}")
            # Fall back to yt-dlp if API fails
            if not isinstance(e, requests.exceptions.HTTPError) or e.response.status_code != 403:
                return self._fetch_videos_yt_dlp(max_results)
            raise

    def _fetch_videos_yt_dlp(self, max_results: int) -> List[VideoInfo]:
        """Fetch videos using yt-dlp as a fallback."""
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'dump_single_json': True,
            'playlistend': max_results,
            'ignoreerrors': True,
            'no_warnings': True,
            'skip_download': True
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                self.logger.info(f"Fetching videos using yt-dlp from: {self.channel_url}")
                info = ydl.extract_info(self.channel_url, download=False)
                
                if not info:
                    self.logger.error("No data returned from yt-dlp")
                    return []
                    
                entries = info.get('entries', [])
                if not entries:
                    # Handle case where there's only one video
                    if 'id' in info and 'title' in info:
                        entries = [info]
                    else:
                        self.logger.error("No video entries found in channel")
                        return []
                
                videos = []
                for entry in entries[:max_results]:
                    try:
                        # Extract upload date in ISO format if available
                        upload_date = entry.get('upload_date', '')
                        if upload_date and len(upload_date) == 8:  # Format: YYYYMMDD
                            upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}T00:00:00Z"
                        
                        videos.append(VideoInfo(
                            video_id=entry.get('id', ''),
                            title=entry.get('title', 'Untitled'),
                            published_at=upload_date,
                            duration=entry.get('duration'),
                            filesize=entry.get('filesize_approx'),
                            resolution=entry.get('resolution')
                        ))
                    except Exception as e:
                        self.logger.warning(f"Error processing video entry: {e}")
                        continue
                
                self.logger.info(f"Fetched {len(videos)} videos using yt-dlp")
                return videos
                
        except Exception as e:
            self.logger.error(f"yt-dlp error fetching video list: {e}")
            return []

    @staticmethod
    def sanitize_filename(title: str) -> str:
        """
        Sanitize filename by removing invalid characters and truncating if too long.
        
        Args:
            title: Original title to sanitize
            
        Returns:
            Sanitized filename
        """
        # Remove invalid characters
        sanitized = re.sub(r'[\\/:*?\"<>|]', '_', title)
        
        # Truncate if too long (max 150 chars to leave room for IDs and extensions)
        max_length = 150
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length].rsplit(' ', 1)[0]  # Try to break at word boundary
            
        return sanitized.strip()
    
    def _progress_hook(self, d):
        """Callback for download progress."""
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', 'N/A')
            speed = d.get('_speed_str', 'N/A')
            eta = d.get('_eta_str', 'N/A')
            self.logger.debug(f"Downloading: {percent} at {speed}, ETA: {eta}")
        elif d['status'] == 'finished':
            self.logger.debug("Download finished, starting post-processing...")
    
    def _get_format_selector(self, quality: str) -> str:
        """Get format selector based on quality preference."""
        quality = quality.lower()
        if quality == 'best':
            return 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        elif quality == '1080p':
            return 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best'
        elif quality == '720p':
            return 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best'
        elif quality == '480p':
            return 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best'
        elif quality == 'audio':
            return 'bestaudio[ext=m4a]/bestaudio/best'
        else:
            # Default to best quality if unknown quality specified
            return 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    
    def get_available_qualities(self, video_id: str) -> Dict[str, str]:
        """Get available quality options for a video."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'listformats': True,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                formats = info.get('formats', [])
                
                # Extract unique resolutions
                resolutions = {}
                for f in formats:
                    if f.get('height'):
                        res = f"{f['height']}p"
                        if res not in resolutions:
                            resolutions[res] = f"{f.get('ext', 'mp4')} @ {f.get('fps', 0)}fps"
                
                return dict(sorted(resolutions.items(), key=lambda x: int(x[0].replace('p', '')), reverse=True))
                
        except Exception as e:
            self.logger.error(f"Error getting available qualities: {e}")
            return {}
    
    def download_video(self, video_id: str, title: str, output_dir: str, 
                      quality: str = 'best', max_retries: int = 3) -> Optional[str]:
        """
        Download a single video.
        
        Args:
            video_id: YouTube video ID
            title: Video title (for filename)
            output_dir: Directory to save the video
            quality: Video quality preference (best, 1080p, 720p, etc.)
            max_retries: Maximum number of download retries
            
        Returns:
            Path to the downloaded file or None if download failed
        """
        url = f"https://www.youtube.com/watch?v={video_id}"
        safe_title = self.sanitize_filename(title)
        
        # Format selection based on quality preference
        format_selector = self._get_format_selector(quality)
        
        ydl_opts = {
            'outtmpl': f"{output_dir}/{safe_title} [{video_id}].%(ext)s",
            'format': format_selector,
            'merge_output_format': 'mp4',
            'quiet': False,  # We want to see errors and warnings
            'noplaylist': True,
            'retries': max_retries,
            'fragment_retries': 10,
            'file_access_retries': 3,
            'continuedl': True,
            'sleep_interval_requests': 1,
            'max_sleep_interval_requests': 5,
            'postprocessors': [{
                'key': 'FFmpegMetadata',
                'add_metadata': True,
            }, {
                'key': 'EmbedThumbnail',
                'already_have_thumbnail': False,
            }],
            'writethumbnail': True,
            'writesubtitles': False,
            'writeautomaticsub': False,
            'subtitleslangs': ['en'],
            'ignoreerrors': False,
            'no_warnings': False,
            'windowsfilenames': True,
            'restrictfilenames': False,
            'consoletitle': False,
            'noprogress': True,
            'progress_hooks': [self._progress_hook],
        }
        
        for attempt in range(max_retries + 1):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Add retry delay for subsequent attempts
                    if attempt > 0:
                        retry_delay = min(2 ** attempt, 60)  # Exponential backoff, max 60s
                        self.logger.info(f"Retry {attempt}/{max_retries} in {retry_delay}s...")
                        time.sleep(retry_delay)
                    
                    self.logger.info(f"Downloading video {video_id} (attempt {attempt + 1}/{max_retries + 1})")
                    result = ydl.extract_info(url, download=True)
                    
                    if result:
                        filename = ydl.prepare_filename(result)
                        self.logger.info(f"Successfully downloaded: {filename}")
                        return filename
                    
            except yt_dlp.DownloadError as e:
                self.logger.error(f"Download error (attempt {attempt + 1}/{max_retries + 1}): {e}")
                if attempt == max_retries:
                    self.logger.error(f"Failed to download video {video_id} after {max_retries} attempts")
            except Exception as e:
                self.logger.error(f"Unexpected error downloading video {video_id}: {e}")
                if attempt == max_retries:
                    self.logger.exception("Max retries reached, giving up")
        
        return None
    
    def download_videos(self, video_infos: List[VideoInfo], output_dir: str, 
                        quality: str = 'best', max_workers: Optional[int] = None) -> Dict[str, str]:
        """
        Download multiple videos in parallel.
        
        Args:
            video_infos: List of VideoInfo objects
            output_dir: Directory to save the videos
            quality: Video quality preference (best, 1080p, 720p, etc.)
            max_workers: Maximum number of concurrent downloads
            
        Returns:
            Dictionary mapping video IDs to their downloaded file paths
        """
        if not video_infos:
            return {}
            
        max_workers = min(max_workers or self.max_workers, 5)  # Cap at 5 workers
        results = {}
        
        self.logger.info(f"Starting parallel download of {len(video_infos)} videos with {max_workers} workers")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Create a future for each download
            future_to_video = {
                executor.submit(
                    self.download_video, 
                    video.video_id, 
                    video.title, 
                    output_dir,
                    quality
                ): video.video_id 
                for video in video_infos
            }
            
            # Process completed downloads
            for future in as_completed(future_to_video):
                video_id = future_to_video[future]
                try:
                    filepath = future.result()
                    if filepath:
                        results[video_id] = filepath
                except Exception as e:
                    self.logger.error(f"Error downloading video {video_id}: {e}")
        
        return results
