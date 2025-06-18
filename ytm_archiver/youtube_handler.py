import re
import yt_dlp
import logging
import subprocess
from typing import List, Dict, Optional
import requests
import time

class YouTubeHandler:
    def __init__(self, channel_url: str, api_key: Optional[str] = None):
        self.channel_url = channel_url
        self.api_key = api_key
        self.logger = logging.getLogger(__name__)

    def fetch_video_list(self, max_results: int = 50) -> List[Dict]:
        if self.api_key:
            return self._fetch_videos_api(max_results)
        else:
            return self._fetch_videos_yt_dlp(max_results)

    def _fetch_videos_api(self, max_results: int) -> List[Dict]:
        # Extract channel ID if given a URL
        channel_id = self.channel_url
        if 'youtube.com' in self.channel_url:
            if '/channel/' in self.channel_url:
                channel_id = self.channel_url.split('/channel/')[1].split('/')[0]
            elif '/user/' in self.channel_url or '/c/' in self.channel_url:
                # Use yt-dlp fallback for user/c URLs
                return self._fetch_videos_yt_dlp(max_results)
        api_url = f"https://www.googleapis.com/youtube/v3/search?key={self.api_key}&channelId={channel_id}&part=snippet,id&order=date&maxResults={max_results}"
        try:
            resp = requests.get(api_url)
            resp.raise_for_status()
            data = resp.json()
            videos = []
            for item in data.get('items', []):
                if item['id']['kind'] == 'youtube#video':
                    videos.append({
                        'video_id': item['id']['videoId'],
                        'title': item['snippet']['title'],
                        'publishedAt': item['snippet']['publishedAt']
                    })
            return videos
        except Exception as e:
            self.logger.error(f"YouTube API error: {e}. Falling back to yt-dlp.")
            return self._fetch_videos_yt_dlp(max_results)

    def _fetch_videos_yt_dlp(self, max_results: int) -> List[Dict]:
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'dump_single_json': True,
            'playlistend': max_results
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.channel_url, download=False)
                entries = info.get('entries', [])
                videos = [{
                    'video_id': v['id'],
                    'title': v.get('title', ''),
                    'publishedAt': v.get('upload_date', '')
                } for v in entries]
                return videos
        except Exception as e:
            self.logger.error(f"yt-dlp error fetching video list: {e}")
            return []

    @staticmethod
    def sanitize_filename(title: str) -> str:
        # Remove or replace characters that are invalid in filenames
        return re.sub(r'[\\/:*?"<>|]', '_', title)

    def download_video(self, video_id: str, title: str, output_dir: str) -> Optional[str]:
        url = f"https://www.youtube.com/watch?v={video_id}"
        safe_title = YouTubeHandler.sanitize_filename(title)
        ydl_opts = {
            'outtmpl': f'{output_dir}/{safe_title} [%(id)s].%(ext)s',
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'quiet': True,
            'noplaylist': True,
            'retries': 5,
            'continuedl': True,
            'sleep_interval_requests': 2,
            'max_sleep_interval_requests': 5,
            'postprocessors': [{
                'key': 'FFmpegMetadata',
            }],
            'forcefilename': True,
            'progress_hooks': [],
            'windowsfilenames': True,
            'restrictfilenames': False,
            'consoletitle': False,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(result)
                return filename
        except Exception as e:
            self.logger.error(f"Failed to download video {video_id}: {e}")
            return None
