import sqlite3
import os
import re
import hashlib
from datetime import datetime

class JobDatabase:
    def __init__(self, work_dir):
        self.db_path = os.path.join(work_dir, "job_history.db")
        self.init_database()
        self.init_scout_table()

    @staticmethod
    def extract_job_id(url):
        """Extracts the numeric LinkedIn job ID from a job URL, if present."""
        if not url:
            return None
        url_str = str(url)
        clean = url_str.split('?', 1)[0]
        m = re.search(r'/jobs/view/(?:[^/?#]*-)?(\d+)(?:/|$)', clean)
        if m:
            return m.group(1)
        m_param = re.search(r'[?&]currentJobId=(\d+)', url_str)
        if m_param:
            return m_param.group(1)
        return None

    def _backfill_job_ids(self, cursor, table_name):
        """Populates missing job_id values from stored URLs for an existing table."""
        cursor.execute(f"SELECT job_hash, url FROM {table_name} WHERE job_id IS NULL OR job_id = ''")
        for job_hash, url in cursor.fetchall():
            jid = self.extract_job_id(url)
            if jid:
                cursor.execute(f"UPDATE {table_name} SET job_id = ? WHERE job_hash = ?", (jid, job_hash))

    def init_database(self):
        """Creates the job history table if it doesn't exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS jobs (
                    job_hash TEXT PRIMARY KEY,
                    job_id TEXT,
                    url TEXT,
                    title TEXT,
                    status TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    reason TEXT
                )
            ''')
            cursor.execute("PRAGMA table_info(jobs)")
            columns = [info[1] for info in cursor.fetchall()]
            if 'reason' not in columns:
                print("Migrating DB: Adding 'reason' column...")
                cursor.execute("ALTER TABLE jobs ADD COLUMN reason TEXT")
            if 'job_id' not in columns:
                print("Migrating DB: Adding 'job_id' column to jobs...")
                cursor.execute("ALTER TABLE jobs ADD COLUMN job_id TEXT")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_job_id ON jobs(job_id)")
            self._backfill_job_ids(cursor, "jobs")
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB Init Error: {e}")

    def init_scout_table(self):
        """Creates the scout jobs and scout history tables if they don't exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scout_jobs (
                    job_hash TEXT PRIMARY KEY,
                    job_id TEXT,
                    url TEXT,
                    title TEXT,
                    company TEXT,
                    location TEXT,
                    score INTEGER,
                    reason TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    completed BOOLEAN DEFAULT 0
                )
            ''')
            cursor.execute("PRAGMA table_info(scout_jobs)")
            scout_cols = [info[1] for info in cursor.fetchall()]
            if 'job_id' not in scout_cols:
                print("Migrating DB: Adding 'job_id' column to scout_jobs...")
                cursor.execute("ALTER TABLE scout_jobs ADD COLUMN job_id TEXT")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_scout_jobs_job_id ON scout_jobs(job_id)")
            self._backfill_job_ids(cursor, "scout_jobs")

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scout_history (
                    job_hash TEXT PRIMARY KEY,
                    job_id TEXT,
                    url TEXT,
                    title TEXT,
                    status TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    reason TEXT
                )
            ''')
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_scout_history_job_id ON scout_history(job_id)")
            cursor.execute('''
                INSERT OR IGNORE INTO scout_history (job_hash, job_id, url, title, status, timestamp, reason)
                SELECT job_hash, job_id, url, title, 'Scouted-Match', timestamp, reason FROM scout_jobs
            ''')

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Scout DB Init Error: {e}")

    def _resolve_key(self, url, job_id=None):
        resolved_id = str(job_id).strip() if job_id else self.extract_job_id(url)
        if resolved_id and not resolved_id.isdigit():
            resolved_id = self.extract_job_id(url)
        return resolved_id, self.get_job_hash(url, job_id=resolved_id)

    def get_job_hash(self, url, job_id=None):
        """Creates a unique short hash for a job URL or canonical job_id."""
        resolved_id = str(job_id).strip() if job_id else self.extract_job_id(url)
        if resolved_id and resolved_id.isdigit():
            clean_url = f"https://www.linkedin.com/jobs/view/{resolved_id}/"
        else:
            clean_url = str(url or '').split('?', 1)[0].rstrip('/')
        return hashlib.md5(clean_url.encode('utf-8')).hexdigest()

    def is_job_seen(self, url, job_id=None):
        """Checks if the job is already in our history. Returns True/False."""
        return self.get_job_status(url, job_id=job_id) is not None

    def get_job_status(self, url, job_id=None):
        """Returns the (status, reason) tuple if job exists, else None."""
        resolved_id, job_hash = self._resolve_key(url, job_id=job_id)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            if resolved_id:
                cursor.execute(
                    "SELECT status, reason FROM jobs WHERE job_id = ? OR job_hash = ? LIMIT 1",
                    (resolved_id, job_hash),
                )
            else:
                cursor.execute("SELECT status, reason FROM jobs WHERE job_hash = ? LIMIT 1", (job_hash,))
            result = cursor.fetchone()
            conn.close()
            if result:
                return result[0], result[1] if result[1] else ""
            return None
        except Exception:
            return None

    def mark_job_seen(self, url, title, status, reason="", job_id=None):
        """Adds a job to history with its final status and reason."""
        resolved_id, job_hash = self._resolve_key(url, job_id=job_id)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO jobs (job_hash, job_id, url, title, status, reason)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (job_hash, resolved_id, url, title, status, reason))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB Save Error: {e}")

    def get_scout_status(self, url, job_id=None):
        """Returns (status, reason) if the job was already scanned in Scout mode, else None."""
        resolved_id, job_hash = self._resolve_key(url, job_id=job_id)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            if resolved_id:
                cursor.execute(
                    "SELECT status, reason FROM scout_history WHERE job_id = ? OR job_hash = ? LIMIT 1",
                    (resolved_id, job_hash),
                )
            else:
                cursor.execute("SELECT status, reason FROM scout_history WHERE job_hash = ? LIMIT 1", (job_hash,))
            result = cursor.fetchone()
            if result:
                conn.close()
                return result[0], result[1] if result[1] else ""

            if resolved_id:
                cursor.execute(
                    "SELECT reason FROM scout_jobs WHERE job_id = ? OR job_hash = ? LIMIT 1",
                    (resolved_id, job_hash),
                )
            else:
                cursor.execute("SELECT reason FROM scout_jobs WHERE job_hash = ? LIMIT 1", (job_hash,))
            legacy = cursor.fetchone()
            conn.close()
            if legacy:
                return "Scouted-Match", legacy[0] if legacy[0] else ""
            return None
        except Exception:
            return None

    def is_scout_seen(self, url, job_id=None):
        """Checks if the job has already been scanned in Scout mode."""
        return self.get_scout_status(url, job_id=job_id) is not None

    def mark_scout_seen(self, url, title, status, reason="", job_id=None):
        """Records a scanned job in scout_history so future Scout runs skip it."""
        resolved_id, job_hash = self._resolve_key(url, job_id=job_id)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO scout_history (job_hash, job_id, url, title, status, reason)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (job_hash, resolved_id, url, title, status, reason))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Scout History Save Error: {e}")

    def add_scout_job(self, url, title, company, location, score, reason, job_id=None):
        """Adds a discovered job to the scout table."""
        resolved_id, job_hash = self._resolve_key(url, job_id=job_id)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            # Only insert if not exists (preserve completed status if re-scouted)
            cursor.execute('''
                INSERT OR IGNORE INTO scout_jobs (job_hash, job_id, url, title, company, location, score, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (job_hash, resolved_id, url, title, company, location, int(score), reason))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Scout DB Save Error: {e}")

    def get_scout_jobs(self):
        """Retrieves all scout jobs."""
        try:
            conn = sqlite3.connect(self.db_path)
            import pandas as pd
            df = pd.read_sql_query("SELECT * FROM scout_jobs ORDER BY timestamp DESC", conn)
            conn.close()
            return df
        except Exception as e:
            print(f"Scout DB Read Error: {e}")
            return None

    def toggle_scout_job(self, job_hash, completed_status):
        """Updates the completed status of a scout job."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("UPDATE scout_jobs SET completed = ? WHERE job_hash = ?", (1 if completed_status else 0, job_hash))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Scout DB Update Error: {e}")

    def clear_scout_table(self):
        """Clears all entries from the scout_jobs table."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM scout_jobs")
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Scout DB Clear Error: {e}")
            return False
