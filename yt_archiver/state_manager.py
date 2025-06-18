import sqlite3
import threading
from typing import Optional, Tuple

class StateManager:
    def __init__(self, db_path: str = 'ytm_archiver.db'):
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self.lock, sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS archived_videos (
                    video_id TEXT PRIMARY KEY,
                    youtube_title TEXT,
                    upload_timestamp TEXT
                )
            ''')
            conn.commit()

    def is_archived(self, video_id: str) -> bool:
        with self.lock, sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT 1 FROM archived_videos WHERE video_id = ?', (video_id,))
            return cur.fetchone() is not None

    def add_archived(self, video_id: str, youtube_title: str, upload_timestamp: str):
        with self.lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'INSERT OR IGNORE INTO archived_videos (video_id, youtube_title, upload_timestamp) VALUES (?, ?, ?)',
                (video_id, youtube_title, upload_timestamp)
            )
            conn.commit()

    def get_all_archived(self) -> list:
        with self.lock, sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT video_id FROM archived_videos')
            return [row[0] for row in cur.fetchall()]

    def get_video_info(self, video_id: str) -> Optional[Tuple[str, str]]:
        with self.lock, sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT youtube_title, upload_timestamp FROM archived_videos WHERE video_id = ?', (video_id,))
            return cur.fetchone()
