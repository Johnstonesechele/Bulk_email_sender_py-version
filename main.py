# main.py
import sys
import csv
import re
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QLineEdit, QFileDialog, QProgressBar,
    QTableWidget, QTableWidgetItem, QMessageBox, QSpinBox, QDialog, QFormLayout,
    QCheckBox, QDialogButtonBox, QInputDialog
)
from PySide6.QtCore import Qt, QThread, Slot
import db
from models import Campaign
from sender import SenderWorker
import os

# import the settings helper
import settings as settings_module

APP_STYLE = """
QMainWindow { background-color: #1a2332; color: #ffffff; }
QTabBar::tab { background: #172026; color: #FFD700; padding: 8px; margin: 2px; border-radius: 6px; }
QWidget { color: #e6eef7; }
QPushButton { background: #233044; color: #FFD700; padding: 6px; border-radius: 6px; }
QLineEdit, QTextEdit { background: #0f1720; color: #e6eef7; border: 1px solid #2b3948; padding: 6px; border-radius: 4px; }
QProgressBar { background: #0f1720; color: #e6eef7; border: 1px solid #2b3948; }
"""

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email: str) -> bool:
    return EMAIL_REGEX.match(email) is not None


class SmtpSettingsDialog(QDialog):
    """
    Dialog to input and save SMTP settings securely.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SMTP Settings (save encrypted)")
        self.setMinimumWidth(420)
        layout = QFormLayout()
        self.host = QLineEdit()
        self.port = QLineEdit()
        self.port.setPlaceholderText("e.g. 587 or 465")
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.use_tls = QCheckBox("Use STARTTLS (typically port 587)")
        layout.addRow("SMTP Host:", self.host)
        layout.addRow("Port:", self.port)
        layout.addRow("Username:", self.username)
        layout.addRow("Password:", self.password)
        layout.addRow("", self.use_tls)

        self.save_protect = QLineEdit()
        self.save_protect.setEchoMode(QLineEdit.EchoMode.Password)
        self.save_protect.setPlaceholderText("Master password to encrypt settings")
        layout.addRow("Master password (for encrypt):", self.save_protect)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

        # If saved settings exist, attempt to prefill after asking for master password
        if settings_module.settings_exist():
            if QMessageBox.question(self, "Load existing", "Saved SMTP settings found. Load them now?") == QMessageBox.StandardButton.Yes:
                pwd, ok = QInputDialog.getText(self, "Master password", "Enter master password to load settings:", QLineEdit.EchoMode.Password)
                if ok and pwd:
                    try:
                        s = settings_module.load_smtp_settings(pwd)
                        self.host.setText(s.get("host", ""))
                        self.port.setText(str(s.get("port", "")))
                        self.username.setText(s.get("username", ""))
                        self.password.setText(s.get("password", ""))
                        self.use_tls.setChecked(bool(s.get("use_tls", False)))
                    except ValueError:
                        QMessageBox.warning(self, "Error", "Could not load settings: invalid password or corrupted file.")

    def on_save(self):
        if not self.host.text().strip() or not self.port.text().strip():
            QMessageBox.warning(self, "Missing", "Please provide at least host and port.")
            return
        master = self.save_protect.text()
        if not master:
            QMessageBox.warning(self, "Master password", "Provide a master password to encrypt settings.")
            return
        try:
            port = int(self.port.text())
        except ValueError:
            QMessageBox.warning(self, "Port", "Port must be a number.")
            return
        sdict = {
            "host": self.host.text().strip(),
            "port": port,
            "username": self.username.text().strip(),
            "password": self.password.text(),
            "use_tls": bool(self.use_tls.isChecked())
        }
        try:
            settings_module.save_smtp_settings(master, sdict)
            QMessageBox.information(self, "Saved", "SMTP settings saved encrypted.")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save settings: {e}")


class EmailManagementTab(QWidget):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent

        layout = QVBoxLayout()
        self.campaign_name = QLineEdit()
        self.campaign_name.setPlaceholderText("Campaign name")
        self.subject = QLineEdit()
        self.subject.setPlaceholderText("Email subject")
        self.body = QTextEdit()
        self.load_csv_btn = QPushButton("Load recipients CSV")
        self.load_csv_btn.clicked.connect(self.load_csv)
        self.preview_table = QTableWidget(0, 2)
        self.preview_table.setHorizontalHeaderLabels(["Email", "Name",])
        self.create_campaign_btn = QPushButton("Create Campaign")
        self.create_campaign_btn.clicked.connect(self.create_campaign)
        self.send_btn = QPushButton("Start Sending")
        self.send_btn.clicked.connect(self.start_sending)
        self.progress = QProgressBar()
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 20)
        self.concurrency.setValue(4)

        cfg_layout = QHBoxLayout()
        cfg_layout.addWidget(QLabel("Concurrency:"))
        cfg_layout.addWidget(self.concurrency)

        smtp_cfg_layout = QHBoxLayout()
        self.smtp_settings_btn = QPushButton("SMTP Settings")
        self.smtp_settings_btn.clicked.connect(self.open_smtp_settings)
        self.load_smtp_btn = QPushButton("Load saved SMTP")
        self.load_smtp_btn.clicked.connect(self.load_saved_smtp)
        smtp_cfg_layout.addWidget(self.smtp_settings_btn)
        smtp_cfg_layout.addWidget(self.load_smtp_btn)

        # Real-time indicators (labels + progress bars)
        self.sent_label = QLabel("Sent: 0 (0.0%)")
        self.failed_label = QLabel("Failed: 0 (0.0%)")
        self.responded_label = QLabel("Responded: 0 (0.0%)")
        self.sent_progress = QProgressBar()
        self.failed_progress = QProgressBar()
        self.responded_progress = QProgressBar()

        layout.addWidget(QLabel("<b>Email Management</b>"))
        layout.addWidget(self.campaign_name)
        layout.addWidget(self.subject)
        layout.addWidget(self.body)
        layout.addWidget(self.load_csv_btn)
        layout.addWidget(self.preview_table)
        layout.addLayout(cfg_layout)
        layout.addLayout(smtp_cfg_layout)
        layout.addWidget(self.create_campaign_btn)
        layout.addWidget(self.send_btn)

        # Add realtime indicators below send button
        layout.addWidget(self.sent_label)
        layout.addWidget(self.sent_progress)
        layout.addWidget(self.failed_label)
        layout.addWidget(self.failed_progress)
        layout.addWidget(self.responded_label)
        layout.addWidget(self.responded_progress)

        layout.addWidget(self.progress)
        self.setLayout(layout)

        self.loaded_recipients = []  # list of (email, name)
        self.loaded_smtp = None  # dict with smtp settings

    def open_smtp_settings(self):
        dlg = SmtpSettingsDialog(self)
        dlg.exec()

    def load_saved_smtp(self):
        if not settings_module.settings_exist():
            QMessageBox.information(self, "No saved settings", "No saved SMTP settings found. Use SMTP Settings to save.")
            return
        pwd, ok = QInputDialog.getText(self, "Master password", "Enter master password to load settings:", QLineEdit.EchoMode.Password)
        if not ok or not pwd:
            return
        try:
            s = settings_module.load_smtp_settings(pwd)
            self.loaded_smtp = s
            QMessageBox.information(self, "Loaded", f"Loaded SMTP settings for host {s.get('host')}")
        except ValueError:
            QMessageBox.warning(self, "Error", "Invalid master password or corrupted settings.")

    def load_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV", "", "CSV Files (*.csv)")
        if not path:
            return
        self.loaded_recipients.clear()
        invalid_count = 0
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if not row:
                    continue
                email = row[0].strip()
                name = row[1].strip() if len(row) > 1 else ""
                if not is_valid_email(email):
                    invalid_count += 1
                    continue
                self.loaded_recipients.append((email, name))
        self.refresh_preview()
        if invalid_count:
            QMessageBox.warning(self, "Invalid emails", f"Skipped {invalid_count} invalid email(s) while loading CSV.")

    def refresh_preview(self):
        self.preview_table.setRowCount(0)
        for e, n in self.loaded_recipients:
            r = self.preview_table.rowCount()
            self.preview_table.insertRow(r)
            self.preview_table.setItem(r, 0, QTableWidgetItem(e))
            self.preview_table.setItem(r, 1, QTableWidgetItem(n))

    def create_campaign(self):
        name = self.campaign_name.text().strip()
        subject = self.subject.text().strip()
        body = self.body.toPlainText().strip()
        if not name or not subject or not body:
            QMessageBox.warning(self, "Missing", "Please fill campaign name, subject and body")
            return
        cid = db.create_campaign(name, subject, body)
        db.add_recipients(cid, self.loaded_recipients)
        QMessageBox.information(self, "Created", f"Campaign {name} created with id {cid}")
        self.parent.refresh_campaigns()

    def start_sending(self):
        # pick selected campaign from parent list
        selected = self.parent.get_selected_campaign()
        if not selected:
            QMessageBox.warning(self, "Select", "Select a campaign from Campaign History tab first")
            return
        campaign = selected

        # Determine SMTP settings: prefer loaded_smtp, otherwise prompt
        smtp = None
        if self.loaded_smtp:
            smtp = self.loaded_smtp
        elif settings_module.settings_exist():
            pwd, ok = QInputDialog.getText(self, "Master password", "Enter master password to load settings:", QLineEdit.EchoMode.Password)
            if not ok or not pwd:
                QMessageBox.warning(self, "No SMTP", "No SMTP settings available.")
                return
            try:
                smtp = settings_module.load_smtp_settings(pwd)
            except ValueError:
                QMessageBox.warning(self, "Error", "Invalid master password or corrupted settings.")
                return
        else:
            dlg = SmtpSettingsDialog(self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                QMessageBox.warning(self, "Cancelled", "No SMTP settings provided.")
                return
            pwd, ok = QInputDialog.getText(self, "Master password", "Enter the master password you used to save settings:", QLineEdit.EchoMode.Password)
            if not ok or not pwd:
                QMessageBox.warning(self, "No SMTP", "No SMTP settings available.")
                return
            try:
                smtp = settings_module.load_smtp_settings(pwd)
            except ValueError:
                QMessageBox.warning(self, "Error", "Could not load settings after save.")
                return

        smtp_host = smtp.get("host")
        smtp_port = int(smtp.get("port", 25))
        smtp_user = smtp.get("username") or None
        smtp_pass = smtp.get("password") or None
        use_tls = bool(smtp.get("use_tls", False))
        concurrency = self.concurrency.value()
        # trigger start in MainWindow (this will reset counters there)
        self.parent.start_sender_thread(campaign['id'], campaign['subject'], campaign['body'],
                                        smtp_host, smtp_port, concurrency, smtp_user, smtp_pass, use_tls)

    @Slot(int, int)
    def on_progress(self, sent, total):
        # keep existing progress bar behavior (overall)
        self.progress.setMaximum(total)
        self.progress.setValue(sent)


class CampaignHistoryTab(QWidget):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        layout = QVBoxLayout()
        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["ID", "Name", "Created", "Subject"])
        self.refresh_btn = QPushButton("Refresh campaigns")
        self.refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(QLabel("<b>Campaign History</b>"))
        layout.addWidget(self.tbl)
        layout.addWidget(self.refresh_btn)
        self.setLayout(layout)
        self.refresh()

    def refresh(self):
        rows = db.get_campaigns()
        self.tbl.setRowCount(0)
        for r in rows:
            idx = self.tbl.rowCount()
            self.tbl.insertRow(idx)
            self.tbl.setItem(idx, 0, QTableWidgetItem(str(r['id'])))
            self.tbl.setItem(idx, 1, QTableWidgetItem(r['name']))
            self.tbl.setItem(idx, 2, QTableWidgetItem(r['created_at']))
            self.tbl.setItem(idx, 3, QTableWidgetItem(r['subject']))


class DataCleaningTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        layout.addWidget(QLabel("<b>Data Cleaning</b>"))
        layout.addWidget(QLabel("Use the Data Cleaning tools in future iterations."))
        self.setLayout(layout)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bulk Email Manager - Professional Edition")
        tabs = QTabWidget()
        self.email_tab = EmailManagementTab(self)
        self.history_tab = CampaignHistoryTab(self)
        self.data_tab = DataCleaningTab()
        tabs.addTab(self.email_tab, "Email Management")
        tabs.addTab(self.history_tab, "Campaign History")
        tabs.addTab(self.data_tab, "Data Cleaning")
        self.setCentralWidget(tabs)
        self.setMinimumSize(1000, 700)

        # real-time counters
        self.sent_count = 0
        self.failed_count = 0
        self.responded_count = 0
        self.total_recipients = 0

        self.sender_thread = None
        self.sender_worker = None

    def refresh_campaigns(self):
        self.history_tab.refresh()

    def get_selected_campaign(self):
        tbl = self.history_tab.tbl
        selected = tbl.currentRow()
        if selected < 0:
            return None
        cid = int(tbl.item(selected, 0).text())
        for c in db.get_campaigns():
            if c['id'] == cid:
                return c
        return None

    def start_sender_thread(self, campaign_id, subject, body, smtp_host, smtp_port, concurrency, smtp_user=None, smtp_pass=None, use_tls=False):
        # guard for existing thread
        if self.sender_thread and self.sender_thread.isRunning():
            QMessageBox.warning(self, "Sending active", "A sending job is already running.")
            return

        # compute total recipients (so percentages can be computed)
        recipients = db.get_recipients_for_campaign(campaign_id)
        self.total_recipients = len(recipients)
        # reset counters
        self.sent_count = 0
        self.failed_count = 0
        self.responded_count = 0

        # update UI immediately
        self._update_realtime_ui()

        # setup worker
        self.sender_thread = QThread()
        self.sender_worker = SenderWorker(smtp_host=smtp_host, smtp_port=smtp_port,
                                          smtp_user=smtp_user, smtp_pass=smtp_pass,
                                          use_tls=use_tls, concurrency=concurrency)
        self.sender_worker.moveToThread(self.sender_thread)
        # wire signals (Qt will queue these because signals come from worker thread)
        self.sender_worker.signals.progress.connect(self.email_tab.on_progress)
        self.sender_worker.signals.status.connect(self.on_recipient_status)
        self.sender_worker.signals.finished.connect(self.on_sending_finished)
        # start sending once thread starts
        self.sender_thread.started.connect(lambda: self.sender_worker.start_campaign(campaign_id, 0, subject, body, smtp_user or "noreply@example.com"))
        self.sender_thread.start()

    @Slot(int, str)
    def on_recipient_status(self, recipient_id, status_text):
        """
        Update counters when worker emits a status for a recipient.
        Expected status_text examples: "sent", "failed: <error>", "invalid: <reason>", "stopped"
        """
        st = status_text.lower()
        if st.startswith("sent"):
            self.sent_count += 1
        elif st.startswith("failed"):
            self.failed_count += 1
        elif st.startswith("invalid"):
            self.failed_count += 1
        elif st.startswith("responded"):
            # if you ever implement automatic response detection, worker can emit "responded"
            self.responded_count += 1

        # persist status in DB (so history shows it)
        try:
            # map simple statuses to DB values
            if st.startswith("sent"):
                db.update_recipient_status(recipient_id, "sent", None, None)
            elif st.startswith("responded"):
                db.update_recipient_status(recipient_id, "responded", None, None)
            else:
                db.update_recipient_status(recipient_id, "failed", status_text, None)
        except Exception:
            # ignore DB errors here but you may want to log them
            pass

        # update UI
        self._update_realtime_ui()

    def _update_realtime_ui(self):
        total = max(1, self.total_recipients)  # avoid div-by-zero
        sent_pct = (self.sent_count / total) * 100
        failed_pct = (self.failed_count / total) * 100
        responded_pct = (self.responded_count / total) * 100

        # set labels
        self.email_tab.sent_label.setText(f"Sent: {self.sent_count} ({sent_pct:.1f}%)")
        self.email_tab.failed_label.setText(f"Failed: {self.failed_count} ({failed_pct:.1f}%)")
        self.email_tab.responded_label.setText(f"Responded: {self.responded_count} ({responded_pct:.1f}%)")

        # set progress bars
        # use int 0-100 range
        self.email_tab.sent_progress.setValue(int(sent_pct))
        self.email_tab.failed_progress.setValue(int(failed_pct))
        self.email_tab.responded_progress.setValue(int(responded_pct))

        # update overall progress too (sent + failed = processed)
        processed = self.sent_count + self.failed_count
        overall_pct = (processed / total) * 100
        self.email_tab.progress.setMaximum(100)
        self.email_tab.progress.setValue(int(overall_pct))

        # process events safely (keeps UI responsive)
        QApplication.processEvents()

    @Slot()
    def on_sending_finished(self):
        QMessageBox.information(self, "Done", "Sending finished")
        if self.sender_thread:
            self.sender_thread.quit()
            self.sender_thread.wait()
            self.sender_worker = None
            self.sender_thread = None

        # final UI update
        self._update_realtime_ui()


def main():
    db.init_db()
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLE)
    mw = MainWindow()
    mw.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
