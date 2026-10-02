import tkinter as tk
from tkinter import messagebox
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import random
import smtplib
from email.mime.text import MIMEText
import webbrowser
import main

# ---------------- GOOGLE SHEETS CONNECTION ----------------

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds = ServiceAccountCredentials.from_json_keyfile_name(
    "credentials.json", scope)

client = gspread.authorize(creds)

sheet = client.open_by_url(
    "https://docs.google.com/spreadsheets/d/1y2gFuAA_1V8xS_APGXz7AOeayK0Uq_70G3VBjWXjnrk/edit"
).sheet1

# ---------------- EMAIL CONFIG ----------------

SENDER_EMAIL = "apexcubesat@gmail.com"
APP_PASSWORD = "eesy ncqq hnah xhxx"


def send_otp(receiver_email):
    otp = str(random.randint(100000, 999999))

    msg = MIMEText(f"Your OTP is: {otp}")
    msg["Subject"] = "Verification OTP"
    msg["From"] = SENDER_EMAIL
    msg["To"] = receiver_email

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.sendmail(SENDER_EMAIL, receiver_email, msg.as_string())
        server.quit()
        return otp
    except:
        messagebox.showerror("Error", "Failed to send OTP")
        return None


# ---------------- MAIN PROGRAM ----------------

def main_program():
    main = tk.Tk()
    main.title("Main Application")
    main.geometry("400x200")

    tk.Label(main, text="WELCOME 😎 ACCESS GRANTED",
             font=("Arial", 16)).pack(pady=60)

    main.mainloop()


# ---------------- LOGIN FUNCTION ----------------

def login():
    email = entry_email.get().strip()
    password = entry_password.get().strip()

    data = sheet.get_all_records()

    for row in data:
        if row["Email"] == email and row["Password"] == password:
            messagebox.showinfo("Success", "Login successful!")
            root.destroy()
            import sys
            sys.exit(main.main())
            return

    messagebox.showerror("Error", "Invalid email or password")


# ---------------- SIGNUP FUNCTION ----------------

def signup():
    global signup_otp

    name = su_name.get().strip()
    email = su_email.get().strip()
    pwd = su_pwd.get().strip()
    repwd = su_repwd.get().strip()
    inst = su_inst.get().strip()
    city = su_city.get().strip()
    state = su_state.get().strip()
    country = su_country.get().strip()
    pin = su_pin.get().strip()

    if "" in (name, email, pwd, repwd, inst, city, state, country, pin):
        messagebox.showerror("Error", "Fill all fields")
        return
    
    # Check Terms agreement
    if not agree_var.get():
        messagebox.showerror("Error", "Please agree to Terms & Conditions")
        return

    if pwd != repwd:
        messagebox.showerror("Error", "Passwords do not match")
        return

    # Check existing email
    data = sheet.get_all_records()
    for row in data:
        if row["Email"] == email:
            messagebox.showerror("Error", "Email already registered")
            return

    signup_otp = send_otp(email)
    if signup_otp:
        otp_window_signup(name, email, pwd, inst, city, state, country, pin)

def otp_window_signup(name, email, pwd, inst, city, state, country, pin):
    win = tk.Toplevel(root)
    win.title("Verify Email")
    win.geometry("300x200")
    win.configure(bg=BG)

    tk.Label(win, text="Enter OTP sent to email",
             bg=BG, fg=TEXT).pack(pady=15)

    otp_entry = tk.Entry(win)
    otp_entry.pack(pady=10)

    def verify():
        if otp_entry.get() == signup_otp:
            sheet.append_row([
                name, email, pwd, inst, city, state, country, pin
            ])
            messagebox.showinfo("Success", "Account created!")
            win.destroy()
            show_login()
        else:
            messagebox.showerror("Error", "Invalid OTP")

    tk.Button(win, text="Verify",
              bg=ACCENT, fg="white",
              command=verify).pack(pady=10)

def otp_window_forgot(email):
    win = tk.Toplevel(root)
    win.title("Reset Password")
    win.geometry("320x260")
    win.configure(bg=BG)

    tk.Label(win, text="Enter OTP",
             bg=BG, fg=TEXT).pack(pady=5)

    otp_entry = tk.Entry(win)
    otp_entry.pack(pady=5)

    tk.Label(win, text="New Password",
             bg=BG, fg=TEXT).pack(pady=5)

    new_pwd = tk.Entry(win, show="*")
    new_pwd.pack(pady=5)

    def verify():
        if otp_entry.get() == fp_otp:
            data = sheet.get_all_records()

            for i, row in enumerate(data, start=2):
                if row["Email"] == email:
                    sheet.update_cell(i, 3, new_pwd.get())
                    messagebox.showinfo("Success", "Password updated!")
                    win.destroy()
                    return

            messagebox.showerror("Error", "Email not found")
        else:
            messagebox.showerror("Error", "Invalid OTP")

    tk.Button(win, text="Reset Password",
              bg=ACCENT, fg="white",
              command=verify).pack(pady=15)

# ---------------- FORGOT PASSWORD ----------------

def forgot_password():
    email = entry_email.get().strip()

    # Treat placeholder as empty
    if not email or email.lower() == "email":
        messagebox.showerror("Error", "Enter your email first")
        return

    global fp_otp
    fp_otp = send_otp(email)

    if fp_otp:
        otp_window_forgot(email)


# ---------------- UI SWITCHING ----------------

def show_signup():
    login_frame.pack_forget()
    signup_frame.pack(fill="both", expand=True)

def show_login():
    signup_frame.pack_forget()
    login_frame.pack(fill="both", expand=True)


# ---------------- MAIN WINDOW ----------------

# ---------------- MAIN WINDOW ----------------
def start():
    global root
    global entry_email, entry_password
    global su_name, su_email, su_pwd, su_repwd
    global su_inst, su_city, su_state, su_country, su_pin
    global agree_var
    global login_frame, signup_frame
    global BG, TEXT, ACCENT
    root = tk.Tk()
    root.title("Sign in")
    root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))
    root.configure(bg="#F0F2F5")
    root.resizable(True, True)

    BG = "#070B18"        # space dark
    CARD = "#0D1326"      # panel dark
    TEXT = "#E6F1FF"      # light text
    ACCENT = "#00D4FF"    # neon cyan

    # -------- CENTER CONTAINER --------
    container = tk.Frame(root, bg=BG)
    container.place(relx=0.5, rely=0.5, anchor="center")

    title = tk.Label(
        root,
        text="APEX GROUND CONTROL STATION",
        font=("Arial", 28, "bold"),
        fg=ACCENT,
        bg=BG
    )
    title.place(relx=0.5, rely=0.15, anchor="center")


    # ---------------- LOGIN FRAME ----------------

    login_frame = tk.Frame(container, bg=CARD, bd=1, relief="solid", padx=30, pady=30)

    tk.Label(login_frame, text="Sign in",
            font=("Arial", 20, "bold"),
            bg=CARD, fg=TEXT).pack(pady=(0, 20))

    entry_email = tk.Entry(login_frame, width=30)
    entry_email.pack(pady=8)
    entry_email.insert(0, "Email")

    entry_password = tk.Entry(login_frame, width=30, show="*")
    entry_password.pack(pady=5)

    show_pass_var = tk.BooleanVar()

    def toggle_password():
        if show_pass_var.get():
            entry_password.config(show="")
        else:
            entry_password.config(show="*")

    tk.Checkbutton(
        login_frame,
        text="Show Password",
        variable=show_pass_var,
        command=toggle_password
    ).pack()

    tk.Button(login_frame, text="Sign in",
                bg="#0A1B3D",
                fg="#00D4FF",
                activebackground="#122C66",
                activeforeground="#00FFFF",
                bd=0,
                width=25, 
                command=login).pack(pady=15)

    tk.Button(login_frame, text="Forgot password?",
            command=forgot_password,
            fg=ACCENT, relief="flat",
            bg=CARD).pack()

    tk.Button(login_frame, text="Create account",
            command=show_signup,
            fg=ACCENT, relief="flat",
            bg=CARD).pack(pady=(10, 0))


    # ---------------- SIGNUP FRAME ----------------

    signup_frame = tk.Frame(container, bg=CARD, bd=1, relief="solid", padx=30, pady=20)

    tk.Label(signup_frame, text="Create Account",
            font=("Arial", 22)).pack(pady=15)

    # ----------- SIGNUP INPUTS WITH LABELS -----------

    tk.Label(signup_frame, text="Full Name").pack()
    su_name = tk.Entry(signup_frame, width=30)
    su_name.pack(pady=3)

    tk.Label(signup_frame, text="Email").pack()
    su_email = tk.Entry(signup_frame, width=30)
    su_email.pack(pady=3)

    tk.Label(signup_frame, text="Create New Password").pack()
    su_pwd = tk.Entry(signup_frame, width=30, show="*")
    su_pwd.pack(pady=3)

    tk.Label(signup_frame, text="Re-enter Password").pack()
    su_repwd = tk.Entry(signup_frame, width=30, show="*")
    su_repwd.pack(pady=3)

    show_signup_pass = tk.BooleanVar()

    def toggle_signup_password():
        if show_signup_pass.get():
            su_pwd.config(show="")
            su_repwd.config(show="")
        else:
            su_pwd.config(show="*")
            su_repwd.config(show="*")

    tk.Checkbutton(
        signup_frame,
        text="Show Password",
        variable=show_signup_pass,
        command=toggle_signup_password
    ).pack(pady=5)

    tk.Label(signup_frame, text="Institution").pack()
    su_inst = tk.Entry(signup_frame, width=30)
    su_inst.pack(pady=3)

    tk.Label(signup_frame, text="City").pack()
    su_city = tk.Entry(signup_frame, width=30)
    su_city.pack(pady=3)

    tk.Label(signup_frame, text="State").pack()
    su_state = tk.Entry(signup_frame, width=30)
    su_state.pack(pady=3)

    tk.Label(signup_frame, text="Country").pack()
    su_country = tk.Entry(signup_frame, width=30)
    su_country.pack(pady=3)

    tk.Label(signup_frame, text="Pincode").pack()
    su_pin = tk.Entry(signup_frame, width=30)
    su_pin.pack(pady=3)

    # -------- TERMS & CONDITIONS --------
    agree_var = tk.BooleanVar()

    def toggle_signup_button():
        if agree_var.get():
            signup_btn.config(state="normal")
        else:
            signup_btn.config(state="disabled")

    def open_terms(event):
        webbrowser.open("https://example.com/terms")  # 🔴 PUT YOUR LINK OR PDF

    tk.Checkbutton(
        signup_frame,
        text="I agree to the Terms & Conditions",
        variable=agree_var,
        command=toggle_signup_button,
        bg=CARD,
        anchor="w",
        justify="left"
    ).pack(pady=8)

    terms_link = tk.Label(
        signup_frame,
        text="View Terms & Conditions",
        fg="blue",
        cursor="hand2",
        bg=CARD
    )
    terms_link.pack()

    terms_link.bind("<Button-1>", open_terms)

    signup_btn = tk.Button(
        signup_frame,
        text="Sign Up",
        width=20,
        command=signup,
        state="disabled"   # 🔴 starts disabled
    )
    signup_btn.pack(pady=10)

    tk.Button(signup_frame, text="Back to Login",
            command=show_login).pack()


    # Start with login
    login_frame.pack(fill="both", expand=True)

    root.mainloop()

if __name__ == "__main__":
    start()