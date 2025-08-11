# db.py
import sqlite3
from typing import List, Tuple, Optional
from models import Campaign, Recipient
import datetime
import threading

DB_PATH = "bemanager.db"
_lock = threading.Lock()

def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _lock:
        conn = _connect()
        cur = conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            subject TEXT,
            body TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER,
            email TEXT NOT NULL,
            name TEXT,
            status TEXT DEFAULT 'pending',
            last_error TEXT,
            attempts INTEGER DEFAULT 0,
            responded_at TEXT,
            FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
        );
        """)
        conn.commit()
        conn.close()

def create_campaign(name: str, subject: str, body: str) -> int:
    created_at = datetime.datetime.utcnow().isoformat()
    with _lock:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("INSERT INTO campaigns (name, subject, body, created_at) VALUES (?, ?, ?, ?)",
                    (name, subject, body, created_at))
        cid = cur.lastrowid
        conn.commit()
        conn.close()
    return cid

def add_recipients(campaign_id: int, recipients: List[Tuple[str, str]]):
    with _lock:
        conn = _connect()
        cur = conn.cursor()
        cur.executemany("INSERT INTO recipients (campaign_id, email, name) VALUES (?, ?, ?)",
                        [(campaign_id, r[0], r[1]) for r in recipients])
        conn.commit()
        conn.close()

def get_campaigns():
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM campaigns ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_recipients_for_campaign(campaign_id: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM recipients WHERE campaign_id = ?", (campaign_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_recipient_status(recipient_id: int, status: str, last_error: Optional[str] = None,
                             attempts: Optional[int] = None, responded_at: Optional[str] = None):
    with _lock:
        conn = _connect()
        cur = conn.cursor()
        query_parts = ["status = ?"]
        params = [status]

        if last_error is not None:
            query_parts.append("last_error = ?")
            params.append(last_error)
        if attempts is not None:
            query_parts.append("attempts = ?")
            params.append(attempts)
        if responded_at is not None:
            query_parts.append("responded_at = ?")
            params.append(responded_at)

        params.append(recipient_id)
        query = f"UPDATE recipients SET {', '.join(query_parts)} WHERE id = ?"

        cur.execute(query, params)
        conn.commit()
        conn.close()

def get_campaign_stats(campaign_id: int):
    """Returns sent_count, failed_count, responded_count, total_recipients."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS sent_count,
            SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_count,
            SUM(CASE WHEN status='responded' THEN 1 ELSE 0 END) AS responded_count,
            COUNT(*) AS total
        FROM recipients
        WHERE campaign_id = ?
    """, (campaign_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row)
