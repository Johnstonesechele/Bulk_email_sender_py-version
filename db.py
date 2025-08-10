# db.py
import sqlite3
from typing import List, Tuple
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
    # recipients: list of (email, name)
    with _lock:
        conn = _connect()
        cur = conn.cursor()
        cur.executemany("INSERT INTO recipients (campaign_id, email, name) VALUES (?, ?, ?)",
                        [(campaign_id, r[0], r[1]) for r in recipients])
        conn.commit()
        conn.close()

def create_tables(self):
    self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS campaign_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER,
            sent_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            responded_count INTEGER DEFAULT 0,
            total_recipients INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    self.conn.commit()

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

def update_recipient_status(recipient_id: int, status: str, last_error: str = None, attempts: int = None):
    with _lock:
        conn = _connect()
        cur = conn.cursor()
        if attempts is not None:
            cur.execute("UPDATE recipients SET status = ?, last_error = ?, attempts = ? WHERE id = ?",
                        (status, last_error, attempts, recipient_id))
        else:
            cur.execute("UPDATE recipients SET status = ?, last_error = ? WHERE id = ?",
                        (status, last_error, recipient_id))
        conn.commit()
        conn.close()
