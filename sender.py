# sender.py
import smtplib
import time
import threading
from email.message import EmailMessage
from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import QObject, Signal, Slot, QThread
from db import update_recipient_status, get_recipients_for_campaign
from email_validator import validate_email, EmailNotValidError

class SenderSignals(QObject):
    progress = Signal(int, int)   # sent_count, total
    status = Signal(int, str)     # recipient_id, status_text
    finished = Signal()

class SenderWorker(QObject):
    """
    Runs in a QThread. Uses ThreadPoolExecutor to send messages concurrently,
    but progress/signals are emitted back to Qt thread via SenderSignals.
    """
    def __init__(self, smtp_host: str, smtp_port: int, smtp_user: str = None, smtp_pass: str = None,
                 use_tls: bool = False, concurrency: int = 4, rate_per_sec: float = 1.0,
                 retry_attempts: int = 2, retry_backoff: float = 2.0):
        super().__init__()
        self.signals = SenderSignals()
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_pass = smtp_pass
        self.use_tls = use_tls
        self.concurrency = concurrency
        self.rate_per_sec = rate_per_sec
        self.retry_attempts = retry_attempts
        self.retry_backoff = retry_backoff
        self._stop_event = threading.Event()

        # simple token bucket rate limiter
        self._last_sent = 0.0
        self._lock = threading.Lock()

    def stop(self):
        self._stop_event.set()

    def _rate_limit(self):
        with self._lock:
            now = time.time()
            interval = 1.0 / max(1e-6, self.rate_per_sec)
            elapsed = now - self._last_sent
            if elapsed < interval:
                time.sleep(interval - elapsed)
            self._last_sent = time.time()

    def _send_single(self, recipient_row: dict, subject: str, body: str, sender_from: str) -> tuple:
        """
        Sends a single message; returns (recipient_id, status_text, attempts)
        """
        rid = recipient_row['id']
        email = recipient_row['email']
        attempts = recipient_row.get('attempts', 0)

        # validate email
        try:
            validate_email(email)
        except EmailNotValidError as e:
            update_recipient_status(rid, 'failed', str(e), attempts)
            return (rid, f"invalid: {str(e)}", attempts)

        last_error = None
        for attempt in range(1, self.retry_attempts + 2):
            if self._stop_event.is_set():
                return (rid, "stopped", attempt-1)
            try:
                self._rate_limit()
                msg = EmailMessage()
                msg["From"] = sender_from
                msg["To"] = email
                msg["Subject"] = subject
                msg.set_content(body)
                # connect & send
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                    if self.use_tls:
                        server.starttls()
                    if self.smtp_user and self.smtp_pass:
                        server.login(self.smtp_user, self.smtp_pass)
                    server.send_message(msg)
                update_recipient_status(rid, 'sent', None, attempt)
                return (rid, "sent", attempt)
            except Exception as e:
                last_error = str(e)
                attempts = attempt
                update_recipient_status(rid, 'failed', last_error, attempts)
                # backoff
                time.sleep(self.retry_backoff * attempt)
        return (rid, f"failed: {last_error}", attempts)

    @Slot(int, int, str, str)
    def start_campaign(self, campaign_id: int, total_expected: int, subject: str, body: str, sender_from: str = "noreply@example.com"):
        # runs in this object's thread
        recipients = get_recipients_for_campaign(campaign_id)
        total = len(recipients)
        sent_count = 0
        with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
            futures = {ex.submit(self._send_single, r, subject, body, sender_from): r for r in recipients}
            for fut in as_completed(futures):
                if self._stop_event.is_set():
                    break
                recipient = futures[fut]
                try:
                    rid, status_text, attempts = fut.result()
                except Exception as e:
                    rid = recipient['id']
                    status_text = f"error: {e}"
                if status_text.startswith("sent"):
                    sent_count += 1
                # emit status for UI
                self.signals.status.emit(rid, status_text)
                self.signals.progress.emit(sent_count, total)
        self.signals.finished.emit()
