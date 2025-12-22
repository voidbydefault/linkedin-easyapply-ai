import sqlite3
import os
import hashlib
from datetime import datetime

class JobDatabase:
    def __init__(self, work_dir):
        self.db_path = os.path.join(work_dir, "job_history.db")
        self.init_database()
        self.init_scout_table()

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
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    reason TEXT
                )
            ''')
            cursor.execute("PRAGMA table_info(jobs)")
            columns = [info[1] for info in cursor.fetchall()]
            if 'reason' not in columns:
                print("Migrating DB: Adding 'reason' column...")
                cursor.execute("ALTER TABLE jobs ADD COLUMN reason TEXT")
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB Init Error: {e}")

    def init_scout_table(self):
        """Creates the scout jobs table if it doesn't exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scout_jobs (
                    job_hash TEXT PRIMARY KEY,
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
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Scout DB Init Error: {e}")

    def get_job_hash(self, url):
        """Creates a unique short hash for a job URL to save space."""
        # Clean the URL to ensure uniqueness (remove query params)
        clean_url = url.split('?')[0]
        return hashlib.md5(clean_url.encode('utf-8')).hexdigest()

    def is_job_seen(self, url):
        """Checks if the job is already in our history. Returns True/False."""
        return self.get_job_status(url) is not None

    def get_job_status(self, url):
        """Returns the (status, reason) tuple if job exists, else None."""
        job_hash = self.get_job_hash(url)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT status, reason FROM jobs WHERE job_hash = ?", (job_hash,))
            result = cursor.fetchone()
            conn.close()
            if result:
                return result[0], result[1] if result[1] else ""
            return None
        except:
            return None

    def mark_job_seen(self, url, title, status, reason=""):
        """Adds a job to history with its final status and reason."""
        job_hash = self.get_job_hash(url)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO jobs (job_hash, url, title, status, reason)
                VALUES (?, ?, ?, ?, ?)
            ''', (job_hash, url, title, status, reason))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB Save Error: {e}")

    def add_scout_job(self, url, title, company, location, score, reason):
        """Adds a discovered job to the scout table."""
        job_hash = self.get_job_hash(url)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            # Only insert if not exists (preserve completed status if re-scouted)
            cursor.execute('''
                INSERT OR IGNORE INTO scout_jobs (job_hash, url, title, company, location, score, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (job_hash, url, title, company, location, int(score), reason))
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
