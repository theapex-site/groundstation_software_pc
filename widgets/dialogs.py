"""
widgets/dialogs.py
──────────────────
All themed popup windows for the APEX GCS.

  ComPortSelector   – startup dialog to pick a serial COM port.
  Toast             – brief non-blocking notification overlay (bottom-right).
  ThemedMessageBox  – drop-in replacement for QMessageBox, fully themed.
                      Supports: information / warning / critical / question
                      Returns the same button-role integers as QMessageBox so
                      existing callers need zero changes.
"""
import os
import json
import tempfile
import serial.tools.list_ports
import tempfile, os
from PyQt5 import QtWidgets, QtCore, QtGui
import ctypes, time
from config import APEX_LOGO, BG_IMAGE, title
import widgets.auth_backend as auth
import widgets.firebase_backend as fb

# ── Shared GCS palette (keep in sync with config.py STYLE) ───────────────────
_BG       = "#020617"
_SURFACE  = "#0f172a"
_BORDER   = "#1e293b"
_ACCENT   = "#38bdf8"
_TEXT     = "#e2e8f0"
_MUTED    = "#94a3b8"
_GREEN    = "#22c55e"
_YELLOW   = "#facc15"
_RED      = "#ef4444"

# ── Session persistence (survives app close, cleared on system reboot) ────────
# %WINDIR%\Temp is cleared on reboot; fall back to regular temp if unavailable
_SESSION_FILE = os.path.join(
    os.environ.get("WINDIR", "C:\\Windows"), "Temp", "apex_gcs_session.json"
)

def _system_boot_time() -> float:
    """Return system uptime start (epoch seconds). Windows only."""
    try:
        uptime_ms = ctypes.windll.kernel32.GetTickCount64()
        return time.time() - (uptime_ms / 1000.0)
    except Exception:
        return 0.0

def _save_session(email: str, refresh_token: str) -> None:
    """
    Remember the signed-in user across restarts.

    Stores a Firebase refresh token, not the password. The old version wrote
    the password here in plain text; a refresh token is scoped to this account
    and can be revoked from the Firebase console if a machine is lost.
    """
    try:
        os.makedirs(os.path.dirname(_SESSION_FILE), exist_ok=True)
        with open(_SESSION_FILE, "w") as f:
            json.dump({
                "email": email,
                "refreshToken": refresh_token,
                "stay": True,
                "boot_time": _system_boot_time(),   # stamp current boot
            }, f)
    except Exception:
        pass

def _load_session() -> dict:
    """Return saved session dict, or {} if stale / missing."""
    try:
        with open(_SESSION_FILE, "r") as f:
            data = json.load(f)
        if not (data.get("stay") and data.get("email") and data.get("refreshToken")):
            return {}
        # Reject session if it was saved before the current boot
        saved_boot = data.get("boot_time", 0)
        current_boot = _system_boot_time()
        if abs(saved_boot - current_boot) > 30:   # >30 s drift = different boot
            _clear_session()
            return {}
        return data
    except Exception:
        pass
    return {}

def _clear_session() -> None:
    """Delete the session file (user unchecked Stay signed in)."""
    try:
        os.remove(_SESSION_FILE)
    except Exception:
        pass

_BASE_STYLE = f"""
    * {{
        font-family: Consolas;
        color: {_TEXT};
    }}
    QDialog, QFrame {{
        background-color: {_BG};
    }}
    QLabel {{
        color: {_TEXT};
        font-size: 12px;
    }}
    QPushButton {{
        background: {_SURFACE};
        color: {_ACCENT};
        border: 1px solid {_ACCENT};
        padding: 6px 18px;
        border-radius: 5px;
        font-size: 12px;
        min-width: 80px;
    }}
    QPushButton:hover {{
        background: {_ACCENT};
        color: {_BG};
    }}
    QPushButton#dangerBtn {{
        border-color: {_RED};
        color: {_RED};
    }}
    QPushButton#dangerBtn:hover {{
        background: {_RED};
        color: {_BG};
    }}
    QComboBox {{
        background: {_SURFACE};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 12px;
    }}
    QComboBox QAbstractItemView {{
        background: {_SURFACE};
        color: {_TEXT};
        selection-background-color: {_ACCENT};
        selection-color: {_BG};
        border: 1px solid {_BORDER};
    }}
    QComboBox::drop-down {{
        border: none;
    }}
    QComboBox::down-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 6px solid {_ACCENT};
        margin-right: 8px;
    }}
    QLineEdit {{
        background: {_SURFACE};
        color: {_TEXT};
        border: 1.5px solid #2d4a5a;
        border-radius: 5px;
        padding: 6px 10px;
        font-size: 15px;
    }}
    QLineEdit:focus {{
        border: 2px solid #4a7a96;
        background: #0f2133;
    }}
"""

def _labelled_input(parent_layout, label_text, placeholder="",
                    echo=QtWidgets.QLineEdit.Normal):
    lbl = QtWidgets.QLabel(label_text)
    lbl.setStyleSheet(f"color: {_TEXT}; font-size: 13px; font-weight: bold; margin-bottom: 3px;")
    inp = QtWidgets.QLineEdit()
    inp.setEchoMode(echo)
    inp.setPlaceholderText(placeholder)
    inp.setMinimumHeight(38)
    inp.setStyleSheet("font-size: 15px;")  
    parent_layout.addWidget(lbl)
    parent_layout.addWidget(inp)
    return inp

# ══════════════════════════════════════════════════════════════════════════════
#  OTP Dialog  (small popup)
# ══════════════════════════════════════════════════════════════════════════════
class _VerifyEmailDialog(QtWidgets.QDialog):
    """
    Waits for the Firebase verification link to be clicked.

    Replaces the old 6-digit OTP box. The code behind that box was generated
    locally and emailed by the app itself through Gmail, which is why a sender
    address and app password had to be compiled into every build. Firebase
    sends this one, so there is no credential left to leak.

    Polls rather than asking the user to press anything: the ID token's
    email_verified claim does not change in a token already issued, so a fresh
    one is minted each poll and that is also what the dialog hands back.
    """

    def __init__(self, parent, session: dict, email: str):
        super().__init__(parent)
        self.session = session
        self._email = email
        self._elapsed = 0

        self.setWindowTitle("Verify Email")
        self.setModal(True)
        self.setFixedWidth(420)
        self.setStyleSheet(_BASE_STYLE)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        title = QtWidgets.QLabel("Check your inbox")
        title.setStyleSheet(f"color:{_ACCENT}; font-size:16px; font-weight:bold;")
        lay.addWidget(title)

        body = QtWidgets.QLabel(
            "We've emailed a verification link to<br><b>{}</b><br><br>"
            "Open it, then come back here. This window closes by itself "
            "once it's confirmed.".format(email))
        body.setWordWrap(True)
        body.setStyleSheet(f"color:{_TEXT}; font-size:12px;")
        lay.addWidget(body)

        self._status = QtWidgets.QLabel("Waiting for confirmation…")
        self._status.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        lay.addWidget(self._status)

        row = QtWidgets.QHBoxLayout()
        self._resend = QtWidgets.QPushButton("Resend Email")
        self._resend.clicked.connect(self._do_resend)
        row.addWidget(self._resend)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(3000)

    def _poll(self):
        self._elapsed += 3
        try:
            fresh = fb.refresh_session(self.session)
            info = fb.account_info(fresh["idToken"])
        except fb.AuthError:
            return                      # transient; keep waiting
        if info.get("emailVerified"):
            self.session = fresh
            self._timer.stop()
            self.accept()
            return
        mins, secs = divmod(self._elapsed, 60)
        self._status.setText(
            "Waiting for confirmation… ({}:{:02d})".format(mins, secs))

    def _do_resend(self):
        self._resend.setEnabled(False)
        try:
            fb.send_verification_email(self.session["idToken"])
            self._status.setText("Verification email sent again.")
        except fb.AuthError as exc:
            self._status.setText(str(exc))
        QtCore.QTimer.singleShot(30000, lambda: self._resend.setEnabled(True))


class _ReauthPage(QtWidgets.QWidget):
    """
    Shown when the account is fine but the licence is not.

    Happens whenever APEX issues a new key: Regenerate Key kills the old one
    immediately, so the next launch lands here instead of dumping the customer
    back at a password prompt they cannot get past. Cancel Subscription lands
    here too, so the moment a replacement key is issued they can paste it in
    without reinstalling or signing up again.

    Only the key is asked for. The account is already signed in via the saved
    refresh token, so there is nothing a password would add, and Firestore
    still has to agree the key belongs to this address before anything opens.
    """

    reauth_success = QtCore.pyqtSignal(str)   # carries the email
    go_login       = QtCore.pyqtSignal()

    def __init__(self, session: dict):
        super().__init__()
        self._session = session          # the on-disk session (refresh token)
        self._fb_session = None          # live Firebase session, once refreshed
        self._build()

    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(46, 40, 46, 40)
        lay.setSpacing(8)
        lay.addStretch()

        title = QtWidgets.QLabel("Licence Required")
        title.setStyleSheet(
            f"color:{_ACCENT}; font-size:22px; font-weight:bold; letter-spacing:1px;")
        lay.addWidget(title)

        sub = QtWidgets.QLabel("APEX Ground Control Station")
        sub.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        lay.addWidget(sub)
        lay.addSpacing(10)

        self._reason = QtWidgets.QLabel(
            "Your licence key is no longer valid. Enter the key APEX sent you "
            "to continue.")
        self._reason.setWordWrap(True)
        self._reason.setStyleSheet(f"color:{_TEXT}; font-size:12px;")
        lay.addWidget(self._reason)
        lay.addSpacing(12)

        self.email_inp = _labelled_input(lay, "Email", "your@email.com")
        self.licence_inp = _labelled_input(
            lay, "Licence Key", "APEX-XXXXX-XXXXX-XXXXX-XXXXX")
        self.licence_inp.returnPressed.connect(self._do_auth)

        hint = QtWidgets.QLabel("Check your email for the latest key from APEX.")
        hint.setStyleSheet(f"color:{_MUTED}; font-size:10px;")
        lay.addWidget(hint)
        lay.addSpacing(14)

        self.auth_btn = QtWidgets.QPushButton("Authenticate")
        self.auth_btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.auth_btn.setStyleSheet(f"""
            QPushButton {{
                background:{_ACCENT}; color:{_BG}; border:none; border-radius:6px;
                padding:10px; font-size:13px; font-weight:bold;
            }}
            QPushButton:hover {{ background:#7dd3fc; }}
            QPushButton:disabled {{ background:#1e293b; color:#475569; }}
        """)
        self.auth_btn.clicked.connect(self._do_auth)
        lay.addWidget(self.auth_btn)

        other = QtWidgets.QPushButton("Sign in as someone else")
        other.setFlat(True)
        other.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        other.setStyleSheet(
            f"QPushButton {{ color:{_MUTED}; font-size:11px; border:none;"
            f" background:transparent; text-decoration:underline; }}"
            f"QPushButton:hover {{ color:{_ACCENT}; }}")
        other.clicked.connect(self._use_other_account)
        lay.addWidget(other)

        lay.addStretch()

    def prepare(self, email: str, fb_session, reason: str = ""):
        """Called by LoginWindow just before this page is shown."""
        self._fb_session = fb_session
        self.email_inp.setText(email or "")
        self.licence_inp.clear()
        if reason:
            self._reason.setText(reason + "\n\nEnter the key APEX sent you to continue.")
        self.licence_inp.setFocus()

    def _use_other_account(self):
        _clear_session()
        self._session.clear()
        fb.set_current_session({})
        self.go_login.emit()

    def _do_auth(self):
        email = self.email_inp.text().strip().lower()
        key = fb.normalise_key(self.licence_inp.text())
        if not email or not key:
            ThemedMessageBox.warning(
                self, "Missing Details",
                "Enter both your email address and your licence key.")
            return

        self.auth_btn.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            session = self._fb_session
            if session is None:
                token = self._session.get("refreshToken")
                if not token:
                    raise fb.AuthError("Your session has expired. Please sign in again.")
                session = fb.refresh_session({
                    "refreshToken": token, "email": email, "uid": ""})
                info = fb.account_info(session["idToken"])
                session["uid"] = info["uid"]
                session["email"] = info["email"]

            # The signed-in account is the one the key must belong to. Saying so
            # plainly beats a generic mismatch error, which would look identical
            # to mistyping the key.
            if session.get("email") and email != session["email"]:
                QtWidgets.QApplication.restoreOverrideCursor()
                ThemedMessageBox.warning(
                    self, "Different Account",
                    "This computer is signed in as " + session["email"] + ".",
                    "To use a licence issued to " + email +
                    ", choose ‘Sign in as someone else’ below.")
                return

            ok, msg = fb.check_entitlement(session, key)
        except fb.AuthError as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            ThemedMessageBox.critical(self, "Could Not Authenticate", str(exc))
            self.go_login.emit()
            return
        finally:
            self.auth_btn.setEnabled(True)
            QtWidgets.QApplication.restoreOverrideCursor()

        if not ok:
            ThemedMessageBox.critical(self, "Licence Not Valid", msg)
            return

        fb.set_current_session(session)
        fb.save_profile(session, {"licenceKey": key})
        if self._session.get("stay"):
            _save_session(session["email"], session.get("refreshToken", ""))
        self.reauth_success.emit(session["email"])


class _LoginPage(QtWidgets.QWidget):
    # Signals emitted to LoginWindow
    login_success   = QtCore.pyqtSignal(str)   # carries the logged-in email
    go_signup       = QtCore.pyqtSignal()
    # (email, live firebase session, why) -> the licence needs re-entering
    needs_reauth    = QtCore.pyqtSignal(str, object, str)

    def __init__(self, session: dict):
        """session dict is shared with LoginWindow for Stay-Signed-In."""
        super().__init__()
        self._session = session
        self._build()
        # Resume after the window has painted, so a slow network shows the
        # login form rather than a frozen blank panel.
        QtCore.QTimer.singleShot(0, self._try_resume)

    def _try_resume(self):
        """
        Sign back in from the saved refresh token, if there is one.

        Also where this computer discovers it has been signed out: if the
        licence's deviceId has moved to another machine, the saved session is
        dropped and the user is told why. That is the whole of the
        one-computer-at-a-time rule on this side — no polling, so nothing eats
        into Firestore's free daily write quota.
        """
        token = self._session.get("refreshToken")
        if not token:
            return
        try:
            session = fb.refresh_session({
                "refreshToken": token,
                "email": self._session.get("email", ""),
                "uid": "",
            })
            info = fb.account_info(session["idToken"])
            if not info.get("emailVerified"):
                return
            session["uid"] = info["uid"]
            session["email"] = info["email"]

            profile = fb.fetch_profile(session)
            licence = (profile or {}).get("licenceKey", "")
            if not licence:
                return

            ok, msg = fb.device_still_ours(session, licence)
            if not ok:
                # A licence that was regenerated or cancelled is recoverable:
                # APEX issues a new key and the customer pastes it in. Being
                # signed in on another computer is not, so that still drops
                # the saved session and sends them back to the sign-in form.
                if "another computer" in msg.lower():
                    _clear_session()
                    self._session.clear()
                    ThemedMessageBox.information(self, "Signed Out", msg)
                else:
                    self.needs_reauth.emit(session["email"], session, msg)
                return

            fb.check_entitlement(session, licence)
        except fb.AuthError:
            return      # offline or expired — fall back to the normal form
        fb.set_current_session(session)
        self.login_success.emit(session["email"])

    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.setSpacing(10)
        lay.addStretch(1)

        # Title
        title = QtWidgets.QLabel("Sign In")
        title.setStyleSheet(
            f"color:{_ACCENT}; font-size:22px; font-weight:bold;"
            f" letter-spacing:1px;")
        lay.addWidget(title)

        sub = QtWidgets.QLabel("APEX Ground Control Station")
        sub.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        lay.addWidget(sub)
        lay.addSpacing(10)

        # Email
        self.email_inp = _labelled_input(lay, "Email", "your@email.com")

        # Pre-fill if stay-signed-in was set last session (memory only)
        if self._session.get("email"):
            self.email_inp.setText(self._session["email"])

        # Password
        self.pwd_inp = _labelled_input(
            lay, "Password", "••••••••",
            echo=QtWidgets.QLineEdit.Password)

        # No password pre-fill any more: nothing stores it. "Stay signed in"
        # now resumes from the saved refresh token instead (see _try_resume).

        # Show Password checkbox
        show_cb = QtWidgets.QCheckBox("Show Password")
        show_cb.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        show_cb.toggled.connect(
            lambda on: self.pwd_inp.setEchoMode(
                QtWidgets.QLineEdit.Normal if on
                else QtWidgets.QLineEdit.Password))
        lay.addWidget(show_cb)

        # Stay signed in
        self.stay_cb = QtWidgets.QCheckBox("Stay signed in")
        self.stay_cb.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        if self._session.get("stay"):
            self.stay_cb.setChecked(True)
        lay.addWidget(self.stay_cb)

        lay.addSpacing(6)

        # Sign In button
        sign_in_btn = QtWidgets.QPushButton("Sign In")
        sign_in_btn.setMinimumHeight(36)
        sign_in_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: {_BG};
                border: none; border-radius: 5px;
                font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background: #7dd3fc; }}
        """)
        sign_in_btn.clicked.connect(self._do_login)
        lay.addWidget(sign_in_btn)

        # Forgot / Sign Up row
        link_row = QtWidgets.QHBoxLayout()
        forgot_btn = QtWidgets.QPushButton("Forgot Password?")
        forgot_btn.setFlat(True)
        forgot_btn.setMinimumHeight(28)
        forgot_btn.setStyleSheet(
            f"color:{_MUTED}; font-size:13px; border:none; padding:2px 4px;")
        forgot_btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        forgot_btn.clicked.connect(self._do_forgot)

        signup_btn = QtWidgets.QPushButton("Sign Up")
        signup_btn.setFlat(True)
        signup_btn.setMinimumHeight(28)
        signup_btn.setStyleSheet(
            f"color:{_ACCENT}; font-size:13px; border:none; padding:2px 4px;")
        signup_btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        signup_btn.clicked.connect(self.go_signup.emit)

        lay.addSpacing(8)          # ← ADD THIS: gap above the row
        link_row.addWidget(forgot_btn)
        link_row.addStretch()
        link_row.addWidget(signup_btn)
        lay.addLayout(link_row)

        lay.addStretch(2)

    # ── Actions ───────────────────────────────────────────────────────────────
    def _do_login(self):
        email = self.email_inp.text().strip().lower()
        pwd   = self.pwd_inp.text().strip()

        if not email or not pwd:
            QtWidgets.QMessageBox.warning(self, "Missing fields",
                                          "Please enter email and password.")
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            session = fb.sign_in(email, pwd)

            if not fb.account_info(session["idToken"]).get("emailVerified"):
                QtWidgets.QApplication.restoreOverrideCursor()
                QtWidgets.QMessageBox.warning(
                    self, "Email Not Verified",
                    "Please confirm the verification link we emailed to\n"
                    + email + "\n\nThen sign in again.")
                try:
                    fb.send_verification_email(session["idToken"])
                except fb.AuthError:
                    pass
                return

            profile = fb.fetch_profile(session)
            licence = (profile or {}).get("licenceKey", "")
            if not licence:
                QtWidgets.QApplication.restoreOverrideCursor()
                self.needs_reauth.emit(
                    session["email"], session,
                    "This account has no APEX GCS licence attached.")
                return

            # Re-checked on every sign-in, so a cancelled licence stops working
            # and signing in here is what signs any other computer out.
            ok, msg = fb.check_entitlement(session, licence)
        except fb.AuthError as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "Login Failed", str(exc))
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        if not ok:
            self.needs_reauth.emit(session["email"], session, msg)
            return

        # Only the email is remembered now. The old code wrote the password to
        # a temp file in plain text; the refresh token below is scoped, and is
        # revocable from the Firebase console.
        if self.stay_cb.isChecked():
            self._session.update({"email": email, "stay": True})
            _save_session(email, session.get("refreshToken", ""))
        else:
            self._session.clear()
            _clear_session()

        fb.set_current_session(session)
        self.login_success.emit(email)

    def _do_forgot(self):
        email = self.email_inp.text().strip()
        if not email:
            QtWidgets.QMessageBox.warning(
                self, "No email", "Enter your email in the field above first.")
            return

        # Firebase sends and handles the reset itself, so there is no OTP to
        # generate here and no mail credential in the build. Deliberately says
        # the same thing whether or not the address exists, so this can't be
        # used to find out who has an account.
        try:
            fb.send_password_reset(email)
        except fb.AuthError as exc:
            if "No account found" not in str(exc):
                QtWidgets.QMessageBox.critical(self, "Error", str(exc))
                return
        QtWidgets.QMessageBox.information(
            self, "Check Your Email",
            "If an account exists for\n" + email + "\n\n"
            "a password reset link is on its way. Open it, set a new "
            "password, then sign in here.")


# ══════════════════════════════════════════════════════════════════════════════
#  Terms & Conditions Dialog
# ══════════════════════════════════════════════════════════════════════════════
_TERMS_TEXT = """\
1. Use of this software is restricted to authorized users only.

2. The ground station is intended for educational and research purposes only.

3. Users must not transmit unauthorized commands to the CubeSat system.

4. The developer is not responsible for misuse or improper operation.

5. Telemetry data received may be logged for analysis and testing.

6. Users must not reverse engineer, modify, or redistribute the software.

7. Communication links may be interrupted; reliability is not guaranteed.

8. Users are responsible for compliance with local RF communication regulations.

9. The software may store configuration and login data locally.

10. Unauthorized access attempts may result in access being blocked.

11. The system should not be used for safety-critical operations.

12. Telemetry, images, and user-generated data may be uploaded to an internet-accessible server.

13. Users should avoid transmitting sensitive or confidential information.

14. The developer is not responsible for data visibility, exposure, or third-party access once uploaded online.

15. By logging in, the user agrees to operate the system responsibly.
"""

class TermsDialog(QtWidgets.QDialog):
    """Modal dialog that displays the Terms & Conditions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Terms & Conditions")
        self.setFixedSize(540, 480)
        self.setModal(True)
        self.setWindowFlags(
            QtCore.Qt.Dialog |
            QtCore.Qt.WindowCloseButtonHint)
        self.setStyleSheet(_BASE_STYLE + f"""
            QDialog {{
                background: {_BG};
                border: 1px solid {_BORDER};
                border-radius: 10px;
            }}
            QTextEdit {{
                background: {_SURFACE};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 10px;
                font-size: 12px;
                font-family: Consolas;
                line-height: 1.6;
            }}
            QScrollBar:vertical {{
                background: {_BG};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {_BORDER};
                border-radius: 4px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {_ACCENT};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        # ── Header row (title + X close button) ──────────────────────────────
        hdr_row = QtWidgets.QHBoxLayout()

        icon_lbl = QtWidgets.QLabel("📋")
        icon_lbl.setStyleSheet("font-size: 18px;")
        hdr_row.addWidget(icon_lbl)

        title_lbl = QtWidgets.QLabel("Terms & Conditions")
        title_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 15px; font-weight: bold;")
        hdr_row.addWidget(title_lbl)
        hdr_row.addStretch()       

        lay.addLayout(hdr_row)

        # ── Divider ───────────────────────────────────────────────────────────
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet(f"color: {_BORDER};")
        lay.addWidget(line)

        # ── Scrollable T&C text ───────────────────────────────────────────────
        text_edit = QtWidgets.QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(_TERMS_TEXT.strip())
        text_edit.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
        lay.addWidget(text_edit)

        # ── Close button at bottom ────────────────────────────────────────────
        ok_btn = QtWidgets.QPushButton("Close")
        ok_btn.setMinimumHeight(34)
        ok_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: {_BG};
                border: none; border-radius: 5px;
                font-size: 12px; font-weight: bold;
            }}
            QPushButton:hover {{ background: #7dd3fc; }}
        """)
        ok_btn.clicked.connect(self.accept)
        lay.addWidget(ok_btn)


# ══════════════════════════════════════════════════════════════════════════════
#  Page 1 — Sign Up form  (right-panel widget, scrollable)
# ══════════════════════════════════════════════════════════════════════════════
class _SignUpPage(QtWidgets.QWidget):
    go_login     = QtCore.pyqtSignal()
    signup_done  = QtCore.pyqtSignal()     # emitted after successful signup

    def __init__(self):
        super().__init__()
        self._build()

    def _build(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scroll area so tall form doesn't clip
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent; border: none;")

        inner_widget = QtWidgets.QWidget()
        inner_widget.setStyleSheet(f"""
            QWidget {{ background: {_BG}; }}
            QLineEdit {{
                background: {_SURFACE};
                color: {_TEXT};
                border: 1.5px solid #2d4a5a;
                border-radius: 5px;
                padding: 6px 10px;
                font-size: 12px;
                font-family: Consolas;
            }}
            QLineEdit:focus {{
                border: 2px solid #4a7a96;
                background: #0f2133;
            }}
        """)
        lay = QtWidgets.QVBoxLayout(inner_widget)
        lay.setContentsMargins(40, 30, 40, 30)
        lay.setSpacing(8)

        # Title
        title = QtWidgets.QLabel("Create Account")
        title.setStyleSheet(
            f"color:{_ACCENT}; font-size:20px; font-weight:bold;")
        lay.addWidget(title)
        lay.addSpacing(6)

        self.name_inp    = _labelled_input(lay, "Full Name",    "John Doe")
        self.email_inp   = _labelled_input(lay, "Email",        "your@email.com")
        self.pwd_inp     = _labelled_input(lay, "Create Password", "••••••••",
                                           echo=QtWidgets.QLineEdit.Password)
        self.repwd_inp   = _labelled_input(lay, "Re-enter Password", "••••••••",
                                           echo=QtWidgets.QLineEdit.Password)

        show_cb = QtWidgets.QCheckBox("Show Password")
        show_cb.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        show_cb.toggled.connect(self._toggle_pwd)
        lay.addWidget(show_cb)
        lay.addSpacing(4)

        self.inst_inp    = _labelled_input(lay, "Institution",  "Your College / Org")
        self.city_inp    = _labelled_input(lay, "City",         "Bengaluru")

        # ── State dropdown (Indian states & UTs) ──────────────────────────────
        state_lbl = QtWidgets.QLabel("State")
        state_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 13px; font-weight: bold; margin-bottom: 3px;")
        lay.addWidget(state_lbl)

        self.state_inp = QtWidgets.QComboBox()
        self.state_inp.setMinimumHeight(38)
        self.state_inp.setStyleSheet(f"""
            QComboBox {{
                background: {_SURFACE};
                color: {_TEXT};
                border: 1.5px solid #2d4a5a;
                border-radius: 5px;
                padding: 6px 10px;
                font-size: 14px;
                font-family: Consolas;
            }}
            QComboBox:focus {{
                border: 2px solid #4a7a96;
                background: #0f2133;
            }}
            QComboBox QAbstractItemView {{
                background: {_SURFACE};
                color: {_TEXT};
                selection-background-color: {_ACCENT};
                selection-color: {_BG};
                border: 1px solid {_BORDER};
                font-size: 13px;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 24px;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 7px solid {_ACCENT};
                margin-right: 8px;
            }}
        """)
        _INDIA_STATES = [
            "-- Select State / UT --",
            # States
            "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar",
            "Chhattisgarh", "Goa", "Gujarat", "Haryana", "Himachal Pradesh",
            "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra",
            "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
            "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
            "Uttar Pradesh", "Uttarakhand", "West Bengal",
            # Union Territories
            "Andaman and Nicobar Islands", "Chandigarh",
            "Dadra and Nagar Haveli and Daman and Diu", "Delhi (NCT)",
            "Jammu & Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
        ]
        self.state_inp.addItems(_INDIA_STATES)
        lay.addWidget(self.state_inp)

        self.country_inp = _labelled_input(lay, "Country",      "India")
        self.pin_inp     = _labelled_input(lay, "Pincode",      "560001")

        # Licence key, emailed when the CubeSat order was fulfilled. Checked
        # against the email above before any verification mail goes out, so a
        # mismatch is reported immediately rather than after a round trip.
        self.licence_inp = _labelled_input(
            lay, "Licence Key", "APEX-XXXXX-XXXXX-XXXXX-XXXXX")
        self.licence_inp.setToolTip(
            "The licence key emailed to you when your CubeSat order was confirmed.\n"
            "It only works with the email address the order was placed with.")
        _hint = QtWidgets.QLabel("Sent to your order email when your CubeSat shipped.")
        _hint.setStyleSheet(f"color:{_MUTED}; font-size:10px;")
        lay.addWidget(_hint)

        # Terms checkbox row — checkbox + "I agree to the" + clickable link
        terms_row = QtWidgets.QHBoxLayout()
        terms_row.setSpacing(4)
        terms_row.setContentsMargins(0, 0, 0, 0)

        self.agree_cb = QtWidgets.QCheckBox("I agree to the")
        self.agree_cb.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        self.agree_cb.toggled.connect(
            lambda on: self.signup_btn.setEnabled(on))
        terms_row.addWidget(self.agree_cb)

        terms_link = QtWidgets.QPushButton("Terms & Conditions")
        terms_link.setFlat(True)
        terms_link.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        terms_link.setStyleSheet(f"""
            QPushButton {{
                color: {_ACCENT};
                font-size: 11px;
                border: none;
                padding: 0px;
                margin: 0px;
                text-decoration: underline;
                background: transparent;
                min-width: 0px;
            }}
            QPushButton:hover {{ color: #7dd3fc; }}
        """)
        terms_link.clicked.connect(self._show_terms)
        terms_row.addWidget(terms_link)
        terms_row.addStretch()

        lay.addLayout(terms_row)
        lay.addSpacing(6)

        # Sign Up button
        self.signup_btn = QtWidgets.QPushButton("Sign Up")
        self.signup_btn.setMinimumHeight(36)
        self.signup_btn.setEnabled(False)
        self.signup_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: {_BG};
                border: none; border-radius: 5px;
                font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background: #7dd3fc; }}
            QPushButton:disabled {{
                background: {_BORDER}; color: {_MUTED};
            }}
        """)
        self.signup_btn.clicked.connect(self._do_signup)
        lay.addWidget(self.signup_btn)

        # Back to login
        back_btn = QtWidgets.QPushButton("← Back to Login")
        back_btn.setFlat(True)
        back_btn.setStyleSheet(
            f"color:{_MUTED}; font-size:11px; border:none; padding:4px 0;")
        back_btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        back_btn.clicked.connect(self.go_login.emit)
        lay.addWidget(back_btn)

        scroll.setWidget(inner_widget)
        outer.addWidget(scroll)

    # ── Actions ───────────────────────────────────────────────────────────────
    def _show_terms(self):
        dlg = TermsDialog(self)
        dlg.exec_()

    def _toggle_pwd(self, on):
        mode = (QtWidgets.QLineEdit.Normal if on
                else QtWidgets.QLineEdit.Password)
        self.pwd_inp.setEchoMode(mode)
        self.repwd_inp.setEchoMode(mode)

    def _do_signup(self):
        name  = self.name_inp.text().strip()
        email = self.email_inp.text().strip()
        pwd   = self.pwd_inp.text().strip()
        repwd = self.repwd_inp.text().strip()
        inst  = self.inst_inp.text().strip()
        city  = self.city_inp.text().strip()
        state = self.state_inp.currentText().strip()
        cntry = self.country_inp.text().strip()
        pin   = self.pin_inp.text().strip()

        if state == "-- Select State / UT --":
            QtWidgets.QMessageBox.warning(
                self, "Missing Fields", "Please select your state.")
            return

        if "" in (name, email, pwd, repwd, inst, city, cntry, pin):
            QtWidgets.QMessageBox.warning(
                self, "Missing Fields", "Please fill in all fields.")
            return

        if pwd != repwd:
            QtWidgets.QMessageBox.warning(
                self, "Password Mismatch", "Passwords do not match.")
            return

        licence = fb.normalise_key(self.licence_inp.text())
        if not licence:
            QtWidgets.QMessageBox.warning(
                self, "Missing Licence Key",
                "Please enter the licence key emailed to you when your "
                "CubeSat order was confirmed.")
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            # Create the account first: the licence can only be read by the
            # address it was issued to, so we need to be signed in as that
            # address before we can tell whether the key matches.
            session = fb.sign_up(email, pwd)
            licence_doc = fb.fetch_licence(session["idToken"], licence)
            if licence_doc is None:
                QtWidgets.QApplication.restoreOverrideCursor()
                QtWidgets.QMessageBox.critical(
                    self, "Licence Does Not Match",
                    "That licence key does not match this email address.\n\n"
                    "Use the same address your CubeSat order was placed with.")
                return
            if licence_doc.get("status") != "active":
                QtWidgets.QApplication.restoreOverrideCursor()
                QtWidgets.QMessageBox.critical(
                    self, "Licence Cancelled",
                    "This licence has been cancelled.\n\n"
                    "Please contact APEX to have it reactivated.")
                return
            if licence_doc.get("uid"):
                QtWidgets.QApplication.restoreOverrideCursor()
                QtWidgets.QMessageBox.critical(
                    self, "Licence Already Used",
                    "This licence has already been activated on another "
                    "account.\n\nContact APEX if you need it moved.")
                return

            fb.send_verification_email(session["idToken"])
        except fb.AuthError as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "Sign Up Failed", str(exc))
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        dlg = _VerifyEmailDialog(self, session, email)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        session = dlg.session       # refreshed, so it carries email_verified
        try:
            ok, msg = fb.check_entitlement(session, licence)
            if not ok:
                QtWidgets.QMessageBox.critical(self, "Licence Error", msg)
                return
            fb.save_profile(session, {
                "name": name, "institution": inst, "city": city,
                "state": state, "country": cntry, "pincode": pin,
                "licenceKey": licence,
            })
        except fb.AuthError as exc:
            QtWidgets.QMessageBox.critical(self, "Sign Up Failed", str(exc))
            return

        # The Drive backup folder is unrelated to licensing; never block on it.
        import threading as _t
        _t.Thread(target=auth.create_user_drive_folder,
                  args=(email,), daemon=True).start()

        QtWidgets.QMessageBox.information(
            self, "Account Created",
            "Account created and licence activated. Please sign in.")
        self.signup_done.emit()


# ══════════════════════════════════════════════════════════════════════════════
#  Page 2 — COM Port selector  (right-panel widget)
# ══════════════════════════════════════════════════════════════════════════════
class _ComPortPage(QtWidgets.QWidget):
    port_selected = QtCore.pyqtSignal(str)   # emits the chosen port string

    def __init__(self):
        super().__init__()
        self._build()

    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.setSpacing(14)
        lay.addStretch(1)

        icon = QtWidgets.QLabel("📡")
        icon.setAlignment(QtCore.Qt.AlignCenter)
        icon.setStyleSheet("font-size: 42px;")
        lay.addWidget(icon)

        title = QtWidgets.QLabel("SELECT COM PORT")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet(
            f"color:{_ACCENT}; font-size:16px; font-weight:bold;"
            f" letter-spacing:2px;")
        lay.addWidget(title)

        sub = QtWidgets.QLabel("Connect the Ground Station serial interface")
        sub.setAlignment(QtCore.Qt.AlignCenter)
        sub.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        lay.addWidget(sub)

        # Divider
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet(f"color:{_BORDER};")
        lay.addWidget(line)

        # Port row
        port_row = QtWidgets.QHBoxLayout()
        port_lbl = QtWidgets.QLabel("Port:")
        port_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        port_lbl.setFixedWidth(36)
        self.combo = QtWidgets.QComboBox()
        self.combo.setMinimumHeight(32)
        port_row.addWidget(port_lbl)
        port_row.addWidget(self.combo)
        lay.addLayout(port_row)

        self._refresh()

        # Buttons
        btn_row = QtWidgets.QHBoxLayout()
        refresh_btn = QtWidgets.QPushButton("🔄  Refresh")
        connect_btn = QtWidgets.QPushButton("🔌  Connect")
        connect_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: {_BG};
                border: 1px solid {_ACCENT}; padding: 6px 18px;
                border-radius: 5px; font-size: 12px;
                font-weight: bold; min-width: 80px;
            }}
            QPushButton:hover {{ background: #7dd3fc; }}
        """)
        refresh_btn.clicked.connect(self._refresh)
        connect_btn.clicked.connect(self._connect)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(connect_btn)
        lay.addLayout(btn_row)

        lay.addStretch(2)

    def _refresh(self):
        self.combo.clear()
        ports = serial.tools.list_ports.comports()
        for p in ports:
            self.combo.addItem(f"{p.device}  —  {p.description}")
        if not ports:
            self.combo.addItem("No ports found")

    def _connect(self):
        port = self.combo.currentText().split("  —  ")[0].strip()
        self.port_selected.emit(port)


# ══════════════════════════════════════════════════════════════════════════════
#  LoginWindow   ← called from main.py
# ══════════════════════════════════════════════════════════════════════════════
class LoginWindow(QtWidgets.QDialog):
    """
    Branded login window that appears at startup.

    Flow:
        Login page  →  (success)  →  COM Port page  →  (connected)  →  accepted()
                    →  (sign up)  →  Sign Up page   →  (done)       →  Login page

    After exec_() returns Accepted, call .get_port() to retrieve the COM port.
    """

    def __init__(self):
        super().__init__()
        self._selected_port = ""
        self._logged_in_email = ""
        # Load session from disk — survives app close, cleared on PC reboot
        self._session: dict = _load_session()

        self.setWindowTitle("Login")
        self.setFixedSize(960, 620)
        self.setWindowFlags(
            QtCore.Qt.Dialog |
            QtCore.Qt.WindowCloseButtonHint)
        self.setStyleSheet(_BASE_STYLE + f"""
            QDialog {{
                background: {_BG};
                border: 1px solid {_BORDER};
                border-radius: 12px;
            }}
        """)

        # Centre on screen
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.width()  // 2,
            screen.center().y() - self.height() // 2,
        )

        self._build()

    # ── Build layout ──────────────────────────────────────────────────────────
    def _build(self):
        root_lay = QtWidgets.QHBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ── LEFT PANEL (branded background + logo) ────────────────────────────
        left = QtWidgets.QLabel()
        left.setFixedWidth(340)
        left.setScaledContents(False)
        left.setAlignment(QtCore.Qt.AlignCenter)

        # Background image
        bg_pix = QtGui.QPixmap(BG_IMAGE)
        if not bg_pix.isNull():
            bg_pix = bg_pix.scaled(
                340, 580,
                QtCore.Qt.KeepAspectRatioByExpanding,
                QtCore.Qt.SmoothTransformation)
            # Crop to exact size
            x_off = max(0, (bg_pix.width()  - 340) // 2)   # ← still 340, wrong
            y_off = max(0, (bg_pix.height() - 580) // 2)   # ← still 580, wrong
            bg_pix = bg_pix.copy(x_off, y_off, 340, 580)
            left.setPixmap(bg_pix)

        # Overlay container (title top, logo below it)
        left_overlay = QtWidgets.QWidget(left)
        left_overlay.setGeometry(0, 0, 340, 580)
        left_overlay.setStyleSheet("background: rgba(2,6,23,0.60);")
        ov_lay = QtWidgets.QVBoxLayout(left_overlay)
        ov_lay.setContentsMargins(12, 20, 12, 16)
        ov_lay.setSpacing(0)

        # Station name — top, centred, large distinct font
        title_lbl = QtWidgets.QLabel()
        title_lbl.setAlignment(QtCore.Qt.AlignCenter)
        title_lbl.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding)
        title_pix = QtGui.QPixmap(title)
        if not title_pix.isNull():
            title_pix = title_pix.scaled(
                310, 360,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation)
            title_lbl.setPixmap(title_pix)
        ov_lay.addWidget(title_lbl, stretch=1)
        

        """ov_lay.addSpacing(10)"""

        # Logo — fills all remaining space below the title
        logo_lbl = QtWidgets.QLabel()
        logo_lbl.setAlignment(QtCore.Qt.AlignCenter)
        logo_lbl.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding)
        logo_pix = QtGui.QPixmap(APEX_LOGO)
        if not logo_pix.isNull():
            logo_pix = logo_pix.scaled(
                310, 360,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation)
            logo_lbl.setPixmap(logo_pix)
        ov_lay.addWidget(logo_lbl, stretch=1)

        root_lay.addWidget(left)

        # ── Vertical divider ──────────────────────────────────────────────────
        div = QtWidgets.QFrame()
        div.setFrameShape(QtWidgets.QFrame.VLine)
        div.setStyleSheet(f"color:{_BORDER};")
        div.setFixedWidth(1)
        root_lay.addWidget(div)

        # ── RIGHT PANEL (QStackedWidget) ──────────────────────────────────────
        self._stack = QtWidgets.QStackedWidget()
        self._stack.setStyleSheet(f"background: {_BG};")

        self._login_page  = _LoginPage(self._session)
        self._signup_page = _SignUpPage()
        self._com_page    = _ComPortPage()
        self._reauth_page = _ReauthPage(self._session)

        self._stack.addWidget(self._login_page)    # index 0
        self._stack.addWidget(self._signup_page)   # index 1
        self._stack.addWidget(self._com_page)      # index 2
        self._stack.addWidget(self._reauth_page)   # index 3

        # Wire signals
        self._login_page.login_success.connect(self._on_login_success)
        self._login_page.go_signup.connect(lambda: self._stack.setCurrentIndex(1))
        self._login_page.needs_reauth.connect(self._on_needs_reauth)

        self._reauth_page.reauth_success.connect(self._on_login_success)
        self._reauth_page.go_login.connect(lambda: self._stack.setCurrentIndex(0))

        self._signup_page.go_login.connect(lambda: self._stack.setCurrentIndex(0))
        self._signup_page.signup_done.connect(lambda: self._stack.setCurrentIndex(0))

        self._com_page.port_selected.connect(self._on_port_selected)

        root_lay.addWidget(self._stack)

        # Resuming a saved session is _LoginPage._try_resume's job: it only
        # emits login_success once the licence has actually been checked.
        # There used to be a second auto-login here that skipped straight past
        # that, which let a revoked licence through whenever "stay signed in"
        # was ticked.

    # ── Slot handlers ─────────────────────────────────────────────────────────
    def _on_login_success(self, email: str = ""):
        """Store the logged-in email and switch to the COM port page."""
        self._logged_in_email = email
        self._stack.setCurrentIndex(2)

    def _on_needs_reauth(self, email: str, session, reason: str):
        """Licence gone or replaced: ask for the new key rather than giving up."""
        self._reauth_page.prepare(email, session, reason)
        self._stack.setCurrentIndex(3)

    def _on_port_selected(self, port: str):
        if port == "No ports found" or not port:
            QtWidgets.QMessageBox.warning(
                self, "No Port",
                "No COM port selected. Please connect the device and refresh.")
            return
        self._selected_port = port
        self.accept()

    def get_port(self) -> str:
        return self._selected_port

    def get_email(self) -> str:
        return self._logged_in_email

# ══════════════════════════════════════════════════════════════════════════════
#  ComPortSelector
# ══════════════════════════════════════════════════════════════════════════════
class ComPortSelector(QtWidgets.QDialog):
    """Startup dialog — operator picks the COM port for the CubeSat link."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("APEX Ground Control Station")
        self.setFixedSize(420, 280)
        self.setStyleSheet(_BASE_STYLE + f"""
            QDialog {{
                background-color: {_BG};
                border: 1px solid {_BORDER};
                border-radius: 10px;
            }}
        """)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(16)

        # ── Header ────────────────────────────────────────────────────────────
        icon_lbl = QtWidgets.QLabel("📡")
        icon_lbl.setAlignment(QtCore.Qt.AlignCenter)
        icon_lbl.setStyleSheet("font-size: 36px;")
        root.addWidget(icon_lbl)

        title = QtWidgets.QLabel("SELECT COM PORT")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet(f"""
            color: {_ACCENT};
            font-size: 16px;
            font-weight: bold;
            letter-spacing: 2px;
        """)
        root.addWidget(title)

        sub = QtWidgets.QLabel("Connect to the Ground Station serial interface")
        sub.setAlignment(QtCore.Qt.AlignCenter)
        sub.setStyleSheet(f"color: {_MUTED}; font-size: 11px;")
        root.addWidget(sub)

        # ── Divider ───────────────────────────────────────────────────────────
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet(f"color: {_BORDER};")
        root.addWidget(line)

        # ── Port selector ─────────────────────────────────────────────────────
        port_row = QtWidgets.QHBoxLayout()
        port_lbl = QtWidgets.QLabel("Port:")
        port_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 12px;")
        port_lbl.setFixedWidth(36)
        self.combo = QtWidgets.QComboBox()
        self.combo.setMinimumHeight(32)
        port_row.addWidget(port_lbl)
        port_row.addWidget(self.combo)
        root.addLayout(port_row)

        self.refresh_ports()

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(10)

        refresh_btn = QtWidgets.QPushButton("🔄  Refresh")
        connect_btn = QtWidgets.QPushButton("🔌  Connect")
        connect_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT};
                color: {_BG};
                border: 1px solid {_ACCENT};
                padding: 6px 18px;
                border-radius: 5px;
                font-size: 12px;
                font-weight: bold;
                min-width: 80px;
            }}
            QPushButton:hover {{
                background: #7dd3fc;
            }}
        """)

        refresh_btn.clicked.connect(self.refresh_ports)
        connect_btn.clicked.connect(self.accept)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(connect_btn)
        root.addLayout(btn_row)

    def refresh_ports(self):
        self.combo.clear()
        ports = serial.tools.list_ports.comports()
        for port in ports:
            self.combo.addItem(f"{port.device}  —  {port.description}")
        if not ports:
            self.combo.addItem("No ports found")

    def get_port(self) -> str:
        # Return only the device name (before the " — " separator)
        return self.combo.currentText().split("  —  ")[0].strip()


# ══════════════════════════════════════════════════════════════════════════════
#  Toast
# ══════════════════════════════════════════════════════════════════════════════
class Toast(QtWidgets.QFrame):
    """Transient themed notification — appears bottom-right, auto-closes."""

    def __init__(self, parent: QtWidgets.QWidget, text: str):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {_SURFACE};
                border: 1px solid {_ACCENT};
                border-radius: 8px;
            }}
            QLabel {{
                color: {_ACCENT};
                font-family: Consolas;
                font-size: 12px;
                padding: 10px 14px;
            }}
        """)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        dot = QtWidgets.QLabel("●")
        dot.setStyleSheet(f"color: {_GREEN}; padding-left: 12px; padding-right: 4px;")
        layout.addWidget(dot)

        msg = QtWidgets.QLabel(text)
        layout.addWidget(msg)

        self.adjustSize()
        pr = parent.rect()
        self.move(pr.width() - self.width() - 20,
                  pr.height() - self.height() - 20)
        self.raise_()
        self.show()
        QtCore.QTimer.singleShot(2500, self.close)


# ══════════════════════════════════════════════════════════════════════════════
#  ThemedMessageBox
# ══════════════════════════════════════════════════════════════════════════════
# Button-role constants — same numeric values as QMessageBox so callers
# can compare with QMessageBox.Yes / .No / .Ok etc. without any changes.
class ThemedMessageBox(QtWidgets.QDialog):
    """
    Fully themed replacement for QMessageBox.

    Usage (mirrors QMessageBox API):

        reply = ThemedMessageBox.question(
            parent, "Confirm", "Are you sure?",
            buttons=["Yes", "No"],          # left→right order
            danger_buttons=["Yes"],         # shown in red
        )
        if reply == "Yes": ...

        ThemedMessageBox.warning(parent, "Oops", "Something went wrong.")
        ThemedMessageBox.information(parent, "Done", "Mission saved.")
        ThemedMessageBox.critical(parent, "Error", "Port not found.")
    """

    # ── icon lookup ───────────────────────────────────────────────────────────
    _ICONS = {
        "information": ("ℹ",  _ACCENT),
        "warning":     ("⚠",  _YELLOW),
        "critical":    ("✖",  _RED),
        "question":    ("?",  _ACCENT),
    }

    def __init__(
        self,
        parent,
        title: str,
        text: str,
        informative: str = "",
        buttons: list[str] = None,
        danger_buttons: list[str] = None,
        kind: str = "information",
    ):
        super().__init__(parent)
        self._result = None
        buttons       = buttons       or ["OK"]
        danger_buttons = danger_buttons or []

        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setStyleSheet(_BASE_STYLE + f"""
            QDialog {{
                background-color: {_BG};
                border: 1px solid {_BORDER};
                border-radius: 10px;
            }}
        """)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)

        # ── Icon + title row ──────────────────────────────────────────────────
        icon_char, icon_color = self._ICONS.get(kind, ("ℹ", _ACCENT))
        hdr = QtWidgets.QHBoxLayout()
        icon_lbl = QtWidgets.QLabel(icon_char)
        icon_lbl.setStyleSheet(
            f"color: {icon_color}; font-size: 28px; margin-right: 10px;"
        )
        icon_lbl.setFixedWidth(38)
        title_lbl = QtWidgets.QLabel(title)
        title_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 15px; font-weight: bold;"
        )
        hdr.addWidget(icon_lbl)
        hdr.addWidget(title_lbl)
        hdr.addStretch()
        root.addLayout(hdr)

        # ── Divider ───────────────────────────────────────────────────────────
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet(f"color: {_BORDER};")
        root.addWidget(line)

        # ── Main text ─────────────────────────────────────────────────────────
        msg_lbl = QtWidgets.QLabel(text)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 13px; line-height: 1.5;")
        root.addWidget(msg_lbl)

        if informative:
            info_lbl = QtWidgets.QLabel(informative)
            info_lbl.setWordWrap(True)
            info_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 12px;")
            root.addWidget(info_lbl)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        for label in buttons:
            btn = QtWidgets.QPushButton(label)
            if label in danger_buttons:
                btn.setObjectName("dangerBtn")
            btn.clicked.connect(lambda _, l=label: self._on_click(l))
            btn_row.addWidget(btn)
        root.addLayout(btn_row)

    def _on_click(self, label: str):
        self._result = label
        self.accept()

    def result_text(self) -> str:
        return self._result or ""

    # ── Static convenience methods (mirroring QMessageBox) ───────────────────
    @staticmethod
    def information(parent, title: str, text: str, informative: str = "") -> str:
        dlg = ThemedMessageBox(parent, title, text, informative,
                               buttons=["OK"], kind="information")
        dlg.exec_()
        return dlg.result_text()

    @staticmethod
    def warning(parent, title: str, text: str, informative: str = "") -> str:
        dlg = ThemedMessageBox(parent, title, text, informative,
                               buttons=["OK"], kind="warning")
        dlg.exec_()
        return dlg.result_text()

    @staticmethod
    def critical(parent, title: str, text: str, informative: str = "") -> str:
        dlg = ThemedMessageBox(parent, title, text, informative,
                               buttons=["OK"], kind="critical")
        dlg.exec_()
        return dlg.result_text()

    @staticmethod
    def question(
        parent,
        title: str,
        text: str,
        informative: str = "",
        buttons: list[str] = None,
        danger_buttons: list[str] = None,
    ) -> str:
        dlg = ThemedMessageBox(
            parent, title, text, informative,
            buttons=buttons or ["Yes", "No"],
            danger_buttons=danger_buttons or [],
            kind="question",
        )
        dlg.exec_()
        return dlg.result_text()


# ══════════════════════════════════════════════════════════════════════════════
#  UserProfileDialog   ← opened by the ⚙ button in TelemetryPanel
# ══════════════════════════════════════════════════════════════════════════════
class _MyBackupsDialog(QtWidgets.QDialog):
    """
    What is actually in the customer's Firebase Storage backup.

    Replaces the old "Open Backup" link to a shared Google Drive folder. That
    folder lived in one APEX-owned Drive account, reachable with a token that
    shipped inside every build; this reads only the signed-in customer's own
    files, through links the server mints on request and which expire in an
    hour.
    """

    _loaded = QtCore.pyqtSignal(object, object)     # (files, error)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("APEX GCS — My Backups")
        self.setMinimumSize(640, 420)
        self.setModal(True)
        self.setStyleSheet(_BASE_STYLE)
        self._files = []
        self._build()
        self._loaded.connect(self._on_loaded)
        self._load()

    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)

        title = QtWidgets.QLabel("My Backups")
        title.setStyleSheet(f"color:{_ACCENT}; font-size:18px; font-weight:bold;")
        lay.addWidget(title)

        self._subtitle = QtWidgets.QLabel("Loading…")
        self._subtitle.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        lay.addWidget(self._subtitle)

        self._table = QtWidgets.QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["File", "Folder", "Size", "Backed up"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 280)
        self._table.setColumnWidth(1, 130)
        self._table.setColumnWidth(2, 80)
        self._table.setStyleSheet(f"""
            QTableWidget {{ background:{_BG}; color:{_TEXT}; gridline-color:{_BORDER};
                            border:1px solid {_BORDER}; border-radius:6px; font-size:11px; }}
            QHeaderView::section {{ background:{_SURFACE}; color:{_MUTED}; border:none;
                                    padding:6px; font-size:10px; }}
            QTableWidget::item:selected {{ background:{_ACCENT}; color:{_BG}; }}
        """)
        self._table.doubleClicked.connect(self._open_selected)
        lay.addWidget(self._table, 1)

        row = QtWidgets.QHBoxLayout()
        self._open_btn = QtWidgets.QPushButton("Open")
        self._open_btn.clicked.connect(self._open_selected)
        self._open_btn.setEnabled(False)
        row.addWidget(self._open_btn)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self._load)
        row.addWidget(refresh)
        row.addStretch()
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

        self._table.itemSelectionChanged.connect(
            lambda: self._open_btn.setEnabled(bool(self._table.selectedItems())))

    def _load(self):
        self._subtitle.setText("Loading…")
        self._table.setRowCount(0)

        def worker():
            session = fb.current_session()
            if not session:
                self._loaded.emit(None, "Not signed in.")
                return
            try:
                self._loaded.emit(fb.list_backups(session), None)
            except Exception as exc:
                self._loaded.emit(None, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    @QtCore.pyqtSlot(object, object)
    def _on_loaded(self, files, error):
        if error:
            self._subtitle.setText(error)
            return
        self._files = files or []
        if not self._files:
            self._subtitle.setText(
                "Nothing backed up yet. Images and reports are uploaded "
                "automatically as they are produced.")
            return
        total = sum(int(f.get("size") or 0) for f in self._files)
        n = len(self._files)
        self._subtitle.setText(
            "{} {}, {}. Double-click one to open it.".format(
                n, "file" if n == 1 else "files", fb.human_bytes(total)))
        self._table.setRowCount(len(self._files))
        for r, f in enumerate(self._files):
            when = str(f.get("uploadedAt") or "")[:16].replace("T", " ")
            for c, v in enumerate([f.get("name", ""), f.get("folder", ""),
                                   fb.human_bytes(f.get("size")), when]):
                self._table.setItem(r, c, QtWidgets.QTableWidgetItem(str(v)))

    def _open_selected(self):
        rows = {i.row() for i in self._table.selectedItems()}
        if not rows:
            return
        f = self._files[sorted(rows)[0]]
        url = f.get("url")
        if url:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))
        else:
            ThemedMessageBox.warning(self, "Unavailable",
                                     "That file has no download link right now.")


class UserProfileDialog(QtWidgets.QDialog):
    """
    Shows the logged-in user's profile info, a Logout button, and a
    link to open their personal Google Drive folder.

    Signals:
        logout_requested  — emitted when the user clicks Logout
    """

    logout_requested = QtCore.pyqtSignal()

    # Column name → friendly label mapping (matches Google Sheet headers)
    _FIELDS = [
        ("Name",        "Full Name"),
        ("Email",       "Email"),
        ("Institution", "College / Institution"),
        ("City",        "City"),
        ("State",       "State"),
        ("Country",     "Country"),
        ("Pincode",     "Pincode"),
    ]

    def __init__(self, email: str, parent=None):
        super().__init__(parent)
        self._email = email
        self._drive_url = None

        self.setWindowTitle("User Profile")
        self.setFixedSize(420, 520)
        self.setModal(True)
        self.setWindowFlags(
            QtCore.Qt.Dialog | QtCore.Qt.WindowCloseButtonHint)
        self.setStyleSheet(_BASE_STYLE + f"""
            QDialog {{
                background: {_BG};
                border: 1px solid {_BORDER};
                border-radius: 12px;
            }}
            QFrame#profileCard {{
                background: {_SURFACE};
                border: 1px solid {_BORDER};
                border-radius: 8px;
            }}
            QLabel#fieldKey {{
                color: {_MUTED};
                font-size: 11px;
            }}
            QLabel#fieldVal {{
                color: {_TEXT};
                font-size: 13px;
                font-weight: bold;
            }}
        """)

        self._build()
        self._load_profile()

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QtWidgets.QHBoxLayout()
        avatar = QtWidgets.QLabel("👤")
        avatar.setStyleSheet("font-size: 28px;")
        hdr.addWidget(avatar)

        title_col = QtWidgets.QVBoxLayout()
        title_lbl = QtWidgets.QLabel("User Profile")
        title_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 15px; font-weight: bold;")
        email_lbl = QtWidgets.QLabel(self._email)
        email_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 11px;")
        title_col.addWidget(title_lbl)
        title_col.addWidget(email_lbl)
        hdr.addLayout(title_col)
        hdr.addStretch()
        root.addLayout(hdr)

        # ── Divider ───────────────────────────────────────────────────────────
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet(f"color: {_BORDER};")
        root.addWidget(line)

        # ── Profile card ──────────────────────────────────────────────────────
        card = QtWidgets.QFrame()
        card.setObjectName("profileCard")
        card_lay = QtWidgets.QGridLayout(card)
        card_lay.setContentsMargins(16, 12, 16, 12)
        card_lay.setVerticalSpacing(10)
        card_lay.setHorizontalSpacing(16)

        self._value_labels = {}
        for row, (key, label) in enumerate(self._FIELDS):
            k_lbl = QtWidgets.QLabel(label)
            k_lbl.setObjectName("fieldKey")
            v_lbl = QtWidgets.QLabel("—")
            v_lbl.setObjectName("fieldVal")
            v_lbl.setWordWrap(True)
            card_lay.addWidget(k_lbl, row, 0)
            card_lay.addWidget(v_lbl, row, 1)
            self._value_labels[key] = v_lbl

        root.addWidget(card)

        # ── Backup storage ────────────────────────────────────────────────────
        store = QtWidgets.QFrame()
        store.setObjectName("profileCard")
        store_lay = QtWidgets.QVBoxLayout(store)
        store_lay.setContentsMargins(16, 12, 16, 12)
        store_lay.setSpacing(6)

        head = QtWidgets.QHBoxLayout()
        st_title = QtWidgets.QLabel("Backup Storage")
        st_title.setStyleSheet(f"color:{_ACCENT}; font-size:12px; font-weight:bold;")
        head.addWidget(st_title)
        head.addStretch()
        self._store_amount = QtWidgets.QLabel("—")
        self._store_amount.setStyleSheet(f"color:{_TEXT}; font-size:12px; font-weight:bold;")
        head.addWidget(self._store_amount)
        store_lay.addLayout(head)

        self._store_bar = QtWidgets.QProgressBar()
        self._store_bar.setRange(0, 100)
        self._store_bar.setValue(0)
        self._store_bar.setTextVisible(False)
        self._store_bar.setFixedHeight(8)
        self._store_bar.setStyleSheet(f"""
            QProgressBar {{ background:{_BG}; border:1px solid {_BORDER};
                            border-radius:4px; }}
            QProgressBar::chunk {{ background:{_ACCENT}; border-radius:4px; }}
        """)
        store_lay.addWidget(self._store_bar)

        self._store_note = QtWidgets.QLabel("Checking…")
        self._store_note.setStyleSheet(f"color:{_MUTED}; font-size:10px;")
        store_lay.addWidget(self._store_note)

        root.addWidget(store)

        # ── Loading indicator ─────────────────────────────────────────────────
        self._status_lbl = QtWidgets.QLabel("Loading profile…")
        self._status_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 11px;")
        self._status_lbl.setAlignment(QtCore.Qt.AlignCenter)
        root.addWidget(self._status_lbl)

        root.addStretch()

        # ── Action buttons ────────────────────────────────────────────────────
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(10)

        self._drive_btn = QtWidgets.QPushButton("🗂  My Backups")
        self._drive_btn.setMinimumHeight(34)
        self._drive_btn.setEnabled(True)   # always enabled — fallback URL used if fetch not done yet
        self._drive_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_SURFACE};
                color: {_ACCENT};
                border: 1px solid {_ACCENT};
                border-radius: 5px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {_ACCENT};
                color: {_BG};
            }}
            QPushButton:disabled {{
                border-color: {_BORDER};
                color: {_MUTED};
            }}
        """)
        self._drive_btn.clicked.connect(self._open_drive)

        logout_btn = QtWidgets.QPushButton("⏻  Logout")
        logout_btn.setMinimumHeight(34)
        logout_btn.setObjectName("dangerBtn")
        logout_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_SURFACE};
                color: {_RED};
                border: 1px solid {_RED};
                border-radius: 5px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {_RED};
                color: white;
            }}
        """)
        logout_btn.clicked.connect(self._do_logout)

        btn_row.addWidget(self._drive_btn, 1)
        btn_row.addWidget(logout_btn, 1)
        root.addLayout(btn_row)

    # ── Data loading (runs in background thread) ──────────────────────────────
    _profile_ready = QtCore.pyqtSignal(object, object)   # (info_dict, drive_url)
    _quota_ready   = QtCore.pyqtSignal(object)          # backup quota, or None

    def _load_profile(self):
        """Fetch user info + Drive URL in a background thread."""
        import widgets.auth_backend as auth
        self._profile_ready.connect(self._on_profile_loaded)

        self._quota_ready.connect(self._on_quota_loaded)

        def worker():
            info = None
            session = fb.current_session()
            if session:
                try:
                    info = fb.fetch_profile(fb.ensure_fresh(session))
                except fb.AuthError:
                    info = None
            url = auth.get_user_drive_url(self._email)
            self._profile_ready.emit(info, url)

            # Separate call, and deliberately after the profile: the backup
            # service is a different endpoint and should not hold up the rest
            # of the dialog if it is slow or unreachable.
            quota = None
            if session:
                try:
                    quota = fb.backup_quota(session)
                except Exception:
                    quota = None
            self._quota_ready.emit(quota)

        threading.Thread(target=worker, daemon=True).start()

    @QtCore.pyqtSlot(object)
    def _on_quota_loaded(self, quota):
        if not quota:
            # Reset the bar too, or it keeps whatever it last showed and
            # implies a usage figure the app could not actually confirm.
            self._store_amount.setText("—")
            self._store_bar.setValue(0)
            self._store_note.setText("Could not reach the backup service.")
            return
        used = int(quota.get("used") or 0)
        allowed = int(quota.get("allowed") or 0) or 1
        pct = min(100, int(used * 100 / allowed))
        self._store_amount.setText(
            fb.human_bytes(used) + " / " + fb.human_bytes(allowed))
        self._store_bar.setValue(pct)
        # Amber past 80%, red past 95%, so a customer sees it filling up before
        # an upload is refused rather than after.
        colour = "#ef4444" if pct >= 95 else "#facc15" if pct >= 80 else _ACCENT
        self._store_bar.setStyleSheet(f"""
            QProgressBar {{ background:{_BG}; border:1px solid {_BORDER};
                            border-radius:4px; }}
            QProgressBar::chunk {{ background:{colour}; border-radius:4px; }}
        """)
        files = int(quota.get("files") or 0)
        last = quota.get("lastUploadAt") or ""
        bits = [str(files) + (" file" if files == 1 else " files")]
        if pct >= 95:
            bits.append("almost full, contact APEX to increase it")
        if last:
            bits.append("last updated " + str(last)[:16].replace("T", " "))
        self._store_note.setText("  ·  ".join(bits))

    @QtCore.pyqtSlot(object, object)
    def _on_profile_loaded(self, info, url):
        if info is None:
            self._status_lbl.setText("⚠  Could not load your details (offline?)")
            self._status_lbl.show()
        else:
            # Labels read as headings ("Institution") while Firestore stores
            # them lower-cased, so matching is case-insensitive.
            info_lower = {k.lower(): v for k, v in info.items()}
            filled = 0
            for key, lbl in self._value_labels.items():
                val = str(info.get(key) or info_lower.get(key.lower(), "")).strip()
                if key == "Email" and not val:
                    val = self._email
                lbl.setText(val if val else "—")
                if val:
                    filled += 1

            if filled <= 1:
                # Only the email resolved, so the account exists but its
                # details were never written. That is what happens when a
                # licence was activated through the re-entry screen, which
                # stores only the key. Saying so beats seven silent dashes.
                self._status_lbl.setText(
                    "Your details were not saved with this account. "
                    "Contact APEX and they can add them.")
                self._status_lbl.show()
            else:
                self._status_lbl.hide()

        if url:
            self._drive_url = url
            self._drive_btn.setEnabled(True)

    # ── Actions ───────────────────────────────────────────────────────────────
    def _open_drive(self):
        """
        Show what is in Firebase Storage.

        Was a link to a shared Google Drive folder, reachable only with a token
        that shipped inside every build. Now lists the signed-in customer's own
        files, through links the server mints on request and which expire in an
        hour.
        """
        _MyBackupsDialog(self).exec_()

    def _do_logout(self):
        reply = ThemedMessageBox.question(
            self, "Logout",
            "Are you sure you want to log out?",
            buttons=["Logout", "Cancel"],
            danger_buttons=["Logout"],
        )
        if reply == "Logout":
            # Log the logout event to ActiveUsers sheet (non-blocking)
            import threading as _t
            _t.Thread(target=fb.log_logout, daemon=True).start()
            self.logout_requested.emit()
            self.accept()


# Need threading import at module level for the worker thread
import threading
