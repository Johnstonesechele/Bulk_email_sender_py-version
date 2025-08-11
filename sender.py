# sender.py
import smtplib
import time
import threading
import datetime
import re
from email.message import EmailMessage
from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import QObject, Signal, Slot, QThread
from db import update_recipient_status, get_recipients_for_campaign
from email_validator import validate_email, EmailNotValidError


class SenderWorker(QObject):
    progress = Signal(int, int, int)  # sent, failed, responded
    finished = Signal(int, int, int)  # sent, failed, responded
    def __init__(self, smtp_host: str, smtp_port: int, smtp_user: str = None, smtp_pass: str = None,
                 use_tls: bool = False, concurrency: int = 4, rate_per_sec: float = 1.0,
                 retry_attempts: int = 2, retry_backoff: float = 2.0):
        super().__init__()
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
        rid = recipient_row['id']
        email = recipient_row['email']
        attempts = recipient_row.get('attempts', 0)

        # Validate email
        try:
            validate_email(email)
        except EmailNotValidError as e:
            update_recipient_status(rid, 'invalid', str(e), attempts)
            return rid, f"invalid: {str(e)}", attempts

        last_error = None
        for attempt in range(1, self.retry_attempts + 2):
            if self._stop_event.is_set():
                return rid, "stopped", attempt - 1
            try:
                self._rate_limit()
                msg = EmailMessage()
                msg["From"] = sender_from
                msg["To"] = email
                msg["Subject"] = subject
                msg.set_content(body)

                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                    if self.use_tls:
                        server.starttls()
                    if self.smtp_user and self.smtp_pass:
                        server.login(self.smtp_user, self.smtp_pass)
                    server.send_message(msg)

                update_recipient_status(rid, 'sent', None, attempt)
                return rid, "sent", attempt
            except Exception as e:
                last_error = str(e)
                update_recipient_status(rid, 'failed', last_error, attempt)
                time.sleep(self.retry_backoff * attempt)

        return rid, f"failed: {last_error}", self.retry_attempts

    @Slot(int, int, str, str)
    def start_campaign(self, campaign_id, start_index, subject, body, sender_from):
        recipients = self.db.get_recipients(campaign_id)

        sent_count = 0
        failed_count = 0
        invalid_count = 0

        # SMTP connection setup
        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
        except Exception as e:
            print(f"SMTP Connection Error: {e}")
            self.finished.emit(sent_count, failed_count, invalid_count)
            return

        # Loop over recipients
        for idx, r in enumerate(recipients[start_index:], start=start_index):
            email_addr = r.email.strip()

            # Increment attempts
            self.db.increment_attempts(r.id)

            # Validate email format
            if not re.match(r"[^@]+@[^@]+\.[^@]+", email_addr):
                self.db.update_recipient_status(r.id, "invalid", "Invalid email format")
                invalid_count += 1
                self.progress.emit(sent_count, failed_count, invalid_count)
                continue

            try:
                # Send email
                message = f"From: {sender_from}\nTo: {email_addr}\nSubject: {subject}\n\n{body}"
                server.sendmail(sender_from, [email_addr], message)

                self.db.update_recipient_status(r.id, "sent")
                sent_count += 1
            except Exception as ex:
                self.db.update_recipient_status(r.id, "failed", str(ex))
                failed_count += 1

            # Emit real-time progress update
            self.progress.emit(sent_count, failed_count, invalid_count)

        # Close SMTP connection
        try:
            server.quit()
        except:
            pass

        # Emit final result
        self.finished.emit(sent_count, failed_count, invalid_count)