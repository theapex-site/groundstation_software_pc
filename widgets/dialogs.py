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
import threading
import serial.tools.list_ports
import tempfile, os
import requests
from PyQt5 import QtWidgets, QtCore, QtGui
import ctypes, time
from config import (APEX_LOGO, PIXIE_LOGO, PIXIE_BUBBLE, DOWNLOAD_ANIMATION,
                    title, APP_VERSION)
import settings
import theme
import widgets.auth_backend as auth
import widgets.firebase_backend as fb

# ── Shared GCS palette ────────────────────────────────────────────────────────
# These used to be a second hardcoded copy of the palette, alongside the ones
# in config.py, ground_station.py and object_detection.py. They now alias
# theme.py so there is one place to change.
_BG       = theme.DEEP
_SURFACE  = theme.PANEL
_RAISED   = theme.RAISED
_BORDER   = theme.LINE
_ACCENT   = theme.TEAL
_TEXT     = theme.CREAM
_MUTED    = theme.SLATE

# Where "Need Help?" writes to. Same address as the support button in the
# licence email.
SUPPORT_EMAIL = "support@theapex.site"
_GREEN    = theme.OK
_YELLOW   = theme.WARN
_RED      = theme.ALARM


def _icon_btn(text: str, icon_name: str,
              color: str = _MUTED, size: int = 16) -> QtWidgets.QPushButton:
    """A button labelled with a line icon instead of a colour emoji.

    An emoji in the label is drawn by the system emoji font, in its own
    colours and at its own weight, so a row of buttons carried several
    unrelated hues. A QIcon is tinted from the theme tokens.
    """
    btn = QtWidgets.QPushButton(text)
    btn.setIcon(theme.icon(icon_name, color, size))
    btn.setIconSize(QtCore.QSize(size, size))
    return btn

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

def _base_style() -> str:
    """
    Dialog chrome, on top of the application sheet.

    Built per call rather than at import time because theme.init() resolves
    the font families once a QApplication exists, and this module is imported
    before that happens.
    """
    return f"""
    QDialog, QFrame {{ background-color: {_BG}; }}
    QLabel {{
        color: {_TEXT};
        font-family: "{theme.BODY}";
        font-size: {theme.FS_BODY}px;
    }}
    QPushButton {{ min-width: 92px; text-align: center; }}
    QLineEdit {{ font-size: {theme.FS_VALUE}px; }}
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
        self.setStyleSheet(_base_style())

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
                background:{_ACCENT}; color:{_BG}; border:none; border-radius:{theme.R_CTRL}px;
                padding:10px; font-size:13px; font-weight:bold;
            }}
            QPushButton:hover {{ background:{theme.GRADIENT_COOL}; }}
            QPushButton:disabled {{ background:{_BORDER}; color:{theme.DISABLED}; }}
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
        fb.set_current_profile({})
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
    # The resume attempt (if there was one) didn't produce a session —
    # offline, expired, or no saved token to begin with. LoginWindow uses
    # this to come back off the splash page to this form.
    resume_failed   = QtCore.pyqtSignal()
    # Internal: the background resume thread's outcome, handed back across
    # threads — Qt auto-queues a signal onto its receiver's thread, which is
    # the whole point of routing the result through one instead of just
    # acting on it from the worker thread directly.
    _resume_done = QtCore.pyqtSignal(dict)

    def __init__(self, session: dict):
        """session dict is shared with LoginWindow for Stay-Signed-In."""
        super().__init__()
        self._session = session
        self._build()
        self._resume_done.connect(self._on_resume_done)
        # Resume after the window has painted, so a slow network shows the
        # login form first rather than a blank panel.
        QtCore.QTimer.singleShot(0, self._start_resume)

    def _start_resume(self):
        token = self._session.get("refreshToken")
        if not token:
            return
        self._resume_label.show()
        threading.Thread(target=self._resume_worker, daemon=True).start()

    def _resume_worker(self):
        """
        Sign back in from the saved refresh token, if there is one.

        Runs off the GUI thread: every fb.* call here is a blocking network
        request to Firebase, and doing this directly on the GUI thread (as
        it used to) froze the whole window for however long that took —
        which a compositor or screen-share renders as solid white rather
        than the last real frame, since a blocked event loop can't repaint
        on request.

        Also where this computer discovers it has been signed out: if the
        licence's deviceId has moved to another machine, the saved session
        is dropped and the user is told why. That is the whole of the
        one-computer-at-a-time rule on this side — no polling, so nothing
        eats into Firestore's free daily write quota.
        """
        token = self._session.get("refreshToken")
        try:
            session = fb.refresh_session({
                "refreshToken": token,
                "email": self._session.get("email", ""),
                "uid": "",
            })
            info = fb.account_info(session["idToken"])
            if not info.get("emailVerified"):
                self._resume_done.emit({"kind": "none"})
                return
            session["uid"] = info["uid"]
            session["email"] = info["email"]

            profile = fb.fetch_profile(session)
            licence = (profile or {}).get("licenceKey", "")
            if not licence:
                self._resume_done.emit({"kind": "none"})
                return

            ok, msg = fb.device_still_ours(session, licence)
            if not ok:
                # A licence that was regenerated or cancelled is recoverable:
                # APEX issues a new key and the customer pastes it in. Being
                # signed in on another computer is not, so that still drops
                # the saved session and sends them back to the sign-in form.
                if "another computer" in msg.lower():
                    self._resume_done.emit({"kind": "signed_out", "msg": msg})
                else:
                    self._resume_done.emit({
                        "kind": "needs_reauth", "email": session["email"],
                        "session": session, "msg": msg,
                    })
                return

            fb.check_entitlement(session, licence)
        except fb.AuthError:
            self._resume_done.emit({"kind": "none"})
            return      # offline or expired — fall back to the normal form
        fb.set_current_session(session)
        fb.set_current_profile(profile)
        self._resume_done.emit({"kind": "success", "email": session["email"]})

    def _on_resume_done(self, result: dict):
        """Back on the GUI thread — safe to touch widgets and self._session
        here, unlike in _resume_worker()."""
        self._resume_label.hide()
        kind = result["kind"]
        if kind == "none":
            self.resume_failed.emit()
            return
        if kind == "signed_out":
            _clear_session()
            self._session.clear()
            ThemedMessageBox.information(self, "Signed Out", result["msg"])
            self.resume_failed.emit()
        elif kind == "needs_reauth":
            self.needs_reauth.emit(result["email"], result["session"], result["msg"])
        elif kind == "success":
            self.login_success.emit(result["email"])

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

        # Shown while _try_resume's background thread is still talking to
        # Firebase, so a saved sign-in taking a moment reads as "working",
        # not as the form just sitting there (or, before this, the window
        # itself going unresponsive — see _try_resume's own comment).
        self._resume_label = QtWidgets.QLabel("Checking saved sign-in…")
        self._resume_label.setStyleSheet(f"color:{_ACCENT}; font-size:11px;")
        self._resume_label.hide()
        lay.addWidget(self._resume_label)

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
        # now resumes from the saved refresh token instead (see _resume_worker).

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
                border: none; border-radius:{theme.R_CTRL}px;
                font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {theme.GRADIENT_COOL}; }}
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
            ThemedMessageBox.warning(self, "Missing fields",
                                          "Please enter email and password.")
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            session = fb.sign_in(email, pwd)

            if not fb.account_info(session["idToken"]).get("emailVerified"):
                QtWidgets.QApplication.restoreOverrideCursor()
                ThemedMessageBox.warning(
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
            ThemedMessageBox.critical(self, "Login Failed", str(exc))
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
        fb.set_current_profile(profile)
        self.login_success.emit(email)

    def _do_forgot(self):
        email = self.email_inp.text().strip()
        if not email:
            ThemedMessageBox.warning(
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
                ThemedMessageBox.critical(self, "Error", str(exc))
                return
        ThemedMessageBox.information(
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
        self.setStyleSheet(_base_style() + f"""
            QDialog {{
                background: {_BG};
                border: 1px solid {_BORDER};
                border-radius:{theme.R_CARD}px;
            }}
            QTextEdit {{
                background: {_SURFACE};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius:{theme.R_CTRL}px;
                padding: 10px;
                font-size: 12px;
                font-family: Consolas;
            }}
            QScrollBar:vertical {{
                background: {_BG};
                width: 8px;
                border-radius:{theme.R_SM}px;
            }}
            QScrollBar::handle:vertical {{
                background: {_BORDER};
                border-radius:{theme.R_SM}px;
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

        icon_lbl = QtWidgets.QLabel()
        icon_lbl.setPixmap(theme.icon("clipboard", theme.SLATE, 18).pixmap(18, 18))
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
                border: none; border-radius:{theme.R_CTRL}px;
                font-size: 12px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {theme.GRADIENT_COOL}; }}
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
                border: 1.5px solid #1a1a2e;
                border-radius:{theme.R_CTRL}px;
                padding: 6px 10px;
                font-size: 12px;
                font-family: Consolas;
            }}
            QLineEdit:focus {{
                border: 2px solid #2dd4bf;
                background: #0d0d18;
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
                border: 1.5px solid #1a1a2e;
                border-radius:{theme.R_CTRL}px;
                padding: 6px 10px;
                font-size: 14px;
                font-family: Consolas;
            }}
            QComboBox:focus {{
                border: 2px solid #2dd4bf;
                background: #0d0d18;
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
            QPushButton:hover {{ color: {_ACCENT}; }}
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
                border: none; border-radius:{theme.R_CTRL}px;
                font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {theme.GRADIENT_COOL}; }}
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
            ThemedMessageBox.warning(
                self, "Missing Fields", "Please select your state.")
            return

        if "" in (name, email, pwd, repwd, inst, city, cntry, pin):
            ThemedMessageBox.warning(
                self, "Missing Fields", "Please fill in all fields.")
            return

        if pwd != repwd:
            ThemedMessageBox.warning(
                self, "Password Mismatch", "Passwords do not match.")
            return

        licence = fb.normalise_key(self.licence_inp.text())
        if not licence:
            ThemedMessageBox.warning(
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
                ThemedMessageBox.critical(
                    self, "Licence Does Not Match",
                    "That licence key does not match this email address.\n\n"
                    "Use the same address your CubeSat order was placed with.")
                return
            if licence_doc.get("status") != "active":
                QtWidgets.QApplication.restoreOverrideCursor()
                ThemedMessageBox.critical(
                    self, "Licence Cancelled",
                    "This licence has been cancelled.\n\n"
                    "Please contact APEX to have it reactivated.")
                return
            if licence_doc.get("uid"):
                QtWidgets.QApplication.restoreOverrideCursor()
                ThemedMessageBox.critical(
                    self, "Licence Already Used",
                    "This licence has already been activated on another "
                    "account.\n\nContact APEX if you need it moved.")
                return

            fb.send_verification_email(session["idToken"])
        except fb.AuthError as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            ThemedMessageBox.critical(self, "Sign Up Failed", str(exc))
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
                ThemedMessageBox.critical(self, "Licence Error", msg)
                return
            fb.save_profile(session, {
                "name": name, "institution": inst, "city": city,
                "state": state, "country": cntry, "pincode": pin,
                "licenceKey": licence,
            })
        except fb.AuthError as exc:
            ThemedMessageBox.critical(self, "Sign Up Failed", str(exc))
            return

        # Nothing to set up for backups any more: Storage folders come into
        # being with the first upload, so there is no folder-creation call to
        # wait on here.
        ThemedMessageBox.information(
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

        icon = QtWidgets.QLabel()
        icon.setAlignment(QtCore.Qt.AlignCenter)
        icon.setPixmap(theme.icon("antenna", theme.TEAL, 40).pixmap(40, 40))
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

        # Buttons
        btn_row = QtWidgets.QHBoxLayout()
        refresh_btn = _icon_btn("Refresh", "refresh")
        connect_btn = _icon_btn("Connect", "plug", _BG)
        connect_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: {_BG};
                border: 1px solid {_ACCENT}; padding: 6px 18px;
                border-radius:{theme.R_CTRL}px; font-size: 12px;
                font-weight: bold; min-width: 80px;
            }}
            QPushButton:hover {{ background: {theme.GRADIENT_COOL}; }}
        """)
        refresh_btn.clicked.connect(self._refresh)
        connect_btn.clicked.connect(self._connect)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(connect_btn)
        lay.addLayout(btn_row)

        # Shown only when there is no port and the USB driver is missing, which
        # is the one case where a customer is stuck with nothing to click.
        self._driver_hint = QtWidgets.QLabel(
            "No port? The CubeSat needs the Silicon Labs USB driver. "
            "It is included with APEX GCS.")
        self._driver_hint.setAlignment(QtCore.Qt.AlignCenter)
        self._driver_hint.setWordWrap(True)
        self._driver_hint.setStyleSheet(f"color:{_MUTED}; font-size:10px;")
        self._driver_hint.hide()
        lay.addWidget(self._driver_hint)

        self._driver_btn = _icon_btn("Install USB Driver", "download")
        self._driver_btn.setMinimumHeight(30)
        self._driver_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_SURFACE}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius:{theme.R_CTRL}px;
                font-size: 11px;
            }}
            QPushButton:hover {{ border-color: {_ACCENT}; color: {_ACCENT}; }}
        """)
        self._driver_btn.clicked.connect(self._install_driver)
        self._driver_btn.hide()
        lay.addWidget(self._driver_btn)

        lay.addStretch(2)

        # Last, because it decides whether to show the two widgets above.
        self._refresh()

    def _refresh(self):
        self.combo.clear()
        ports = serial.tools.list_ports.comports()
        for p in ports:
            self.combo.addItem(f"{p.device}  —  {p.description}")
        if not ports:
            self.combo.addItem("No ports found")
        self._update_driver_offer(bool(ports))

    def _update_driver_offer(self, have_ports: bool):
        """Offer the bundled driver only when it would actually help."""
        try:
            import widgets.driver_install as drv
            offer = (not have_ports) and drv.is_available() and not drv.is_installed()
        except Exception:
            offer = False
        self._driver_hint.setVisible(offer)
        self._driver_btn.setVisible(offer)

    def _install_driver(self):
        import widgets.driver_install as drv

        if ThemedMessageBox.question(
                self, "Install USB Driver",
                "Install the " + drv.DEVICE_NAME + " driver?",
                "Windows will ask for administrator permission. The driver is "
                "supplied by Silicon Labs under their licence agreement, "
                "included with APEX GCS.",
                buttons=["Install", "Cancel"]) != "Install":
            return

        self._driver_btn.setEnabled(False)
        self._driver_btn.setText("Installing…")
        QtWidgets.QApplication.processEvents()
        try:
            ok, message = drv.install()
        finally:
            self._driver_btn.setEnabled(True)
            self._driver_btn.setText("Install USB Driver")

        if ok:
            ThemedMessageBox.information(self, "Install USB Driver", message)
            self._refresh()
        else:
            ThemedMessageBox.warning(self, "Install USB Driver", message)

    def _connect(self):
        port = self.combo.currentText().split("  —  ")[0].strip()
        self.port_selected.emit(port)


class _SplashPage(QtWidgets.QWidget):
    """Shown in place of the sign-in form while a saved "stay signed in"
    session is being resumed.

    Someone who ticked that box already proved who they are last time —
    making them stare at the same email/password form again for the second
    or two the resume check takes (even off the GUI thread, Firebase still
    takes a moment to answer) read as not actually remembering them. This
    is what they see instead, until _resume_worker settles one way or the
    other.
    """

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setAlignment(QtCore.Qt.AlignCenter)
        lay.setSpacing(16)

        logo = QtWidgets.QLabel()
        logo.setAlignment(QtCore.Qt.AlignCenter)
        pix = QtGui.QPixmap(PIXIE_LOGO)
        if not pix.isNull():
            logo.setPixmap(pix.scaled(
                120, 120, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        lay.addWidget(logo)

        msg = QtWidgets.QLabel("Signing you in…")
        msg.setAlignment(QtCore.Qt.AlignCenter)
        msg.setStyleSheet(f"color:{_MUTED}; font-size:12px; letter-spacing:1px;")
        lay.addWidget(msg)


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
        self.setStyleSheet(_base_style() + f"""
            QDialog {{
                background: {_BG};
                border: 1px solid {_BORDER};
                border-radius:{theme.R_CARD}px;
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
        # Both the starfield and the scrim take the panel's real height. They
        # were pinned at 580 against a 620px window, which left a 40px band of
        # flat black under the logo with a visible seam across it — on the
        # first screen of the product.
        panel_w, panel_h = 340, self.height()

        left = QtWidgets.QLabel()
        left.setFixedWidth(panel_w)
        left.setScaledContents(False)
        left.setAlignment(QtCore.Qt.AlignCenter)

        # The same starfield the main window and the website use, drawn at the
        # panel's exact size. Was images/BG1.jpg scaled and centre-cropped,
        # which never matched the site.
        from widgets.space_bg import backdrop
        left.setPixmap(backdrop(panel_w, panel_h))

        # Overlay container (title top, logo below it)
        left_overlay = QtWidgets.QWidget(left)
        left_overlay.setGeometry(0, 0, panel_w, panel_h)
        # Light, because the starfield underneath is the point now.
        left_overlay.setStyleSheet("background: rgba(0,0,0,0.35);")
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
        self._splash_page = _SplashPage()

        self._stack.addWidget(self._login_page)    # index 0
        self._stack.addWidget(self._signup_page)   # index 1
        self._stack.addWidget(self._com_page)      # index 2
        self._stack.addWidget(self._reauth_page)   # index 3
        self._stack.addWidget(self._splash_page)   # index 4

        # A saved "stay signed in" session already proved who this is last
        # time — showing the blank sign-in form while _resume_worker
        # re-checks it (then snapping straight to the COM port page a
        # moment later) read as the form flashing by half-built, not as
        # "remembering" the operator. The splash page stands in for it
        # until the resume attempt actually resolves, one way or another.
        if self._session.get("refreshToken"):
            self._stack.setCurrentIndex(4)

        # Wire signals
        self._login_page.login_success.connect(self._on_login_success)
        self._login_page.go_signup.connect(lambda: self._stack.setCurrentIndex(1))
        self._login_page.needs_reauth.connect(self._on_needs_reauth)
        self._login_page.resume_failed.connect(lambda: self._stack.setCurrentIndex(0))

        self._reauth_page.reauth_success.connect(self._on_login_success)
        self._reauth_page.go_login.connect(lambda: self._stack.setCurrentIndex(0))

        self._signup_page.go_login.connect(lambda: self._stack.setCurrentIndex(0))
        self._signup_page.signup_done.connect(lambda: self._stack.setCurrentIndex(0))

        self._com_page.port_selected.connect(self._on_port_selected)

        root_lay.addWidget(self._stack)

        # Resuming a saved session is _LoginPage._resume_worker's job: it only
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
            ThemedMessageBox.warning(
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
        self.setStyleSheet(_base_style() + f"""
            QDialog {{
                background-color: {_BG};
                border: 1px solid {_BORDER};
                border-radius:{theme.R_CARD}px;
            }}
        """)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(16)

        # ── Header ────────────────────────────────────────────────────────────
        icon_lbl = QtWidgets.QLabel()
        icon_lbl.setAlignment(QtCore.Qt.AlignCenter)
        icon_lbl.setPixmap(theme.icon("antenna", theme.TEAL, 34).pixmap(34, 34))
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

        refresh_btn = _icon_btn("Refresh", "refresh")
        connect_btn = _icon_btn("Connect", "plug", _BG)
        connect_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT};
                color: {_BG};
                border: 1px solid {_ACCENT};
                padding: 6px 18px;
                border-radius:{theme.R_CTRL}px;
                font-size: 12px;
                font-weight: bold;
                min-width: 80px;
            }}
            QPushButton:hover {{
                background: {theme.GRADIENT_COOL};
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

    _EDGE = {"info": _ACCENT, "success": _GREEN, "warn": _YELLOW, "error": _RED}

    # Live toasts per parent window, oldest first.
    _stacks: dict = {}

    def __init__(self, parent: QtWidgets.QWidget, text: str, kind: str = "info"):
        super().__init__(parent)
        edge = self._EDGE.get(kind, _ACCENT)

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {_RAISED};
                border: 1px solid {edge};
                border-radius:{theme.R_CARD}px;
            }}
            QLabel {{
                color: {_TEXT};
                font-size: {theme.FS_BODY}px;
                padding: 11px 15px;
            }}
        """)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        dot = QtWidgets.QLabel("●")
        dot.setStyleSheet(f"color: {edge}; padding-left: 14px; padding-right: 2px;")
        layout.addWidget(dot)
        layout.addWidget(QtWidgets.QLabel(text))

        self.adjustSize()
        Toast._stacks.setdefault(id(parent), []).append(self)
        self._restack(parent)

        self.raise_()
        self.show()
        QtCore.QTimer.singleShot(2800, self.close)

    @staticmethod
    def _restack(parent):
        """Lay the live toasts out upward from the bottom-right corner."""
        y = parent.rect().height() - 20
        for toast in reversed(Toast._stacks.get(id(parent), [])):
            y -= toast.height()
            toast.move(parent.rect().width() - toast.width() - 20, y)
            y -= 8

    def closeEvent(self, event):
        stack = Toast._stacks.get(id(self.parent()), [])
        if self in stack:
            stack.remove(self)
            self._restack(self.parent())
        self.deleteLater()
        super().closeEvent(event)


class PixieBubble(QtWidgets.QFrame):
    """A comic thought-cloud over Pixie's head — what command confirmations
    and similar chatter become while she's actually on screen, instead of
    the bottom-right Toast everything still falls back to the moment she's
    faded out mid-downlink.

    The art itself is the user's reference thought-bubble image, re-cut:
    its black ink and white fill are isolated with a flood fill from the
    image border (stopped at the ink), then recoloured — ink to the
    message's edge colour, fill to the panel's own dark colour — rather
    than redrawn as a shape of our own. See tools/make_pixie_bubble.py.
    """

    # Same semantic colours as Toast for an actual alert, but a plain
    # white outline for an ordinary "info" message — the reference image
    # inverted (white ink on the dark fill), not the app's teal accent.
    _EDGE = dict(Toast._EDGE, info="#ffffff")
    # Loaded lazily (first use), not at class-body eval time: this module
    # is imported before QApplication exists (main.py builds LoginWindow's
    # import before the app), and QPixmap needs one alive to load a file.
    _PIX = None

    # Where the art's lower trailer dot and its text-safe interior sit,
    # as fractions of the asset's own width/height — measured once off
    # the source image, not eyeballed per redraw.
    _ANCHOR_FX, _ANCHOR_FY = 0.875, 0.981
    _TEXT_FRAC = (0.15, 0.16, 0.85, 0.56)   # left, top, right, bottom

    _DISPLAY_W = 240

    # One live bubble per anchor, so a fast second message doesn't stack
    # on top of the first — it replaces it instead.
    _live: dict = {}

    def __init__(self, anchor: QtWidgets.QWidget, text: str, kind: str = "info"):
        host = anchor.window()
        super().__init__(host)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

        prev = PixieBubble._live.get(id(anchor))
        if prev is not None:
            prev.close()

        self._anchor = anchor
        edge = self._EDGE.get(kind, _ACCENT)

        w = self._DISPLAY_W
        h = round(w * self._base_pixmap().height() / self._base_pixmap().width())
        self._pix = self._tinted(edge, w, h)
        self.setFixedSize(w, h)

        lf, tf, rf, bf = self._TEXT_FRAC
        text_rect = QtCore.QRect(
            round(lf * w), round(tf * h), round((rf - lf) * w), round((bf - tf) * h))

        label = QtWidgets.QLabel(text, self)
        label.setWordWrap(True)
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setStyleSheet(
            f"color: {_TEXT}; font-size: {theme.FS_BODY}px; background: transparent;")
        label.setGeometry(text_rect)

        self._fx = QtWidgets.QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        self._fade = QtCore.QPropertyAnimation(self._fx, b"opacity", self)
        self._fade.setEasingCurve(QtCore.QEasingCurve.InOutCubic)

        PixieBubble._live[id(anchor)] = self
        self._reposition()
        self.raise_()
        self.show()

        self._fade.setDuration(220)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        QtCore.QTimer.singleShot(2300, self._fade_out)

    def _fade_out(self):
        try:
            self._fade.finished.disconnect()
        except TypeError:
            pass
        self._fade.finished.connect(self.close)
        self._fade.stop()
        self._fade.setDuration(320)
        self._fade.setStartValue(self._fx.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    @classmethod
    def _base_pixmap(cls) -> QtGui.QPixmap:
        if cls._PIX is None:
            cls._PIX = QtGui.QPixmap(PIXIE_BUBBLE)
        return cls._PIX

    @classmethod
    def _tinted(cls, edge: str, w: int, h: int) -> QtGui.QPixmap:
        """The art, scaled to display size and with its ink swapped to
        this message's edge colour — both cached per (colour, size), and
        scaled down before the per-pixel swap so the loop runs over a few
        thousand pixels, not the full-resolution source."""
        cache = cls.__dict__.get("_tint_cache")
        if cache is None:
            cache = {}
            cls._tint_cache = cache
        key = (edge, w, h)
        hit = cache.get(key)
        if hit is not None:
            return hit

        ratio = QtWidgets.QApplication.instance().devicePixelRatio() \
            if QtWidgets.QApplication.instance() else 1.0
        scaled = cls._base_pixmap().scaled(
            int(w * ratio), int(h * ratio),
            QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)

        img = scaled.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
        want = QtGui.QColor(_ACCENT)
        swap = QtGui.QColor(edge)
        if swap != want:
            for y in range(img.height()):
                for x in range(img.width()):
                    px = img.pixelColor(x, y)
                    if px.alpha() and abs(px.red() - want.red()) < 40 \
                            and abs(px.green() - want.green()) < 40 \
                            and abs(px.blue() - want.blue()) < 40:
                        px.setRed(swap.red())
                        px.setGreen(swap.green())
                        px.setBlue(swap.blue())
                        img.setPixelColor(x, y, px)
        result = QtGui.QPixmap.fromImage(img)
        result.setDevicePixelRatio(ratio)
        cache[key] = result
        return result

    def _reposition(self):
        host = self.parentWidget()
        top_left = self._anchor.mapTo(host, QtCore.QPoint(0, 0))
        acx = top_left.x() + self._anchor.width() / 2
        ax = self._ANCHOR_FX * self.width()
        ay = self._ANCHOR_FY * self.height()
        x = max(8, min(acx - ax, host.width() - self.width() - 8))
        y = max(8, top_left.y() - ay - 4)
        self.move(int(x), int(y))

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        p.drawPixmap(0, 0, self._pix)
        p.end()

    def closeEvent(self, event):
        if PixieBubble._live.get(id(self._anchor)) is self:
            del PixieBubble._live[id(self._anchor)]
        self.deleteLater()
        super().closeEvent(event)


# ══════════════════════════════════════════════════════════════════════════════
#  Self-update: download + silent install, in-app rather than a browser tab
# ══════════════════════════════════════════════════════════════════════════════
class _DownloadThread(QtCore.QThread):
    """Streams the installer to a temp file, reporting progress as it goes.

    A plain QThread rather than the check-for-updates pattern's bare
    threading.Thread + QMetaObject.invokeMethod: this needs a steady stream
    of progress ticks, not one callback at the end, so Qt signals are the
    simpler fit.
    """
    progress = QtCore.pyqtSignal(int, int)   # bytes_done, bytes_total (0 if unknown)
    finished_ok = QtCore.pyqtSignal(str)     # local file path
    failed = QtCore.pyqtSignal(str)          # error message

    def __init__(self, url: str, dest_path: str, parent=None):
        super().__init__(parent)
        self._url = url
        self._dest = dest_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            resp = requests.get(self._url, stream=True, timeout=30)
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            with open(self._dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    if self._cancelled:
                        return
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    self.progress.emit(done, total)
            if self._cancelled:
                return
            self.finished_ok.emit(self._dest)
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))


class _RoundedMovieLabel(QtWidgets.QLabel):
    """A QLabel driven by a QMovie, clipped to a rounded rect.

    QSS border-radius only clips a QLabel's background/border, never a
    pixmap drawn inside it (via setMovie or setPixmap) — the frame would
    still paint square. Each frame is repainted here through a rounded
    QPainterPath clip instead.
    """

    def __init__(self, radius: int, parent=None):
        super().__init__(parent)
        self._radius = radius

    def paintEvent(self, event):
        pix = self.pixmap()
        if pix is None or pix.isNull():
            super().paintEvent(event)
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(self.rect()), self._radius, self._radius)
        p.setClipPath(path)
        p.drawPixmap(self.rect(), pix)
        p.end()


class UpdateDownloadDialog(QtWidgets.QDialog):
    """Progress dialog for the in-app updater.

    Downloads the installer with a cancellable progress bar, then swaps in
    a Restart Now button once it lands — Cancel stays available even after
    the download finishes, in case the operator changes their mind, and
    always deletes whatever was fetched rather than leaving a stray partial
    (or full) installer behind in temp.
    """

    # The source clip is 1280x720 (16:9); this is its width inside the
    # dialog, with height derived to keep that ratio.
    _VIDEO_W = 384

    def __init__(self, parent, url: str, version: str):
        super().__init__(parent)
        self.setWindowTitle("Downloading Update")
        self.setModal(True)
        self.setFixedWidth(440)
        self.setStyleSheet(_base_style() + f"""
            QDialog {{
                background-color: {_BG};
                border: 1px solid {_BORDER};
                border-radius: {theme.R_CARD}px;
            }}
        """)

        self._dest = os.path.join(
            tempfile.gettempdir(), f"APEX_GCS_Setup_{version}.exe")
        self._done_path = None

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)

        # Pixie, looping silently for as long as the dialog is open — the
        # download itself has no notion of visual progress worth animating
        # frame-by-frame, so this just keeps the wait from feeling dead.
        #
        # QMovie/GIF rather than QMediaPlayer/mp4: the Qt5 multimedia
        # backend's Windows video decoder depends on whatever DirectShow
        # filters happen to be registered on the machine, and failed to
        # open an ordinary H.264 mp4 outright on this one (ResourceError,
        # no codec available) — not something to risk on a customer's PC
        # with unknown codecs installed. GIF decodes through Qt's own
        # built-in image plugin, no OS codec dependency at all.
        video_h = round(self._VIDEO_W * 9 / 16)
        self._video = _RoundedMovieLabel(theme.R_CARD)
        self._video.setFixedSize(self._VIDEO_W, video_h)
        root.addWidget(self._video, alignment=QtCore.Qt.AlignHCenter)

        self._movie = QtGui.QMovie(DOWNLOAD_ANIMATION)
        self._movie.setScaledSize(QtCore.QSize(self._VIDEO_W, video_h))
        self._video.setMovie(self._movie)
        self._movie.start()

        title_lbl = QtWidgets.QLabel(f"Downloading APEX GCS v{version}…")
        title_lbl.setStyleSheet(
            f"color: {theme.CREAM}; font-size: {theme.FS_BODY}px; font-weight: 600;")
        root.addWidget(title_lbl)

        self._bar = QtWidgets.QProgressBar()
        self._bar.setRange(0, 0)   # indeterminate until Content-Length is known
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(10)
        root.addWidget(self._bar)

        self._status = QtWidgets.QLabel("Starting download…")
        self._status.setStyleSheet(f"color: {theme.SLATE}; font-size: {theme.FS_LABEL}px;")
        root.addWidget(self._status)

        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        self._cancel_btn = QtWidgets.QPushButton("Cancel")
        self._cancel_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._cancel_btn.clicked.connect(self._on_cancel)
        row.addWidget(self._cancel_btn)

        self._restart_btn = QtWidgets.QPushButton("Restart Now")
        self._restart_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._restart_btn.clicked.connect(self._on_restart)
        # Not objectName("primaryBtn") + the app stylesheet: _base_style()
        # above sets a local `QPushButton {...}` rule, and once a widget has
        # its own stylesheet, Qt resolves QPushButton styling against that
        # local rule alone rather than merging it with the app-level
        # `#primaryBtn` one — the id selector's gradient silently never
        # applied. Reproducing it as one self-contained inline sheet here
        # sidesteps the cascade entirely.
        self._restart_btn.setStyleSheet(f"""
            QPushButton {{
                background: {theme.GRADIENT};
                color: {theme.DEEP};
                border: none;
                font-family: "{theme.DISPLAY}";
                font-weight: 700;
                padding: 11px 20px;
            }}
            QPushButton:hover {{ background: {theme.GRADIENT_COOL}; }}
            QPushButton:disabled {{ background: {theme.LINE}; color: {theme.DISABLED}; }}
        """)
        # Disabled rather than hidden: toggling a layout item's visibility
        # after the row has already been laid out left it reserving no
        # space for Restart Now even once .show() was called on it —
        # staying in the layout throughout and just flipping enabled state
        # sidesteps that.
        self._restart_btn.setEnabled(False)
        row.addWidget(self._restart_btn)
        root.addLayout(row)

        self._thread = _DownloadThread(url, self._dest, self)
        self._thread.progress.connect(self._on_progress)
        self._thread.finished_ok.connect(self._on_finished)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def closeEvent(self, event):
        self._movie.stop()
        super().closeEvent(event)

    def _on_progress(self, done: int, total: int):
        if total:
            self._bar.setRange(0, total)
            self._bar.setValue(done)
            self._status.setText(f"{done / 1_048_576:.1f} / {total / 1_048_576:.1f} MB")
        else:
            self._status.setText(f"{done / 1_048_576:.1f} MB")

    def _on_finished(self, path: str):
        self._done_path = path
        self._bar.setRange(0, 1)
        self._bar.setValue(1)
        self._status.setText("Download complete — ready to install.")
        self._restart_btn.setEnabled(True)

    def _on_failed(self, message: str):
        self._status.setText(f"Download failed: {message}")
        self._cancel_btn.setText("Close")

    def _delete_download(self):
        try:
            if os.path.exists(self._dest):
                os.remove(self._dest)
        except OSError:
            pass

    def _on_cancel(self):
        self._thread.cancel()
        self._thread.wait(3000)
        self._delete_download()
        self.reject()

    def _on_restart(self):
        if not self._done_path or not os.path.exists(self._done_path):
            return
        # ShellExecuteW rather than os.startfile(): the installer needs the
        # /VERYSILENT flags passed as parameters, which startfile has no way
        # to carry. The "runas" verb is what actually raises the UAC prompt —
        # the installer's embedded admin manifest alone doesn't trigger it
        # from a plain CreateProcess.
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", self._done_path,
            "/VERYSILENT /NORESTART /SUPPRESSMSGBOXES", None, 1)
        if result <= 32:
            # <= 32 is every ShellExecute failure code, including 5 (the
            # user clicked "No" on the UAC prompt) — nothing was launched.
            ThemedMessageBox.critical(
                self, "Could Not Start Update",
                "The installer could not be launched.",
                informative="The update was not approved or could not start.")
            return

        # The installer's own [Run] step relaunches APEX GCS once it's done,
        # so this process just needs to get out of the way — anything else
        # open would hold its files locked against being overwritten.
        parent = self.parent()
        if parent is not None and hasattr(parent, "_prepare_for_restart"):
            parent._prepare_for_restart()
        self.accept()
        QtCore.QTimer.singleShot(200, lambda: os._exit(0))


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
        "information": ("info",     _ACCENT),
        "warning":     ("warning",  _YELLOW),
        "critical":    ("cross",    _RED),
        "question":    ("question", _ACCENT),
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
        # Word wrap on the labels below only takes effect once something
        # constrains the width. Without a maximum, a long informative string —
        # a full save path, a stack-trace message — produced one unbroken line
        # and a dialog wider than the screen.
        self.setMaximumWidth(580)
        self.setStyleSheet(_base_style() + f"""
            QDialog {{
                background-color: {_BG};
                border: 1px solid {_BORDER};
                border-radius:{theme.R_CARD}px;
            }}
        """)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)

        # ── Icon + title row ──────────────────────────────────────────────────
        icon_name, icon_color = self._ICONS.get(kind, ("info", _ACCENT))
        hdr = QtWidgets.QHBoxLayout()
        icon_lbl = QtWidgets.QLabel()
        icon_lbl.setPixmap(theme.icon(icon_name, icon_color, 26).pixmap(26, 26))
        icon_lbl.setStyleSheet("margin-right: 10px;")
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
        msg_lbl.setStyleSheet(f"color: {_TEXT}; font-size: {theme.FS_BODY}px;")
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
#  UserProfileDialog   <- opened by the gear button in the application bar
# ══════════════════════════════════════════════════════════════════════════════
class _SortItem(QtWidgets.QTableWidgetItem):
    """A cell that sorts on a key rather than on its displayed text.

    The table sorted lexically, so "9.4 KB" came after "10.1 KB" and a date
    sorted by its rendering. The key is the real value; the text stays
    human-readable.
    """

    def __init__(self, text: str, key):
        super().__init__(text)
        self._key = key

    def __lt__(self, other):
        if isinstance(other, _SortItem):
            return self._key < other._key
        return super().__lt__(other)


class _MyBackupsDialog(QtWidgets.QDialog):
    """
    What is actually in the customer's Firebase Storage backup.

    Replaces the old "Open Backup" link to a shared Google Drive folder. That
    folder lived in one APEX-owned Drive account, reachable with a token that
    shipped inside every build; this reads only the signed-in customer's own
    files, through links the server mints on request and which expire in an
    hour.

    Every row carries a checkbox, ticked on open, so "Download Selected" and
    "Delete Selected" act on the whole backup by default and the customer
    unticks what they want to leave alone.
    """

    _loaded   = QtCore.pyqtSignal(object, object)   # (files, error)
    _finished = QtCore.pyqtSignal(object, object)   # (message, error)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("APEX GCS — My Backups")
        self.setMinimumSize(720, 460)
        self.setModal(True)
        self.setStyleSheet(_base_style())
        self._files = []
        self._busy = False
        self._build()
        self._loaded.connect(self._on_loaded)
        self._finished.connect(self._on_finished)
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

        self._all_cb = QtWidgets.QCheckBox("Select all")
        self._all_cb.setChecked(True)
        self._all_cb.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        self._all_cb.clicked.connect(self._toggle_all)
        lay.addWidget(self._all_cb)

        self._table = QtWidgets.QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["", "File", "Folder", "Size", "Backed up"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setSortIndicator(4, QtCore.Qt.DescendingOrder)
        self._table.setColumnWidth(0, 30)
        self._table.setColumnWidth(1, 280)
        self._table.setColumnWidth(2, 130)
        self._table.setColumnWidth(3, 80)
        self._table.setStyleSheet(f"""
            QTableWidget {{ background:{_BG}; color:{_TEXT}; gridline-color:{_BORDER};
                            border:1px solid {_BORDER}; border-radius:{theme.R_CTRL}px; font-size:11px; }}
            QHeaderView::section {{ background:{_SURFACE}; color:{_MUTED}; border:none;
                                    padding:6px; font-size:10px; }}
            QTableWidget::item:selected {{ background:{_ACCENT}; color:{_BG}; }}
        """)
        self._table.doubleClicked.connect(self._open_selected)
        lay.addWidget(self._table, 1)

        row = QtWidgets.QHBoxLayout()
        self._dl_btn = QtWidgets.QPushButton("Download Selected")
        self._dl_btn.setMinimumHeight(32)
        self._dl_btn.clicked.connect(self._download_checked)
        row.addWidget(self._dl_btn)

        self._del_btn = QtWidgets.QPushButton("Delete Selected")
        self._del_btn.setMinimumHeight(34)
        # Was a third hardcoded red, built from a mix of plain and f-strings
        # so half the placeholders never interpolated. The shared danger rule
        # in theme.qss() covers it.
        self._del_btn.setObjectName("dangerBtn")
        self._del_btn.clicked.connect(self._delete_checked)
        row.addWidget(self._del_btn)

        self._refresh_btn = QtWidgets.QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._load)
        row.addWidget(self._refresh_btn)
        row.addStretch()
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

        self._set_buttons_enabled(False)

    # ── selection helpers ────────────────────────────────────────────────────

    def _set_buttons_enabled(self, on: bool):
        self._dl_btn.setEnabled(on)
        self._del_btn.setEnabled(on)

    def _toggle_all(self, checked: bool):
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 0)
            if item is not None:
                item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)

    def _checked_rows(self) -> list:
        """Indexes into self._files, not visual rows.

        The table sorts now, so a visual row no longer corresponds to a
        position in self._files — reading self._files[visual_row] would
        download and delete the wrong files. Each checkbox carries its own
        file index in Qt.UserRole.
        """
        rows = []
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 0)
            if item is not None and item.checkState() == QtCore.Qt.Checked:
                idx = item.data(QtCore.Qt.UserRole)
                rows.append(r if idx is None else int(idx))
        return rows

    def _on_item_changed(self, item):
        if item.column() != 0:
            return
        n = len(self._checked_rows())
        self._set_buttons_enabled(bool(n) and not self._busy)
        # Kept in step with the rows without firing _toggle_all back at them.
        self._all_cb.blockSignals(True)
        self._all_cb.setChecked(n == self._table.rowCount() and n > 0)
        self._all_cb.blockSignals(False)

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
            self._set_buttons_enabled(False)
            return
        total = sum(int(f.get("size") or 0) for f in self._files)
        n = len(self._files)
        self._subtitle.setText(
            "{} {}, {}. Untick anything you want to leave alone.".format(
                n, "file" if n == 1 else "files", fb.human_bytes(total)))

        # Filling the table fires itemChanged for every cell, which would
        # recount the selection row by row, so it is disconnected meanwhile.
        try:
            self._table.itemChanged.disconnect(self._on_item_changed)
        except TypeError:
            pass
        # Sorting during a fill reorders rows underneath the loop.
        self._table.setSortingEnabled(False)
        self._table.setRowCount(n)
        for r, f in enumerate(self._files):
            when = str(f.get("uploadedAt") or "")[:16].replace("T", " ")
            cb = QtWidgets.QTableWidgetItem()
            cb.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
            cb.setCheckState(QtCore.Qt.Checked)
            cb.setData(QtCore.Qt.UserRole, r)   # survives re-sorting
            self._table.setItem(r, 0, cb)
            size_raw = f.get("size") or 0
            try:
                size_raw = int(size_raw)
            except (TypeError, ValueError):
                size_raw = 0

            name_item = _SortItem(str(f.get("name", "")),
                                  str(f.get("name", "")).lower())
            folder_item = _SortItem(str(f.get("folder", "")),
                                    str(f.get("folder", "")).lower())
            size_item = _SortItem(fb.human_bytes(f.get("size")), size_raw)
            when_item = _SortItem(when, str(f.get("uploadedAt") or ""))

            # Figures right-align so the magnitudes line up; text stays left.
            size_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            when_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

            for c, item in enumerate((name_item, folder_item, size_item, when_item),
                                     start=1):
                self._table.setItem(r, c, item)
        self._table.setSortingEnabled(True)
        self._table.itemChanged.connect(self._on_item_changed)

        self._all_cb.blockSignals(True)
        self._all_cb.setChecked(True)
        self._all_cb.blockSignals(False)
        self._set_buttons_enabled(True)

    # ── actions ──────────────────────────────────────────────────────────────

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

    def _begin(self, note: str):
        self._busy = True
        self._set_buttons_enabled(False)
        self._refresh_btn.setEnabled(False)
        self._subtitle.setText(note)

    @QtCore.pyqtSlot(object, object)
    def _on_finished(self, message, error):
        self._busy = False
        self._refresh_btn.setEnabled(True)
        if error:
            ThemedMessageBox.warning(self, "Backup", str(error))
        elif message:
            ThemedMessageBox.information(self, "Backup", str(message))
        self._load()

    def _download_checked(self):
        rows = self._checked_rows()
        if not rows:
            return
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Where should the backup be saved?",
            os.path.expanduser("~"))
        if not folder:
            return

        picked = [self._files[r] for r in rows]
        self._begin("Downloading {} file(s)…".format(len(picked)))

        def worker():
            session = fb.current_session()
            if not session:
                self._finished.emit(None, "Not signed in.")
                return
            ok = 0
            bad = []
            for f in picked:
                # Each file keeps its folder, so a restore looks like the
                # APEX_GCS_Saved tree it came from rather than one flat heap.
                sub = str(f.get("folder") or "")
                name = str(f.get("name") or "")
                dest = os.path.join(folder, sub, name) if sub else os.path.join(folder, name)
                try:
                    if fb.download_backup(session, f.get("url", ""), dest):
                        ok += 1
                    else:
                        bad.append(name)
                except Exception as exc:
                    print("[Backup] download error for '" + name + "': " + str(exc))
                    bad.append(name)
            msg = "Saved {} of {} file(s) to:\n{}".format(ok, len(picked), folder)
            if bad:
                msg += "\n\nCould not download: " + ", ".join(bad[:5])
                if len(bad) > 5:
                    msg += " and {} more".format(len(bad) - 5)
            self._finished.emit(msg, None)

        threading.Thread(target=worker, daemon=True).start()

    def _delete_checked(self):
        rows = self._checked_rows()
        if not rows:
            return
        picked = [self._files[r] for r in rows]
        paths = [f.get("path") for f in picked if f.get("path")]
        if not paths:
            ThemedMessageBox.warning(
                self, "Cannot delete",
                "This list is from an older version of the backup service.\n"
                "Click Refresh and try again.")
            return

        freed = fb.human_bytes(sum(int(f.get("size") or 0) for f in picked))
        n = len(paths)
        answer = ThemedMessageBox.question(
            self, "Delete backups",
            "Delete {} {} from your APEX backup and free {}?".format(
                n, "file" if n == 1 else "files", freed),
            "The copies on this computer in APEX_GCS_Saved are not touched, "
            "but the backup copies cannot be recovered.",
            buttons=["Delete", "Cancel"], danger_buttons=["Delete"])
        if answer != "Delete":
            return

        self._begin("Deleting {} file(s)…".format(n))

        def worker():
            session = fb.current_session()
            if not session:
                self._finished.emit(None, "Not signed in.")
                return
            try:
                res = fb.delete_backups(session, paths)
            except Exception as exc:
                self._finished.emit(None, str(exc))
                return
            msg = "Deleted {} file(s), freeing {}.".format(
                res.get("deleted", 0), fb.human_bytes(res.get("freedBytes")))
            if res.get("failed"):
                msg += "\n\n{} could not be deleted.".format(res["failed"])
            self._finished.emit(msg, None)

        threading.Thread(target=worker, daemon=True).start()


class UserProfileDialog(QtWidgets.QDialog):
    """
    Shows the logged-in user's profile info, their backup allowance, a button
    that opens My Backups, and a Logout button.

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

        self.setWindowTitle("User Profile")
        self.setFixedSize(420, 520)
        self.setModal(True)
        self.setWindowFlags(
            QtCore.Qt.Dialog | QtCore.Qt.WindowCloseButtonHint)
        self.setStyleSheet(_base_style() + f"""
            QDialog {{
                background: {_BG};
                border: 1px solid {_BORDER};
                border-radius:{theme.R_CARD}px;
            }}
            QFrame#profileCard {{
                background: {_SURFACE};
                border: 1px solid {_BORDER};
                border-radius:{theme.R_CARD}px;
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
        avatar = QtWidgets.QLabel()
        avatar.setPixmap(theme.icon("user", theme.SLATE, 26).pixmap(26, 26))
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
                            border-radius:{theme.R_SM}px; }}
            QProgressBar::chunk {{ background:{_ACCENT}; border-radius:{theme.R_SM}px; }}
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

        self._backups_btn = _icon_btn("My Backups", "archive")
        self._backups_btn.setMinimumHeight(34)
        self._backups_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_SURFACE};
                color: {_ACCENT};
                border: 1px solid {_ACCENT};
                border-radius:{theme.R_CTRL}px;
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
        self._backups_btn.clicked.connect(self._open_backups)

        logout_btn = QtWidgets.QPushButton("⏻  Logout")
        logout_btn.setMinimumHeight(34)
        logout_btn.setObjectName("dangerBtn")
        logout_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_SURFACE};
                color: {_RED};
                border: 1px solid {_RED};
                border-radius:{theme.R_CTRL}px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {_RED};
                color: white;
            }}
        """)
        logout_btn.clicked.connect(self._do_logout)

        help_btn = QtWidgets.QPushButton("?  Need Help?")
        help_btn.setMinimumHeight(34)
        help_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_SURFACE};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius:{theme.R_CTRL}px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                border-color: {_ACCENT};
                color: {_ACCENT};
            }}
        """)
        help_btn.clicked.connect(self._open_support)

        btn_row.addWidget(self._backups_btn, 1)
        btn_row.addWidget(help_btn, 1)
        btn_row.addWidget(logout_btn, 1)
        root.addLayout(btn_row)

    # ── Data loading (runs in background thread) ──────────────────────────────
    _profile_ready = QtCore.pyqtSignal(object, object)   # (info_dict, unused)
    _quota_ready   = QtCore.pyqtSignal(object)          # backup quota, or None

    def _load_profile(self):
        """Fetch the profile and the backup allowance in background threads."""
        self._profile_ready.connect(self._on_profile_loaded)

        self._quota_ready.connect(self._on_quota_loaded)

        def worker():
            # The profile doc was already fetched once at login and cached
            # (see fb.set_current_profile) — reused here rather than hitting
            # Firestore again every time this dialog opens. Only a cache
            # miss (dialog opened before login finished caching it, in
            # theory) falls back to a live read.
            info = fb.current_profile()
            session = fb.current_session()
            if info is None and session:
                try:
                    info = fb.fetch_profile(fb.ensure_fresh(session))
                    if info is not None:
                        fb.set_current_profile(info)
                except fb.AuthError:
                    info = None
            self._profile_ready.emit(info, None)

            # Separate, and after, because the backup service is a different
            # endpoint and a cold start there should not hold up the details.
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
                            border-radius:{theme.R_SM}px; }}
            QProgressBar::chunk {{ background:{colour}; border-radius:{theme.R_SM}px; }}
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
            self._status_lbl.setText("Could not load your details (offline?)")
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


    # ── Actions ───────────────────────────────────────────────────────────────
    def _open_backups(self):
        """
        Show what is in Firebase Storage.

        Was a link to a shared Google Drive folder, reachable only with a token
        that shipped inside every build. Now lists the signed-in customer's own
        files, through links the server mints on request and which expire in an
        hour, and lets them download or delete what they choose.
        """
        _MyBackupsDialog(self).exec_()

    def _open_support(self):
        """Open the APEX website contact page with user details pre-filled."""
        name = self._value_labels.get("Name", None)
        name = name.text() if name and name.text() != "—" else ""
        email = self._email or ""

        url = QtCore.QUrl("https://theapex.site/contact.html")
        query = QtCore.QUrlQuery()
        query.addQueryItem("form", "report-issue")
        if name:
            query.addQueryItem("name", name)
        if email:
            query.addQueryItem("email", email)
        url.setQuery(query)

        if not QtGui.QDesktopServices.openUrl(url):
            ThemedMessageBox.information(
                self, "Need Help?",
                "Visit https://theapex.site/contact.html",
                "A browser could not be opened automatically.")

    def _do_logout(self):
        reply = ThemedMessageBox.question(
            self, "Logout",
            "Are you sure you want to log out?",
            buttons=["Logout", "Cancel"],
            danger_buttons=["Logout"],
        )
        if reply == "Logout":
            import threading as _t
            _t.Thread(target=fb.log_logout, daemon=True).start()
            self.logout_requested.emit()
            self.accept()


# Need threading import at module level for the worker thread
import threading


# ══════════════════════════════════════════════════════════════════════════════
#  SettingsDialog
# ══════════════════════════════════════════════════════════════════════════════
class SettingsDialog(QtWidgets.QDialog):
    """
    The application's actual preferences.

    There were none: baud rate, the graph sample windows and the alarm bands
    were hardcoded, and the background animation was reachable only through
    the APEX_STATIC_BG and APEX_BG_FPS environment variables. The gear icon,
    meanwhile, opened the user's profile — which is not what a gear means
    anywhere else.

    Only settings that actually take effect are here. Alarm thresholds and
    unit conversion are deliberately absent: wiring those means reworking the
    threshold table and the unit column, and a preferences screen full of
    controls that change nothing is worse than one that is honest about its
    scope.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(560)
        self.setStyleSheet(_base_style() + f"""
            QDialog {{
                background: {_BG};
                border: 1px solid {_BORDER};
                border-radius: {theme.R_CARD}px;
            }}
        """)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 20)
        root.setSpacing(6)

        hdr = QtWidgets.QHBoxLayout()
        icon = QtWidgets.QLabel()
        icon.setPixmap(theme.icon("gear", _ACCENT, 22).pixmap(22, 22))
        hdr.addWidget(icon)
        title = QtWidgets.QLabel("Settings")
        title.setStyleSheet(
            f"color:{_ACCENT}; font-family:'{theme.DISPLAY}';"
            f" font-size:{theme.FS_HEAD}px; font-weight:600; margin-left:8px;")
        hdr.addWidget(title)
        hdr.addStretch()
        root.addLayout(hdr)
        root.addSpacing(8)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignLeft)
        form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)

        # ── Serial ───────────────────────────────────────────────────────────
        form.addRow(self._caption("SERIAL"))
        self._baud = QtWidgets.QComboBox()
        for rate in settings.BAUD_CHOICES:
            self._baud.addItem(f"{rate:,}", rate)
        self._baud.setCurrentIndex(
            max(0, settings.BAUD_CHOICES.index(settings.baud())
                if settings.baud() in settings.BAUD_CHOICES else 4))
        self._baud.setToolTip("Takes effect the next time the port is opened "
                              "— use Reconnect Serial to apply it now.")
        form.addRow(self._label("Baud rate"), self._baud)

        # ── Display ──────────────────────────────────────────────────────────
        form.addRow(self._caption("DISPLAY"))
        self._animate = QtWidgets.QCheckBox("Animate the starfield")
        self._animate.setChecked(settings.get("display/animate"))
        form.addRow("", self._animate)

        self._fps = QtWidgets.QSpinBox()
        self._fps.setRange(10, 60)
        self._fps.setSuffix(" fps")
        self._fps.setValue(settings.get("display/fps"))
        self._fps.setToolTip("Costs roughly 13% of one core at 30 fps.")
        form.addRow(self._label("Animation rate"), self._fps)
        self._animate.toggled.connect(self._fps.setEnabled)
        self._fps.setEnabled(self._animate.isChecked())

        # ── Graphs ───────────────────────────────────────────────────────────
        form.addRow(self._caption("GRAPHS"))
        self._mini = QtWidgets.QSpinBox()
        self._mini.setRange(10, 500)
        self._mini.setSuffix(" samples")
        self._mini.setValue(settings.get("graphs/mini_points"))
        form.addRow(self._label("Dashboard window"), self._mini)

        self._popup = QtWidgets.QSpinBox()
        self._popup.setRange(50, 5000)
        self._popup.setSuffix(" samples")
        self._popup.setValue(settings.get("graphs/popup_points"))
        form.addRow(self._label("Expanded window"), self._popup)

        root.addLayout(form)

        # ── About ────────────────────────────────────────────────────────────
        root.addSpacing(10)
        root.addWidget(self._caption("ABOUT"))
        ver = QtWidgets.QLabel(
            f"APEX Ground Control Station v{APP_VERSION}")
        ver.setStyleSheet(f"color:{_TEXT}; font-size:{theme.FS_BODY}px;")
        root.addWidget(ver)

        about_row = QtWidgets.QHBoxLayout()
        check_btn = _icon_btn("Check for updates", "download")
        check_btn.clicked.connect(self._check_updates)
        about_row.addWidget(check_btn)
        about_row.addStretch()
        root.addLayout(about_row)

        # ── Buttons ──────────────────────────────────────────────────────────
        root.addSpacing(14)
        btn_row = QtWidgets.QHBoxLayout()
        defaults_btn = QtWidgets.QPushButton("Restore defaults")
        defaults_btn.setObjectName("ghostBtn")
        defaults_btn.clicked.connect(self._restore_defaults)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QtWidgets.QPushButton("Save")
        save_btn.setObjectName("primaryBtn")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(defaults_btn)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

    # ── helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _caption(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setObjectName("groupCaption")
        return lbl

    @staticmethod
    def _label(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet(f"color:{_TEXT}; font-size:{theme.FS_BODY}px;")
        return lbl

    def _restore_defaults(self):
        settings.reset()
        self._baud.setCurrentIndex(settings.BAUD_CHOICES.index(settings.baud()))
        self._animate.setChecked(settings.get("display/animate"))
        self._fps.setValue(settings.get("display/fps"))
        self._mini.setValue(settings.get("graphs/mini_points"))
        self._popup.setValue(settings.get("graphs/popup_points"))

    def _check_updates(self):
        import core.updater
        core.updater.check_for_updates(self)

    def _save(self):
        settings.set_("serial/baud", self._baud.currentData())
        settings.set_("display/animate", self._animate.isChecked())
        settings.set_("display/fps", self._fps.value())
        settings.set_("graphs/mini_points", self._mini.value())
        settings.set_("graphs/popup_points", self._popup.value())
        self.accept()
