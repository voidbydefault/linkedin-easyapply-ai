import sqlite3
import os
import hashlib
from datetime import datetime

class JobDatabase:
    def __init__(self, work_dir):
        self.db_path = os.path.join(work_dir, "job_history.db")
        self.init_database()

    def init_database(self):
        """Creates the job history table if it doesn't exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS jobs (
                    job_hash TEXT PRIMARY KEY,
                    url TEXT,
                    title TEXT,
                    status TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB Init Error: {e}")

    def get_job_hash(self, url):
        """Creates a unique short hash for a job URL to save space."""
        # Clean the URL to ensure uniqueness (remove query params)
        clean_url = url.split('?')[0]
        return hashlib.md5(clean_url.encode('utf-8')).hexdigest()

    def is_job_seen(self, url):
        """Checks if the job is already in our history. Returns True/False."""
        return self.get_job_status(url) is not None

    def get_job_status(self, url):
        """Returns the status string (e.g., 'Applied', 'Skipped') if job exists, else None."""
        job_hash = self.get_job_hash(url)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM jobs WHERE job_hash = ?", (job_hash,))
            result = cursor.fetchone()
            conn.close()
            if result:
                return result[0]
            return None
        except:
            return None

    def mark_job_seen(self, url, title, status):
        """Adds a job to history with its final status (Applied/Skipped/Failed)."""
        job_hash = self.get_job_hash(url)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO jobs (job_hash, url, title, status)
                VALUES (?, ?, ?, ?)
            ''', (job_hash, url, title, status))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB Save Error: {e}")
