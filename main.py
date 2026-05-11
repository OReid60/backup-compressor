# ========================================================= 
# Imports
# =========================================================
import os
import zipfile
import subprocess
import json
import threading
import queue
import sys  
import pystray
import winshell
import ctypes
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from PIL import Image as PILImage, ImageDraw
from datetime import datetime
from tkinter import *
from tkinter import filedialog, messagebox
from tkinter import ttk

# =========================================================
# Constants
# =========================================================
APP_NAME = "Backup Compressor"
APP_VERSION = "2.5.1"

BG = "#313338"
CARD = "#2b2d31"
CARD_DARK = "#1e1f22"
TEXT = "#f2f3f5"
MUTED = "#b5bac1"
ACCENT = "#5865f2"
ACCENT_HOVER = "#4752c4"
LOCAL_SCHEDULE_COLOR = "#3498db"
CLOUD_SCHEDULE_COLOR = "#1abc9c"
STOPPED_COLOR = "#ffffff"
BTN_WIDTH = 16
GOOGLE_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Main color and sizing constants are kept together so UI sections share one
# visual language without repeating magic values.

# =========================================================
# Runtime State
# =========================================================
main_buttons = []
selected_items = []
cloud_selected_items = []
scheduled_backup_times = []
scheduler_running = False
last_run_time = None    
active_profile_path = None
backup_running = False
compression_progress_window = None
compression_progress_bar = None
ui_thread_id = threading.get_ident()
progress_queue = queue.Queue()
backup_result_queue = queue.Queue()
ui_action_queue = queue.Queue()
tray_icon = None
app_should_exit = False 


# App data lives in %APPDATA%\Backup Compressor so settings and logs persist
# even when the app is packaged into an executable.
app_data_folder = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), APP_NAME)
os.makedirs(app_data_folder, exist_ok=True)

settings_file = os.path.join(app_data_folder, "app_settings.json")

logs_folder = os.path.join(app_data_folder, "logs")
os.makedirs(logs_folder, exist_ok=True)

backup_log_file = os.path.join(logs_folder, "backup_log.txt")
backup_events_file = os.path.join(logs_folder, "backup_events.jsonl")

# =========================================================
# File Paths
# =========================================================

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

# =========================================================
# Backup Item Selection
# =========================================================

def add_files():
    files = filedialog.askopenfilenames(parent=root)
    selected_items.extend(files)
    update_list()

def add_folder():
    folder = filedialog.askdirectory(parent=root)
    if folder:
        selected_items.append(folder)
        update_list()

def update_list():
    listbox.delete(0, END)

    for item in selected_items:
        listbox.insert(END, item)

    item_count = len(selected_items)
    new_height = max(5, min(item_count, 25))
    listbox.config(height=new_height)

    update_backup_summary()

def choose_destination():
    folder = filedialog.askdirectory(parent=root)
    if folder:
        destination_var.set(folder)

def get_backup_name(extension):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"backup_{timestamp}.{extension}"

def add_to_zip(zipf, item):
    if os.path.isfile(item):
        zipf.write(item, os.path.basename(item))
    else:
        for root_dir, dirs, files in os.walk(item):
            for file in files:
                full_path = os.path.join(root_dir, file)
                arcname = os.path.relpath(full_path, os.path.dirname(item))
                zipf.write(full_path, arcname)

def create_zip(output_path, items=None):
    items = selected_items if items is None else items
    total_files = count_backup_files(items)
    processed = 0

    if total_files == 0:
        raise ValueError("No files found to back up.")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for item in items:
            if os.path.isfile(item):
                zipf.write(item, os.path.basename(item))
                processed += 1
                set_progress(
                    (processed / total_files) * 100,
                    f"Compressing: {os.path.basename(item)}"
                )

            elif os.path.isdir(item):
                for root_dir, dirs, files in os.walk(item):
                    for file in files:
                        full_path = os.path.join(root_dir, file)
                        arcname = os.path.relpath(full_path, os.path.dirname(item))
                        zipf.write(full_path, arcname)

                        processed += 1
                        set_progress(
                            (processed / total_files) * 100,
                            f"Compressing: {file}"
                        )

    set_progress(100, "ZIP backup complete.")

def create_7z(output_path, items=None):
    items = selected_items if items is None else items
    seven_zip = r"C:\Program Files\7-Zip\7z.exe"

    if not os.path.exists(seven_zip):
        raise FileNotFoundError("7-Zip not found.")

    command = [
        seven_zip,
        "a",
        "-t7z",
        output_path
    ] + items

    set_progress(10, "Creating 7Z backup...")

    subprocess.run(
        command,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW
    )     

    set_progress(100, "7Z backup complete.")

def create_rar(output_path, items=None):
    items = selected_items if items is None else items
    rar_exe = r"C:\Program Files\WinRAR\Rar.exe"

    if not os.path.exists(rar_exe):
        raise FileNotFoundError(
            "WinRAR is required for RAR backups.\n\n"
            "Download and install WinRAR from:\n"
            "https://www.win-rar.com/"
        )

    command = [
        rar_exe,
        "a",
        output_path
    ] + items

    set_progress(10, "Creating RAR backup...")

    subprocess.run(
        command,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW
    )

    set_progress(100, "RAR backup complete.")   

def get_file_count_and_size(items=None):
    items = selected_items if items is None else items
    total_files = 0
    total_size = 0

    for item in items:
        if os.path.isfile(item):
            total_files += 1
            total_size += os.path.getsize(item)

        elif os.path.isdir(item):
            for root_dir, dirs, files in os.walk(item):
                for file in files:
                    full_path = os.path.join(root_dir, file)

                    try:
                        total_files += 1
                        total_size += os.path.getsize(full_path)
                    except OSError:
                        pass

    return total_files, total_size

def format_size(size_bytes):
    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)

    for unit in units:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024

    return f"{size:.2f} PB"

def update_backup_summary():
    total_files, total_size = get_file_count_and_size()
    summary_var.set(f"Selected: {total_files} files | Total size: {format_size(total_size)}")

def queue_ui_action(callback, *args, **kwargs):
    if threading.get_ident() == ui_thread_id:
        callback(*args, **kwargs)
    else:
        ui_action_queue.put((callback, args, kwargs))

def _apply_ui_busy(is_busy):
    global backup_running

    state = DISABLED if is_busy else NORMAL

    for button in main_buttons:
        button.config(state=state)

    if is_busy:
        status_var.set("Backup running... please wait.")
    else:
        status_var.set("Ready")

def set_ui_busy(is_busy):
    global backup_running
    backup_running = is_busy
    queue_ui_action(_apply_ui_busy, is_busy)

def open_destination_folder():
    destination = destination_var.get()

    if not destination or not os.path.exists(destination):
        return

    try:
        os.startfile(destination)
    except Exception:
        pass

def open_folder_for_path(path):
    if not path:
        messagebox.showwarning("No Location", "No backup location is available for this history item.")
        return

    folder_path = path

    if os.path.isfile(path):
        folder_path = os.path.dirname(path)

    if not os.path.exists(folder_path):
        messagebox.showwarning("Location Missing", "The backup location could not be found.")
        return

    try:
        os.startfile(folder_path)
    except Exception as e:
        messagebox.showerror("Open Location Failed", str(e))

# =========================================================
# App Settings, Profiles, and Logs
# =========================================================

def save_app_settings():
    # Persist lightweight preferences only; profile files keep their own item lists.
    settings = {
        "last_profile": active_profile_path,
        "destination": destination_var.get(),
        "format": format_var.get(),
        "schedule_times": scheduled_backup_times,
        "cloud_schedule_times": cloud_scheduled_backup_times,
        "google_drive_folder": google_drive_folder_var.get(),
        "sql_server": sql_server_var.get(),
        "sql_database": sql_database_var.get(),
        "sql_include_scheduler": sql_include_scheduler_var.get()
    }

    with open(settings_file, "w", encoding="utf-8") as file:
        json.dump(settings, file, indent=4)

def load_app_settings():
    global active_profile_path

    if not os.path.exists(settings_file):
        return

    try:
        with open(settings_file, "r", encoding="utf-8") as file:
            settings = json.load(file)

        destination_var.set(settings.get("destination", ""))
        format_var.set(settings.get("format", "zip"))
        sql_server_var.set(settings.get("sql_server", r".\SQLEXPRESS"))
        sql_database_var.set(settings.get("sql_database", "BackupCompressorTest"))
        sql_include_scheduler_var.set(settings.get("sql_include_scheduler", False))
        google_drive_folder_var.set(settings.get("google_drive_folder", "My Drive"))

        scheduled_backup_times.clear()
        scheduled_backup_times.extend(settings.get("schedule_times", []))
        update_schedule_list()

        cloud_scheduled_backup_times.clear()
        cloud_scheduled_backup_times.extend(settings.get("cloud_schedule_times", []))
        update_cloud_schedule_list()

        active_profile_path = settings.get("last_profile")

    except Exception:
        pass

def on_app_close():
    save_app_settings()

    if app_should_exit:
        root.destroy()
    else:
        hide_window()

def perform_backup(items, destination, format_choice):
    set_progress(0, "Starting backup...")

    total_files, total_size = get_file_count_and_size(items)
    set_progress(0, f"Backing up {total_files} files | {format_size(total_size)}")

    if format_choice == "zip":
        output = os.path.join(destination, get_backup_name("zip"))
        create_zip(output, items)

    elif format_choice == "7z":
        output = os.path.join(destination, get_backup_name("7z"))
        create_7z(output, items)

    elif format_choice == "rar":
        output = os.path.join(destination, get_backup_name("rar"))
        create_rar(output, items)

    else:
        raise ValueError("Unknown backup format selected.")

    write_backup_log(destination, output, format_choice, items)
    set_progress(100, f"Backup complete: {os.path.basename(output)}")
    return output

def update_scheduler_indicator_after_backup():
    if scheduler_running:
        scheduler_status_var.set("Idle")
        status_label.config(image=icon_blue)
    else:
        status_label.config(image=icon_red)

    refresh_schedule_tray_icon()

def start_backup(show_messages=True):
    if backup_running:
        return False

    items = list(selected_items)

    if not items:
        if show_messages:
            messagebox.showwarning("No files", "Please select files or folders first.")
        return False

    destination = destination_var.get()

    if not destination:
        if show_messages:
            messagebox.showwarning("No destination", "Please choose a destination folder.")
        return False

    if not os.path.exists(destination):
        os.makedirs(destination)

    format_choice = format_var.get()

    locked_files = get_locked_files(items)

    if locked_files:
        file_preview = "\n".join(locked_files[:10])

        if show_messages:
            messagebox.showwarning(
                "Files In Use",
                f"Backup cannot start because file(s) are currently in use:\n\n{file_preview}\n\nClose the file(s) and try again."
            )
        else:
            write_scheduler_status("Scheduled backup skipped: file(s) in use.")

        return False

    try:
        set_ui_busy(True)
        if show_messages:
            show_compression_progress_window()

        output = perform_backup(items, destination, format_choice)
        queue_ui_action(save_app_settings)
        queue_ui_action(close_compression_progress_window)

        if tray_icon:
            tray_icon.notify(
                f"Backup completed: {os.path.basename(output)}",
                "Backup Compressor"
            )

        if show_messages:
            open_after = messagebox.askyesno(
                "Backup Complete",
                f"Backup created successfully:\n\n{output}\n\nOpen destination folder?"
            )

            if open_after:
                open_destination_folder()

        return True

    except Exception as e:
        queue_ui_action(close_compression_progress_window)

        if show_messages:
            messagebox.showerror("Backup Failed", str(e))
        else:
            write_scheduler_status(f"Scheduled backup failed: {e}")

        write_backup_event(
            "local",
            "failed",
            str(e),
            destination=destination,
            backup_format=format_choice.upper()
        )

        return False

    finally:
        queue_ui_action(close_compression_progress_window)
        set_ui_busy(False)
        queue_ui_action(update_scheduler_indicator_after_backup)

def is_file_in_use(file_path):
    GENERIC_READ = 0x80000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = -1

    handle = ctypes.windll.kernel32.CreateFileW(
        file_path,
        GENERIC_READ,
        0,  # no sharing allowed
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        None
    )

    if handle == INVALID_HANDLE_VALUE:
        error = ctypes.windll.kernel32.GetLastError()
        return error in (32, 33)  # sharing violation / lock violation

    ctypes.windll.kernel32.CloseHandle(handle)
    return False

def get_locked_files(items=None):
    items = selected_items if items is None else items
    locked_files = []

    for item in items:
        if os.path.isfile(item):
            if is_file_in_use(item):
                locked_files.append(item)

        elif os.path.isdir(item):
            for root_dir, dirs, files in os.walk(item):
                for file in files:
                    full_path = os.path.join(root_dir, file)

                    if is_file_in_use(full_path):
                        locked_files.append(full_path)

    return locked_files

def clear_list():
    selected_items.clear()
    update_list()

def write_backup_log(destination, output_file, format_choice, items=None):
    items = selected_items if items is None else items
    log_path = backup_log_file

    with open(log_path, "a", encoding="utf-8") as log:
        log.write("====================================\n")
        log.write(f"Backup Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"Format: {format_choice.upper()}\n")
        log.write(f"Output File: {output_file}\n")
        log.write("Items Backed Up:\n")

        for item in items:
            log.write(f"- {item}\n")

        log.write("\n")

    write_backup_event(
        "local",
        "completed",
        f"{format_choice.upper()} backup completed",
        output_file=output_file,
        destination=destination,
        backup_format=format_choice.upper()
    )
    queue_ui_action(refresh_logs_tab)

def write_backup_event(event_type, status, message, output_file="", destination="", backup_format=""):
    event = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": event_type,
        "status": status,
        "message": message,
        "file": output_file,
        "destination": destination,
        "format": backup_format
    }

    with open(backup_events_file, "a", encoding="utf-8") as event_file:
        event_file.write(json.dumps(event) + "\n")

def load_backup_events(limit=50):
    if not os.path.exists(backup_events_file):
        return []

    events = []

    with open(backup_events_file, "r", encoding="utf-8") as event_file:
        for line in event_file:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    return events[-limit:]

def save_profile():
    if not selected_items:
        messagebox.showwarning("No Items", "Add files or folders before saving a profile.")
        return
        

    profile_path = filedialog.asksaveasfilename(
        parent=root,
        defaultextension=".json",
        filetypes=[("Backup Profile", "*.json")]
    )

    if not profile_path:
        return
    global active_profile_path
    active_profile_path = profile_path

    profile_data = {
        "items": selected_items,
        "destination": destination_var.get(),
        "format": format_var.get(),
        "schedule_times": scheduled_backup_times
    }

    with open(profile_path, "w", encoding="utf-8") as file:
        json.dump(profile_data, file, indent=4)

    messagebox.showinfo("Profile Saved", "Backup profile saved successfully.")
    save_app_settings()

def load_profile():
    profile_path = filedialog.askopenfilename(
        parent=root,
        filetypes=[("Backup Profile", "*.json")]
    )

    if not profile_path:
        return

    with open(profile_path, "r", encoding="utf-8") as file:
        profile_data = json.load(file)

    selected_items.clear()
    selected_items.extend(profile_data.get("items", []))

    destination_var.set(profile_data.get("destination", ""))
    format_var.set(profile_data.get("format", "zip"))

    scheduled_backup_times.clear()
    scheduled_backup_times.extend(profile_data.get("schedule_times", []))

    update_list()
    update_schedule_list()

    global active_profile_path
    active_profile_path = profile_path

    messagebox.showinfo("Profile Loaded", "Backup profile loaded successfully.")

def view_backup_log():
    log_path = backup_log_file

    if not os.path.exists(log_path):
        messagebox.showinfo("No Logs", "No backup log found yet.")
        refresh_logs_tab()
        return

    log_window = Toplevel(root)
    log_window.title("Backup Log Viewer")
    log_window.geometry("700x450")
    log_window.configure(bg="#1e1e1e")
    log_window.transient(root)

    text_area = Text(
        log_window,
        wrap=WORD,
        bg="#1f1f1f",
        fg="#ffffff",
        insertbackground="#ffffff",
        font=("Consolas", 10),
        relief=FLAT
    )
    text_area.pack(side=LEFT, expand=True, fill=BOTH, padx=(10, 0), pady=10)

    scrollbar = Scrollbar(log_window)
    scrollbar.pack(side=RIGHT, fill=Y, padx=(0, 10), pady=10)

    text_area.config(yscrollcommand=scrollbar.set)
    scrollbar.config(command=text_area.yview)

    with open(log_path, "r", encoding="utf-8") as log_file:
        text_area.insert(END, log_file.read())

    text_area.config(state=DISABLED)
    center_window_over_parent(log_window, root, 700, 450)
    refresh_logs_tab()

def refresh_logs_tab():
    if "log_text" not in globals():
        return

    log_text.config(state=NORMAL)
    log_text.delete("1.0", END)

    if os.path.exists(backup_log_file):
        with open(backup_log_file, "r", encoding="utf-8") as log_file:
            log_text.insert(END, log_file.read())
    else:
        log_text.insert(END, "No backup log found yet.")

    log_text.config(state=DISABLED)
    log_text.see(END)

def apply_modern_style():
    
    style = ttk.Style()
    style.theme_use("clam")

    root.configure(bg=BG)

    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD, relief="flat")
    style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))

    style.configure(
    "TButton",
    font=("Segoe UI", 10),
    padding=8,
    background=CARD_DARK,
    foreground=TEXT,
    borderwidth=0,
    relief="flat"
)

    style.map("TButton", background=[("active", "#3f4147")])

    style.configure(
    "Accent.TButton",
    background=ACCENT,
    foreground="#ffffff",
    font=("Segoe UI", 11, "bold"),
    padding=10,
    borderwidth=0,
    relief="flat"
)

    style.map("Accent.TButton", background=[("active", ACCENT_HOVER)])

    style.configure(
    "CompactAccent.TButton",
    background=ACCENT,
    foreground="#ffffff",
    font=("Segoe UI", 10),
    padding=8,
    borderwidth=0,
    relief="flat"
)

    style.map("CompactAccent.TButton", background=[("active", ACCENT_HOVER)])

    style.configure("TRadiobutton", background="#2d2d2d", foreground="#ffffff", font=("Segoe UI", 10))
    style.configure(
        "StartupDisabled.TCheckbutton",
        background=CARD,
        foreground="#ffb86c",
        font=("Segoe UI", 10, "bold")
    )
    style.configure(
        "StartupEnabled.TCheckbutton",
        background=CARD,
        foreground="#57f287",
        font=("Segoe UI", 10, "bold")
    )
    style.configure("TEntry", fieldbackground="#3a3a3a", foreground="#ffffff")
    style.configure(
        "Compression.Horizontal.TProgressbar",
        background=CLOUD_SCHEDULE_COLOR,
        troughcolor="#b8b6ae",
        bordercolor="#b8b6ae",
        lightcolor=CLOUD_SCHEDULE_COLOR,
        darkcolor=CLOUD_SCHEDULE_COLOR
    )

    # Notebook base
    style.configure(
        "TNotebook",
        background="#1e1e1e",
        borderwidth=0,
        relief="flat"
    )

    # Tabs
    style.configure(
        "TNotebook",
        background=BG,
        borderwidth=0,
        relief="flat"
)

    style.configure(
        "TNotebook.Tab",
        background=CARD_DARK,
        foreground=MUTED,
        padding=(18, 10),
        borderwidth=0,
        relief="flat"
)

    style.map(
        "TNotebook.Tab",
        background=[
            ("selected", CARD),
            ("active", "#3f4147")
        ],
        foreground=[
            ("selected", TEXT),
            ("active", TEXT)
    ]
)

    style.layout("TNotebook.Tab", [
        ("Notebook.tab", {
            "sticky": "nswe",
            "children": [
                ("Notebook.padding", {
                    "children": [
                        ("Notebook.label", {"sticky": ""})
                    ]
                })
            ]
        })
    ])

# =========================================================
# Backup Runtime Helpers
# =========================================================
    
def set_progress(value, message):
    if threading.get_ident() != ui_thread_id:
        progress_queue.put((value, message))
        return

    progress_var.set(value)
    status_var.set(message)
    root.update_idletasks()

def process_progress_queue():
    try:
        while True:
            value, message = progress_queue.get_nowait()
            progress_var.set(value)
            status_var.set(message)
    except queue.Empty:
        pass

    root.after(100, process_progress_queue)

def process_ui_action_queue():
    try:
        while True:
            callback, args, kwargs = ui_action_queue.get_nowait()
            callback(*args, **kwargs)
    except queue.Empty:
        pass

    root.after(100, process_ui_action_queue)

def start_backup_async():
    if backup_running:
        return

    items = list(selected_items)

    if not items:
        messagebox.showwarning("No files", "Please select files or folders first.")
        return

    destination = destination_var.get()

    if not destination:
        messagebox.showwarning("No destination", "Please choose a destination folder.")
        return

    format_choice = format_var.get()
    set_ui_busy(True)
    show_compression_progress_window()
    set_progress(0, "Starting backup...")

    backup_thread = threading.Thread(
        target=run_interactive_backup_worker,
        args=(items, destination, format_choice),
        daemon=True
    )
    backup_thread.start()

def run_interactive_backup_worker(items, destination, format_choice):
    try:
        if not os.path.exists(destination):
            os.makedirs(destination)

        locked_files = get_locked_files(items)

        if locked_files:
            backup_result_queue.put({
                "success": False,
                "locked_files": locked_files,
                "destination": destination,
                "format_choice": format_choice
            })
            return

        output = perform_backup(items, destination, format_choice)

        backup_result_queue.put({
            "success": True,
            "output": output,
            "destination": destination,
            "format_choice": format_choice
        })

    except Exception as e:
        write_backup_event(
            "local",
            "failed",
            str(e),
            destination=destination,
            backup_format=format_choice.upper()
        )

        backup_result_queue.put({
            "success": False,
            "error": str(e),
            "destination": destination,
            "format_choice": format_choice
        })

def process_backup_result_queue():
    try:
        while True:
            result = backup_result_queue.get_nowait()
            finish_interactive_backup(result)
    except queue.Empty:
        pass

    root.after(100, process_backup_result_queue)

def finish_interactive_backup(result):
    close_compression_progress_window()
    set_ui_busy(False)
    update_scheduler_indicator_after_backup()
    save_app_settings()
    refresh_logs_tab()

    if result.get("locked_files"):
        file_preview = "\n".join(result["locked_files"][:10])
        messagebox.showwarning(
            "Files In Use",
            f"Backup cannot start because file(s) are currently in use:\n\n{file_preview}\n\nClose the file(s) and try again."
        )
        return

    if not result.get("success"):
        messagebox.showerror("Backup Failed", result.get("error", "Unknown backup error."))
        return

    output = result["output"]

    if tray_icon:
        tray_icon.notify(
            f"Backup completed: {os.path.basename(output)}",
            "Backup Compressor"
        )

    open_after = messagebox.askyesno(
        "Backup Complete",
        f"Backup created successfully:\n\n{output}\n\nOpen destination folder?"
    )

    if open_after:
        open_destination_folder()

def show_compression_progress_window():
    global compression_progress_window, compression_progress_bar

    if compression_progress_window and compression_progress_window.winfo_exists():
        return

    compression_progress_window = Toplevel(root)
    compression_progress_window.title("Compression Progress")
    compression_progress_window.geometry("460x150")
    compression_progress_window.configure(bg=BG)
    compression_progress_window.transient(root)
    compression_progress_window.grab_set()
    compression_progress_window.resizable(False, False)
    compression_progress_window.protocol("WM_DELETE_WINDOW", lambda: None)

    ttk.Label(
        compression_progress_window,
        text="Compressing Backup",
        background=BG,
        foreground=TEXT,
        font=("Segoe UI", 12, "bold")
    ).pack(anchor="w", padx=20, pady=(20, 10))

    compression_progress_bar = ttk.Progressbar(
        compression_progress_window,
        variable=progress_var,
        maximum=100,
        style="Compression.Horizontal.TProgressbar"
    )
    compression_progress_bar.pack(fill=X, padx=20, pady=(0, 10))

    ttk.Label(
        compression_progress_window,
        textvariable=status_var,
        background=BG,
        foreground=MUTED
    ).pack(anchor="w", padx=20)

    center_window_over_parent(compression_progress_window, root, 460, 150)

def close_compression_progress_window():
    global compression_progress_window, compression_progress_bar

    if compression_progress_window and compression_progress_window.winfo_exists():
        compression_progress_window.grab_release()
        compression_progress_window.destroy()

    compression_progress_window = None
    compression_progress_bar = None

def count_backup_files(items=None):
    items = selected_items if items is None else items
    total = 0

    for item in items:
        if os.path.isfile(item):
            total += 1
        elif os.path.isdir(item):
            for _, _, files in os.walk(item):
                total += len(files)

    return total

def get_backup_folder_summary():
    destination = destination_var.get().strip()

    if not destination or not os.path.exists(destination):
        return "Destination not set"

    backup_files = get_backup_file_paths(destination)

    total_size = 0

    for file_path in backup_files:
        try:
            total_size += os.path.getsize(file_path)
        except OSError:
            pass

    return f"{len(backup_files)} backup file(s) | {format_size(total_size)}"

def get_backup_file_paths(destination):
    if not destination or not os.path.exists(destination):
        return []

    return [
        os.path.join(destination, file)
        for file in os.listdir(destination)
        if file.lower().endswith((".zip", ".7z", ".rar", ".bak"))
    ]

def refresh_dashboard():
    dashboard_status_var.set(status_var.get())
    dashboard_progress_var.set(progress_var.get())
    dashboard_schedule_var.set(scheduler_status_var.get())
    dashboard_cloud_var.set(cloud_status_var.get())
    dashboard_google_var.set(google_drive_status_var.get())
    dashboard_storage_var.set(get_backup_folder_summary())

    destination = destination_var.get().strip()
    backup_files = get_backup_file_paths(destination)

    if not backup_files:
        dashboard_last_backup_var.set("No backup files found")
        dashboard_history.delete(*dashboard_history.get_children())
        root.after(1000, refresh_dashboard)
        return

    backup_file_set = set(backup_files)
    events = [
        event for event in load_backup_events(limit=100)
        if not event.get("file") or event.get("file") in backup_file_set
    ][-50:]

    if events:
        last_event = events[-1]
        dashboard_last_backup_var.set(
            f"{last_event.get('time', '')} | {last_event.get('type', '')} | {last_event.get('status', '')}"
        )
    else:
        dashboard_last_backup_var.set("No backup events yet")

    dashboard_history.delete(*dashboard_history.get_children())

    for event in reversed(events):
        dashboard_history.insert(
            "",
            END,
            values=(
                event.get("time", ""),
                event.get("type", ""),
                event.get("status", ""),
                event.get("format", ""),
                os.path.basename(event.get("file", "")),
                event.get("message", ""),
                event.get("file", ""),
                event.get("destination", "")
            )
        )

    root.after(1000, refresh_dashboard)

def open_selected_dashboard_backup(event=None):
    selected = dashboard_history.selection()

    if not selected:
        messagebox.showwarning("No Backup Selected", "Select a backup history item first.")
        return

    values = dashboard_history.item(selected[0], "values")
    file_path = values[6] if len(values) > 6 else ""
    destination = values[7] if len(values) > 7 else ""

    open_folder_for_path(file_path or destination)

def run_backup_silent():
    destination = destination_var.get().strip()

    before_files = set()

    if destination and os.path.exists(destination):
        before_files = set(os.listdir(destination))

    file_backup_success = start_backup(show_messages=False)

    if sql_include_scheduler_var.get():
        write_scheduler_status("SQL scheduler option is enabled. Starting SQL backup.")
        backup_sql_database_silent()
    else:
        write_scheduler_status("SQL scheduler option is disabled. Skipping SQL backup.")

    if cloud_schedule_enabled_var.get():
        if destination and os.path.exists(destination):
            after_files = set(os.listdir(destination))
            new_files = after_files - before_files

            backup_files = [
                os.path.join(destination, file)
                for file in new_files
                if file.lower().endswith((".zip", ".7z", ".rar", ".bak"))
            ]

            if backup_files:
                write_scheduler_status(
                    f"Cloud schedule enabled. Uploading {len(backup_files)} backup file(s) to Google Drive."
                )

                for file_path in backup_files:
                    try:
                        upload_file_to_google_drive(file_path)
                        write_scheduler_status(
                            f"Google Drive upload complete: {os.path.basename(file_path)}"
                        )
                    except Exception as e:
                        write_scheduler_status(
                            f"Google Drive upload failed for {os.path.basename(file_path)}: {e}"
                        )
            else:
                write_scheduler_status("Cloud upload skipped: no new backup files found.")
        else:
            write_scheduler_status("Cloud upload skipped: destination folder not found.")

    return file_backup_success

def run_cloud_backup_silent():
    destination = destination_var.get().strip()

    before_files = set()

    if destination and os.path.exists(destination):
        before_files = set(os.listdir(destination))

    file_backup_success = False

    if cloud_selected_items:
        original_selected_items = list(selected_items)

        try:
            selected_items.clear()
            selected_items.extend(cloud_selected_items)
            write_scheduler_status("Cloud schedule starting selected file/folder backup.")
            file_backup_success = start_backup(show_messages=False)
        finally:
            selected_items.clear()
            selected_items.extend(original_selected_items)
    else:
        write_scheduler_status("Cloud file backup skipped: no cloud items selected.")

    if sql_include_scheduler_var.get():
        write_scheduler_status("Cloud schedule SQL option is enabled. Starting SQL backup.")
        backup_sql_database_silent()
    else:
        write_scheduler_status("Cloud schedule SQL option is disabled. Skipping SQL backup.")

    if destination and os.path.exists(destination):
        after_files = set(os.listdir(destination))
        new_files = after_files - before_files

        backup_files = [
            os.path.join(destination, file)
            for file in new_files
            if file.lower().endswith((".zip", ".7z", ".rar", ".bak"))
        ]

        upload_backup_files_to_google_drive(backup_files)
    else:
        write_scheduler_status("Cloud upload skipped: destination folder not found.")

    return file_backup_success

def upload_backup_files_to_google_drive(backup_files):
    if not backup_files:
        write_scheduler_status("Cloud upload skipped: no new backup files found.")
        return

    write_scheduler_status(
        f"Cloud schedule enabled. Uploading {len(backup_files)} backup file(s) to Google Drive."
    )

    for file_path in backup_files:
        try:
            upload_file_to_google_drive(file_path)
            write_scheduler_status(
                f"Google Drive upload complete: {os.path.basename(file_path)}"
            )
        except Exception as e:
            write_scheduler_status(
                f"Google Drive upload failed for {os.path.basename(file_path)}: {e}"
            )

def backup_sql_database_silent():
    selected_databases = [
        db for db, var in sql_database_vars.items()
        if var.get()
    ]

    if not selected_databases:
        typed_db = sql_database_var.get().strip()
        if typed_db:
            selected_databases = [typed_db]

    if not selected_databases:
        write_scheduler_status("SQL backup skipped: no database selected.")
        return False

    success_count = 0

    for database in selected_databases:
        if backup_single_sql_database(database, show_messages=False):
            success_count += 1

    write_scheduler_status(
        f"SQL scheduled backup finished: {success_count}/{len(selected_databases)} database(s)."
    )

    return success_count > 0

def write_scheduler_status(message):
    with open(backup_log_file, "a", encoding="utf-8") as log:
        log.write(f"[Scheduler] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - v{APP_VERSION} - {message}\n")

    status = "completed"
    lowered = message.lower()

    if "failed" in lowered:
        status = "failed"
    elif "skipped" in lowered or "stopped" in lowered:
        status = "info"
    elif "started" in lowered or "starting" in lowered:
        status = "running"

    write_backup_event("scheduler", status, message)
    refresh_logs_tab()

# =========================================================
# Scheduler and Tray Helpers
# =========================================================

def check_scheduled_backups():  
    global last_run_time    
    if scheduler_running:
        current_time = datetime.now().strftime("%I:%M %p").lstrip("0")
        today = datetime.now().strftime("%a")

        for schedule in scheduled_backup_times:
            if isinstance(schedule, dict):
                schedule_time = schedule.get("time")
                schedule_days = schedule.get("days", [])
                should_run = current_time == schedule_time and today in schedule_days
            else:
                should_run = current_time == schedule and selected_days[today].get()

            if should_run and current_time != last_run_time:
                last_run_time = current_time
                scheduler_status_var.set("Running Backup")
                status_label.config(image=icon_blue)
                update_tray_icon(LOCAL_SCHEDULE_COLOR)
                write_scheduler_status(f"Scheduled backup started at {current_time}")

                backup_thread = threading.Thread(target=run_backup_silent)
                backup_thread.daemon = True
                backup_thread.start()
                break

        else:
            if scheduler_status_var.get() != "Running Backup":
                scheduler_status_var.set("Idle")
                status_label.config(image=icon_blue)
                refresh_schedule_tray_icon()

    root.after(60000, check_scheduled_backups)

def start_cloud_backup_schedule():
    global cloud_scheduler_running

    if not cloud_scheduled_backup_times:
        messagebox.showwarning(
            "No Cloud Schedule",
            "Add at least one cloud backup time in the Cloud Backup tab first."
        )
        return

    destination = destination_var.get().strip()

    if not destination:
        messagebox.showwarning(
            "No Destination",
            "Choose a local backup destination on the Backup tab first."
        )
        return

    if not os.path.exists(destination):
        os.makedirs(destination)

    cloud_scheduler_running = True
    cloud_status_var.set("Running")
    cloud_schedule_enabled_var.set(True)
    sql_include_scheduler_var.set(True)
    refresh_schedule_tray_icon()

    google_email = get_google_drive_account_email()
    file_count, folder_count = get_selected_item_counts(cloud_selected_items)
    database_count = len(get_selected_sql_databases())

    messagebox.showinfo(
        "Local + Cloud Schedule Started",
        "Local + cloud backup schedule is now enabled.\n\n"
        f"Local backups will be saved to:\n{destination}\n\n"
        "Schedule times will use the Cloud Backup tab only.\n\n"
        f"Folder: {folder_count}\n"
        f"Files: {file_count}\n"
        f"Databases: {database_count}\n\n"
        f"Cloud backups will upload to Google Drive account:\n{google_email}\n\n"
        f"Google Drive folder:\n{google_drive_folder_var.get().strip() or 'Backup Compressor'}\n"
        "Backups will be nested by file type, date, and backup time."
    )

    write_scheduler_status(
        f"Cloud backup schedule enabled from Cloud Backup tab. Local destination: {destination}. Google account: {google_email}"
    )

def get_selected_item_counts(items=None):
    items = selected_items if items is None else items
    file_count = 0
    folder_count = 0

    for item in items:
        if os.path.isfile(item):
            file_count += 1
        elif os.path.isdir(item):
            folder_count += 1

    return file_count, folder_count

def get_selected_sql_databases():
    selected_databases = [
        db for db, var in sql_database_vars.items()
        if var.get()
    ]

    if selected_databases:
        return selected_databases

    typed_db = sql_database_var.get().strip()

    if typed_db:
        return [db.strip() for db in typed_db.split(",") if db.strip()]

    return []

def update_schedule_list():
    schedule_listbox.delete(0, END)

    for schedule in scheduled_backup_times:
        if isinstance(schedule, dict):
            name = schedule.get("name", "Local Backup")
            time = schedule.get("time", "")
            description = schedule.get("description", "")
            days = ", ".join(schedule.get("days", []))

            display_text = f"{name} | {time} | {description} | {days}"
            schedule_listbox.insert(END, display_text)
        else:
            # old saved schedules support
            schedule_listbox.insert(END, schedule)

def add_backup_time():
    hour = hours_var.get()
    minute = minutes_var.get()
    ampm = ampm_var.get()

    backup_time = f"{int(hour)}:{minute} {ampm}"

    new_schedule = {
        "name": schedule_name_var.get().strip() or "Local Backup",
        "time": backup_time,
        "description": schedule_description_var.get().strip() or "No description",
        "days": [day for day, var in selected_days.items() if var.get()]
    }

    scheduled_backup_times.append(new_schedule)

    update_schedule_list()
    save_app_settings()

    write_scheduler_status(
        f"Backup schedule added: {new_schedule['name']} at {backup_time}"
    )
        
def remove_selected_time():
    selected = schedule_listbox.curselection()

    if not selected:
        messagebox.showwarning(
            "No Schedule Selected",
            "Select a schedule to remove."
        )
        return

    index = selected[0]

    removed_schedule = scheduled_backup_times.pop(index)

    update_schedule_list()

    save_app_settings()

    if isinstance(removed_schedule, dict):
        schedule_name = removed_schedule.get("name", "Unknown")
        write_scheduler_status(
            f"Schedule removed: {schedule_name}"
        )

def update_tray_icon(color):
    if tray_icon:
        tray_icon.icon = create_tray_image(color)

def refresh_schedule_tray_icon():
    if cloud_scheduler_running:
        update_tray_icon(CLOUD_SCHEDULE_COLOR)
    elif scheduler_running:
        update_tray_icon(LOCAL_SCHEDULE_COLOR)
    else:
        update_tray_icon(STOPPED_COLOR)

def start_scheduler():  # start
    global scheduler_running

    if not scheduled_backup_times:
        messagebox.showwarning("No Schedule", "Add at least one backup time first.")
        return

    scheduler_running = True

    scheduler_status_var.set("Running")   # when started
    status_label.config(image=icon_blue)
    refresh_schedule_tray_icon()
    
    write_scheduler_status("Scheduler started")

def create_tray_image(color=STOPPED_COLOR):
    size = 64
    padding = 2

    image = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.ellipse(
        (padding, padding, size - padding, size - padding),
        fill=color,
        outline="#0f3f3f",
        width=2
    )

    return image    

def stop_scheduler():   # stopped
    global scheduler_running

    scheduler_running = False
    
    scheduler_status_var.set("Stopped")   
    status_label.config(image=icon_red)
    refresh_schedule_tray_icon()

    write_scheduler_status("Scheduler stopped")

def create_status_icon(color, size=14):
    img = PhotoImage(width=size, height=size)

    for x in range(size):
        for y in range(size):
            # Draw a circle
            if (x - size//2)**2 + (y - size//2)**2 <= (size//2)**2:
                img.put(color, (x, y))

    return img

def show_window(icon=None, item=None):
    root.after(0, root.deiconify)
    root.after(0, root.lift)
    root.after(0, root.focus_force)

def hide_window():
    root.withdraw()

def quit_app(icon=None, item=None):
    global app_should_exit

    app_should_exit = True
    save_app_settings()

    if tray_icon:
        tray_icon.stop()

    root.after(0, root.destroy)

def setup_tray_icon():
    global tray_icon

    tray_icon = pystray.Icon(
        "Backup Compressor",
        create_tray_image(STOPPED_COLOR),
        "Backup Compressor",
        menu=pystray.Menu(
            pystray.MenuItem("Scheduler", show_window, default=True),  # KEY
            pystray.MenuItem("Exit", quit_app),
    )
)

    threading.Thread(target=tray_icon.run, daemon=True).start()

def get_startup_shortcut_path():
    startup_folder = winshell.startup()
    return os.path.join(startup_folder, "Backup Compressor.lnk")

def enable_run_on_startup():
    shortcut_path = get_startup_shortcut_path()

    python_exe = sys.executable
    script_path = os.path.abspath(sys.argv[0])

    with winshell.shortcut(shortcut_path) as shortcut:
        shortcut.path = python_exe
        shortcut.arguments = f'"{script_path}"'
        shortcut.description = "Start Backup Compressor with Windows"

    messagebox.showinfo("Startup Enabled", "App will run when Windows starts.")

def disable_run_on_startup():
    shortcut_path = get_startup_shortcut_path()

    if os.path.exists(shortcut_path):
        os.remove(shortcut_path)

    messagebox.showinfo("Startup Disabled", "App will no longer run when Windows starts.")

# =========================================================
# SQL Server Backup
# =========================================================

def discover_sql_servers():
    command = ["sqlcmd", "-L"]
    servers = set()

    computer_name = os.environ.get("COMPUTERNAME", "")

    servers.add(r".\SQLEXPRESS")
    servers.add(r"localhost\SQLEXPRESS")

    if computer_name:
        servers.add(fr"{computer_name}\SQLEXPRESS")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        for line in result.stdout.splitlines():
            line = line.strip()

            if not line:
                continue
            if "Servers:" in line:
                continue
            if ";" in line:
                continue
            if "UID:" in line or "PWD:" in line or "Trusted_Connection" in line:
                continue
            if "Login ID" in line:
                continue

            if (
                "\\" in line
                or line.upper().startswith(("DESKTOP", "LAPTOP", "SERVER", "LOCALHOST"))
                or line.startswith(".")
            ):
                servers.add(line)

        server_list = sorted(servers)

        root.after(0, lambda: show_sql_server_selection_window(server_list))
        root.after(0, lambda: sql_selected_count_var.set("SQL Server search complete."))

    except Exception as e:
        root.after(0, lambda: messagebox.showerror("SQL Discovery Failed", str(e)))

    finally:
        root.after(
            0,
            lambda: btn_find_servers.config(
                text="Find SQL Servers",
                state=NORMAL
            )
        )
    
def start_sql_server_discovery():
    btn_find_servers.config(
        text="Searching...",
        state=DISABLED
    )

    sql_selected_count_var.set("Searching for SQL Servers...")

    thread = threading.Thread(target=discover_sql_servers)
    thread.daemon = True
    thread.start()

def show_sql_server_selection_window(servers):
    server_window = Toplevel(root)
    server_window.title("Select SQL Server")
    server_window.geometry("450x350")
    server_window.configure(bg=BG)
    server_window.transient(root)

    ttk.Label(
        server_window,
        text="Discovered / Common SQL Servers",
        background=BG,
        foreground=TEXT,
        font=("Segoe UI", 12, "bold")
    ).pack(anchor="w", padx=15, pady=(15, 10))

    listbox = Listbox(
        server_window,
        bg="#1f1f1f",
        fg="#ffffff",
        selectbackground="#0078d4",
        selectforeground="#ffffff",
        font=("Segoe UI", 10),
        relief=FLAT
    )

    listbox.pack(fill=BOTH, expand=True, padx=15, pady=(0, 10))

    for server in servers:
        listbox.insert(END, server)

    def select_server():
        selected = listbox.curselection()

        if not selected:
            return

        chosen_server = listbox.get(selected[0])

        sql_server_var.set(chosen_server)

        messagebox.showinfo(
            "SQL Server Selected",
            f"Selected SQL Server:\n\n{chosen_server}"
        )

        server_window.destroy()

    button_row = ttk.Frame(server_window)
    button_row.pack(fill=X, padx=15, pady=(0, 15))

    ttk.Button(
        button_row,
        text="Select Server",
        width=BTN_WIDTH,
        command=select_server
    ).pack(side=LEFT, padx=(0, 8))

    ttk.Button(
        button_row,
        text="Cancel",
        width=BTN_WIDTH,
        command=server_window.destroy
    ).pack(side=LEFT)

    center_window_over_parent(server_window, root, 450, 350)

def center_window_over_parent(window, parent, width=None, height=None):
    parent.update_idletasks()
    window.update_idletasks()

    window_width = width or window.winfo_width()
    window_height = height or window.winfo_height()

    parent_x = parent.winfo_rootx()
    parent_y = parent.winfo_rooty()
    parent_width = parent.winfo_width()
    parent_height = parent.winfo_height()

    x = parent_x + (parent_width - window_width) // 2
    y = parent_y + (parent_height - window_height) // 2

    window.geometry(f"{window_width}x{window_height}+{x}+{y}")

def configure_dialog_parents(parent):
    # Keep Tkinter alert and picker dialogs attached to the main app window.
    for dialog_name in ("showinfo", "showwarning", "showerror", "askyesno"):
        original_dialog = getattr(messagebox, dialog_name)

        def messagebox_wrapper(*args, _original_dialog=original_dialog, **kwargs):
            kwargs.setdefault("parent", parent)
            return _original_dialog(*args, **kwargs)

        setattr(messagebox, dialog_name, messagebox_wrapper)

    for dialog_name in (
        "askopenfilename",
        "askopenfilenames",
        "askdirectory",
        "asksaveasfilename",
    ):
        original_dialog = getattr(filedialog, dialog_name)

        def filedialog_wrapper(*args, _original_dialog=original_dialog, **kwargs):
            kwargs.setdefault("parent", parent)
            return _original_dialog(*args, **kwargs)

        setattr(filedialog, dialog_name, filedialog_wrapper)

def configure_main_window():
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()

    window_width = min(max(int(screen_width * 0.92), 1000), 1400)
    window_height = min(max(int(screen_height * 0.88), 800), 1000)

    x = max(0, int((screen_width - window_width) / 2))
    y = max(0, int((screen_height - window_height) / 2))

    root.geometry(f"{window_width}x{window_height}+{x}+{y}")
    root.minsize(1000, 760)

def backup_mysql_database(host, user, password, database, output_file):
    command = [
        "mysqldump",
        "-h", host,
        "-u", user,
        f"-p{password}",
        database
    ]

    with open(output_file, "w", encoding="utf-8") as file:
        subprocess.run(command, stdout=file, stderr=subprocess.PIPE, text=True, check=True)

def test_sql_connection():
    server = sql_server_var.get().strip()

    if not server:
        messagebox.showwarning("Missing Server", "Enter SQL Server name.")
        return

    command = [
        "sqlcmd",
        "-S", server,
        "-E",
        "-C",
        "-Q", "SELECT @@VERSION"
    ]

    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
        messagebox.showinfo("SQL Connection", "SQL Server connection successful.")
    except Exception as e:
        messagebox.showerror("SQL Connection Failed", str(e))

def backup_single_sql_database(database, show_messages=True):
    server = sql_server_var.get().strip()
    destination = destination_var.get().strip()

    if not server or not database:
        if show_messages:
            messagebox.showwarning("Missing SQL Info", "Enter SQL Server and database name.")
        return False

    if not destination:
        if show_messages:
            messagebox.showwarning("No Destination", "Choose a backup destination first.")
        return False

    os.makedirs(destination, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_file = os.path.join(destination, f"{database}_{timestamp}.bak")

    query = f"BACKUP DATABASE [{database}] TO DISK = N'{output_file}' WITH INIT;"

    command = [
        "sqlcmd",
        "-S", server,
        "-E",
        "-C",
        "-Q", query
    ]

    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
        write_scheduler_status(f"SQL backup complete: {database}")
        return True

    except Exception as e:
        write_scheduler_status(f"SQL backup failed for {database}: {e}")

        if show_messages:
            messagebox.showerror("SQL Backup Failed", str(e))

        return False

def backup_sql_database():
    selected_databases = [
        db for db, var in sql_database_vars.items()
        if var.get()
    ]

    if not selected_databases:
        typed_db = sql_database_var.get().strip()
        if typed_db:
            selected_databases = [typed_db]

    if not selected_databases:
        messagebox.showwarning("No Database", "Select or enter at least one database.")
        return False

    success_count = 0

    for database in selected_databases:
        if backup_single_sql_database(database, show_messages=False):
            success_count += 1

    messagebox.showinfo(
        "SQL Backup Complete",
        f"Backed up {success_count} of {len(selected_databases)} database(s)."
    )

    return success_count > 0

def load_sql_databases():
    server = sql_server_var.get().strip()

    if not server:
        messagebox.showwarning("Missing Server", "Enter SQL Server name.")
        return

    command = [
        "sqlcmd",
        "-S", server,
        "-E",
        "-C",
        "-h", "-1",
        "-W",
        "-Q",
        "SET NOCOUNT ON; SELECT name FROM sys.databases WHERE database_id > 4 ORDER BY name;"
    ]

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)

        database_names = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]   

        if not database_names:
            messagebox.showinfo("SQL Databases", "No user databases found.")
            return

        show_database_selection_window(database_names)

    except Exception as e:
        messagebox.showerror("Load Databases Failed", str(e))

def show_database_selection_window(database_names):
    db_window = Toplevel(root)
    db_window.title("Select SQL Databases")
    db_window.configure(bg=BG)
    db_window.transient(root)
    db_window.grab_set()

    db_window.geometry("500x450")

    ttk.Label(
        db_window,
        text=f"Select databases to back up ({len(database_names)} found)",
        background=BG,
        foreground=TEXT,
        font=("Segoe UI", 12, "bold")
    ).pack(anchor="w", padx=15, pady=(15, 10))

    select_all_var = BooleanVar(value=False)

    list_frame = ttk.Frame(db_window, style="Card.TFrame", padding=10)
    list_frame.pack(fill=BOTH, expand=True, padx=15, pady=(0, 10))

    canvas = Canvas(list_frame, bg=CARD, highlightthickness=0)
    scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
    checkbox_frame = ttk.Frame(canvas, style="Card.TFrame")

    canvas.create_window((0, 0), window=checkbox_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    def scroll_database_list(event):
        if canvas.winfo_exists():
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def close_database_selection_window():
        canvas.unbind_all("<MouseWheel>")
        db_window.destroy()

    canvas.bind_all("<MouseWheel>", scroll_database_list)
    db_window.protocol("WM_DELETE_WINDOW", close_database_selection_window)

    checkbox_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    canvas.pack(side=LEFT, fill=BOTH, expand=True)

    if len(database_names) > 10:
        scrollbar.pack(side=RIGHT, fill=Y)
    else:
        scrollbar.pack_forget()

    temp_vars = {}

    def toggle_all():
        checked = select_all_var.get()
        for var in temp_vars.values():
            var.set(checked)

    ttk.Checkbutton(
        checkbox_frame,
        text="Select / Deselect All",
        variable=select_all_var,
        command=toggle_all
    ).pack(anchor="w", pady=(0, 8))

    ttk.Separator(checkbox_frame, orient="horizontal").pack(fill=X, pady=(0, 8))

    for db_name in database_names:
        existing_var = sql_database_vars.get(db_name)
        was_selected = existing_var.get() if existing_var else False

        var = BooleanVar(value=was_selected)
        temp_vars[db_name] = var

        ttk.Checkbutton(
            checkbox_frame,
            text=db_name,
            variable=var
        ).pack(anchor="w", pady=3)

    def save_selection():
        sql_database_vars.clear()

        selected_databases = []

        for db_name, var in temp_vars.items():
            saved_var = BooleanVar(value=var.get())
            sql_database_vars[db_name] = saved_var

            if var.get():
                selected_databases.append(db_name)

        sql_database_var.set(", ".join(selected_databases))
        if len(selected_databases) == 0:
            sql_selected_count_var.set("No databases selected")
        else:
            sql_selected_count_var.set(
                f"{len(selected_databases)} database(s) selected"
            )

        messagebox.showinfo(
            "Databases Selected",
            f"{len(selected_databases)} database(s) selected."
        )

        close_database_selection_window()

    button_row = ttk.Frame(db_window)
    button_row.pack(fill=X, padx=15, pady=(0, 15))

    ttk.Button(
        button_row,
        text="Save Selection",
        width=BTN_WIDTH,
        command=save_selection
    ).pack(side=LEFT, padx=(0, 8))

    ttk.Button(
        button_row,
        text="Cancel",
        width=BTN_WIDTH,
        command=close_database_selection_window
        ).pack(side=LEFT)

    center_window_over_parent(db_window, root, 500, 450)

# =========================================================
# Google Drive Backup
# =========================================================
    
def connect_google_drive():
    global google_drive_service

    credentials_path = resource_path("credentials.json")
    token_path = os.path.join(app_data_folder, "token.json")

    creds = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(
            token_path,
            GOOGLE_DRIVE_SCOPES
        )

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            credentials_path,
            GOOGLE_DRIVE_SCOPES
        )

        creds = flow.run_local_server(port=0)

        with open(token_path, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())

    google_drive_service = build(
        "drive",
        "v3",
        credentials=creds
    )

    google_drive_status_var.set("Google Drive Connected")

    messagebox.showinfo(
        "Google Drive",
        "Google Drive connected successfully."
    )    
    
def upload_file_to_google_drive(file_path):
    global google_drive_service

    if google_drive_service is None:
        connect_google_drive()

    folder_id = get_google_drive_backup_folder_id(file_path)

    file_metadata = {
        "name": os.path.basename(file_path),
        "parents": [folder_id]
    }

    media = MediaFileUpload(file_path, resumable=True)

    uploaded_file = google_drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id"
    ).execute()

    return uploaded_file.get("id")

def get_google_drive_backup_folder_id(file_path):
    root_folder_name = google_drive_folder_var.get().strip() or "Backup Compressor"
    root_folder_id = get_or_create_google_drive_folder(root_folder_name)

    extension = os.path.splitext(file_path)[1].replace(".", "").upper() or "OTHER"
    type_folder_id = get_or_create_google_drive_folder(extension, parent_id=root_folder_id)

    try:
        backup_time = datetime.fromtimestamp(os.path.getmtime(file_path))
    except OSError:
        backup_time = datetime.now()

    date_folder_id = get_or_create_google_drive_folder(
        backup_time.strftime("%Y-%m-%d"),
        parent_id=type_folder_id
    )
    time_folder_id = get_or_create_google_drive_folder(
        backup_time.strftime("%H-%M-%S"),
        parent_id=date_folder_id
    )

    return time_folder_id

def get_google_drive_account_email():
    global google_drive_service

    try:
        if google_drive_service is None:
            connect_google_drive()

        about = google_drive_service.about().get(
            fields="user(emailAddress)"
        ).execute()

        return about.get("user", {}).get("emailAddress", "Connected Google account")

    except Exception:
        return "Connected Google account"
        
def upload_test_to_google_drive():
    test_file = os.path.join(app_data_folder, "google_drive_test.txt")

    with open(test_file, "w", encoding="utf-8") as file:
        file.write("Google Drive test upload from Backup Compressor.")

    try:
        upload_file_to_google_drive(test_file)
        messagebox.showinfo("Google Drive", "Test file uploaded successfully.")
    except Exception as e:
        messagebox.showerror("Google Drive Upload Failed", str(e))

def upload_latest_backup_to_google_drive():
    destination = destination_var.get().strip()

    if not destination or not os.path.exists(destination):
        write_scheduler_status("Google Drive upload skipped: destination folder not found.")
        return False

    backup_files = [
        os.path.join(destination, file)
        for file in os.listdir(destination)
        if file.lower().endswith((".zip", ".7z", ".rar", ".bak"))
    ]

    if not backup_files:
        write_scheduler_status("Google Drive upload skipped: no backup files found.")
        return False

    latest_file = max(backup_files, key=os.path.getmtime)

    try:
        upload_file_to_google_drive(latest_file)
        write_scheduler_status(f"Google Drive upload complete: {os.path.basename(latest_file)}")
        return True
    except Exception as e:
        write_scheduler_status(f"Google Drive upload failed: {e}")
        return False
    
def disconnect_google_drive():
    global google_drive_service

    token_path = os.path.join(app_data_folder, "token.json")

    try:
        if os.path.exists(token_path):
            os.remove(token_path)

        google_drive_service = None
        google_drive_status_var.set("Not Connected")

        messagebox.showinfo(
            "Google Drive",
            "Google Drive account disconnected."
        )

    except Exception as e:
        messagebox.showerror(
            "Disconnect Failed",
            str(e)
        )

def refresh_google_drive_status():
    token_path = os.path.join(app_data_folder, "token.json")

    if os.path.exists(token_path):
        google_drive_status_var.set("Google Drive Connected")
    else:
        google_drive_status_var.set("Not Connected")

def toggle_run_on_startup():
    if run_on_startup_var.get():
        enable_run_on_startup()
    else:
        disable_run_on_startup()

    update_startup_checkbox_style()

def update_startup_checkbox_style():
    if "startup_checkbox" not in globals():
        return

    style_name = (
        "StartupEnabled.TCheckbutton"
        if run_on_startup_var.get()
        else "StartupDisabled.TCheckbutton"
    )
    startup_checkbox.configure(style=style_name)

def get_or_create_google_drive_folder(folder_name, parent_id=None):
    global google_drive_service

    escaped_folder_name = folder_name.replace("'", "\\'")
    query = (
        f"name='{escaped_folder_name}' "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )

    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = google_drive_service.files().list(
        q=query,
        fields="files(id,name)"
    ).execute()

    folders = results.get("files", [])

    if folders:
        return folders[0]["id"]

    folder_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder"
    }

    if parent_id:
        folder_metadata["parents"] = [parent_id]

    folder = google_drive_service.files().create(
        body=folder_metadata,
        fields="id"
    ).execute()

    return folder["id"]

# =========================================================
# Cloud Backup Scheduler
# =========================================================

def add_cloud_backup_items():
    selection_window = Toplevel(root)
    selection_window.title("Cloud Backup Selection")
    selection_window.geometry("420x140")
    selection_window.configure(bg=BG)

    selection_window.transient(root)
    selection_window.grab_set()
    selection_window.resizable(False, False)

    ttk.Label(
        selection_window,
        text="Select what to add to cloud backup:",
        background=BG,
        foreground=TEXT,
        font=("Segoe UI", 11, "bold")
    ).pack(anchor="w", padx=20, pady=(20, 15))

    button_row = ttk.Frame(
        selection_window,
        style="Card.TFrame"
    )
    button_row.pack(padx=20, pady=(0, 20))

    def select_files():
        files = filedialog.askopenfilenames(parent=selection_window)

        if files:
            cloud_selected_items.extend(files)

        update_cloud_selection_count()
        selection_window.destroy()

    def select_folder():
        folder = filedialog.askdirectory(parent=selection_window)

        if folder:
            cloud_selected_items.append(folder)

        update_cloud_selection_count()
        selection_window.destroy()

    ttk.Button(
        button_row,
        text="Files",
        width=BTN_WIDTH,
        command=select_files
    ).pack(side=LEFT, padx=(0, 8))

    ttk.Button(
        button_row,
        text="Folder",
        width=BTN_WIDTH,
        command=select_folder
    ).pack(side=LEFT, padx=(0, 8))

    ttk.Button(
        button_row,
        text="Cancel",
        width=BTN_WIDTH,
        command=selection_window.destroy
    ).pack(side=LEFT)

    center_window_over_parent(selection_window, root, 420, 140)

def add_cloud_backup_time():
    hour = cloud_hours_var.get()
    minute = cloud_minutes_var.get()
    ampm = cloud_ampm_var.get()

    backup_time = f"{int(hour)}:{minute} {ampm}"
    selected_cloud_days = [
        day for day, var in cloud_selected_days.items()
        if var.get()
    ]

    if not selected_cloud_days:
        messagebox.showwarning("No Days Selected", "Select at least one cloud backup day.")
        return

    cloud_scheduled_backup_times.append({
        "time": backup_time,
        "days": selected_cloud_days
    })
    update_cloud_schedule_list()
    save_app_settings()

def update_cloud_schedule_list():
    cloud_schedule_listbox.delete(0, END)

    for schedule in cloud_scheduled_backup_times:
        if isinstance(schedule, dict):
            backup_time = schedule.get("time", "")
            days = ", ".join(schedule.get("days", []))
            cloud_schedule_listbox.insert(END, f"{backup_time} | {days}")
        else:
            # Keep old saved time-only schedules readable.
            cloud_schedule_listbox.insert(END, schedule)

def remove_cloud_backup_time():
    selected = cloud_schedule_listbox.curselection()

    if not selected:
        messagebox.showwarning("No Time Selected", "Select a cloud backup time to remove.")
        return

    index = selected[0]
    cloud_scheduled_backup_times.pop(index)

    update_cloud_schedule_list()
    save_app_settings()

def start_cloud_scheduler():
    global cloud_scheduler_running

    if not cloud_scheduled_backup_times:
        messagebox.showwarning("No Cloud Schedule", "Add at least one cloud backup time first.")
        return

    cloud_scheduler_running = True
    cloud_status_var.set("Running")
    cloud_schedule_enabled_var.set(True)
    refresh_schedule_tray_icon()

    messagebox.showinfo(
        "Cloud Backup Scheduler Started",
        "Cloud backup scheduler is now running."
    )

def stop_cloud_scheduler():
    global cloud_scheduler_running

    cloud_scheduler_running = False
    cloud_status_var.set("Stopped")
    cloud_schedule_enabled_var.set(False)
    refresh_schedule_tray_icon()

def update_cloud_selection_count():
    cloud_selected_count_var.set(
        f"{len(cloud_selected_items)} cloud item(s) selected"
    )

def check_cloud_scheduled_backups():
    global cloud_last_run_time

    if cloud_scheduler_running:
        current_time = datetime.now().strftime("%I:%M %p").lstrip("0")
        today = datetime.now().strftime("%a")

        for schedule in cloud_scheduled_backup_times:
            if isinstance(schedule, dict):
                schedule_time = schedule.get("time")
                schedule_days = schedule.get("days", [])
                should_run = current_time == schedule_time and today in schedule_days
            else:
                # Legacy time-only schedules run on any selected cloud day.
                should_run = current_time == schedule and cloud_selected_days[today].get()

            if should_run and current_time != cloud_last_run_time:
                cloud_last_run_time = current_time
                cloud_status_var.set("Running Backup")
                update_tray_icon(CLOUD_SCHEDULE_COLOR)

                backup_thread = threading.Thread(target=run_cloud_backup_silent)
                backup_thread.daemon = True
                backup_thread.start()
                break

        else:
            cloud_status_var.set("Idle")
            refresh_schedule_tray_icon()

    root.after(60000, check_cloud_scheduled_backups)

# =========================================================
# Tkinter App Bootstrap
# =========================================================

root = Tk() 
google_drive_service = None
configure_dialog_parents(root)

selected_days = {
    "Mon": BooleanVar(value=True),
    "Tue": BooleanVar(value=True),
    "Wed": BooleanVar(value=True),
    "Thu": BooleanVar(value=True),
    "Fri": BooleanVar(value=True),
    "Sat": BooleanVar(value=True),
    "Sun": BooleanVar(value=True),
}

cloud_selected_days = {
    "Mon": BooleanVar(value=True),
    "Tue": BooleanVar(value=True),
    "Wed": BooleanVar(value=True),
    "Thu": BooleanVar(value=True),
    "Fri": BooleanVar(value=True),
    "Sat": BooleanVar(value=True),
    "Sun": BooleanVar(value=True),
}

root.iconbitmap(os.path.join(os.path.dirname(__file__), "app_icon.ico"))
icon_red = create_status_icon(STOPPED_COLOR)            # not running
icon_blue = create_status_icon(LOCAL_SCHEDULE_COLOR)    # local scheduler
icon_teal = create_status_icon(CLOUD_SCHEDULE_COLOR)    # cloud scheduler

root.title(f"{APP_NAME} v{APP_VERSION}")
configure_main_window()


# Tkinter variables shared by callbacks and UI widgets.
apply_modern_style()    
destination_var = StringVar()
format_var = StringVar(value="zip")
sql_server_var = StringVar(value=r".\SQLEXPRESS")
sql_database_var = StringVar(value="BackupCompressorTest")
sql_database_vars = {}
sql_include_scheduler_var = BooleanVar(value=False)
sql_selected_count_var = StringVar(value="0 databases selected")

google_drive_status_var = StringVar(value="Not Connected")
cloud_schedule_enabled_var = BooleanVar(value=False)
google_drive_folder_var = StringVar(value="My Drive")
cloud_selected_count_var = StringVar(value="0 cloud item(s) selected")
cloud_scheduled_backup_times = []

cloud_hours_var = StringVar(value="12")
cloud_minutes_var = StringVar(value="00")
cloud_ampm_var = StringVar(value="AM")
cloud_scheduler_running = False
cloud_last_run_time = None
cloud_status_var = StringVar(value="Stopped")

run_on_startup_var = BooleanVar(value=False)

schedule_time_var = StringVar()
scheduler_status_var = StringVar(value="Stopped")
schedule_name_var = StringVar(value="Local Backup")
schedule_description_var = StringVar(value="Files/Folders backup")

progress_var = DoubleVar(value=0)
status_var = StringVar(value="Ready")
summary_var = StringVar(value="Selected: 0 files | Total size: 0 B")
dashboard_progress_var = DoubleVar(value=0)
dashboard_status_var = StringVar(value="Ready")
dashboard_last_backup_var = StringVar(value="No backup events yet")
dashboard_schedule_var = StringVar(value="Stopped")
dashboard_cloud_var = StringVar(value="Stopped")
dashboard_google_var = StringVar(value="Not Connected")
dashboard_storage_var = StringVar(value="Destination not set")

# Main tab container.
notebook = ttk.Notebook(root)

notebook.pack_propagate(False)
notebook.configure(style="TNotebook")
notebook.pack(fill=BOTH, expand=True, padx=15, pady=15)

dashboard_tab = ttk.Frame(notebook, padding=20)
backup_tab = ttk.Frame(notebook, padding=20)
scheduler_tab = ttk.Frame(notebook, padding=20)
settings_tab = ttk.Frame(notebook, padding=20)
logs_tab = ttk.Frame(notebook, padding=20)

# Notebook tabs.
notebook.add(backup_tab, text="Backup")
notebook.add(scheduler_tab, text="Scheduler")
notebook.add(settings_tab, text="Cloud Backup")
notebook.add(dashboard_tab, text="Dashboard")
notebook.add(logs_tab, text="Logs")

# =========================================================
# Dashboard Tab
# =========================================================

dashboard_top_row = ttk.Frame(dashboard_tab)
dashboard_top_row.pack(fill=X, pady=(0, 10))
dashboard_top_row.columnconfigure(0, weight=1)
dashboard_top_row.columnconfigure(1, weight=1)
dashboard_top_row.columnconfigure(2, weight=1)

dashboard_status_card = ttk.Frame(dashboard_top_row, style="Card.TFrame", padding=15)
dashboard_status_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

ttk.Label(
    dashboard_status_card,
    text="Current Backup",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 10))

ttk.Label(
    dashboard_status_card,
    textvariable=dashboard_status_var,
    background=CARD,
    foreground="#57f287",
    font=("Segoe UI", 10, "bold")
).pack(anchor="w", pady=(0, 8))

ttk.Progressbar(
    dashboard_status_card,
    variable=dashboard_progress_var,
    maximum=100
).pack(fill=X)

dashboard_schedule_card = ttk.Frame(dashboard_top_row, style="Card.TFrame", padding=15)
dashboard_schedule_card.grid(row=0, column=1, sticky="nsew", padx=8)

ttk.Label(
    dashboard_schedule_card,
    text="Schedules Set",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 10))

ttk.Label(
    dashboard_schedule_card,
    textvariable=dashboard_schedule_var,
    background=CARD,
    foreground="#57f287"
).pack(anchor="w")

ttk.Label(
    dashboard_schedule_card,
    textvariable=dashboard_cloud_var,
    background=CARD,
    foreground="#57f287"
).pack(anchor="w", pady=(6, 0))

dashboard_cloud_card = ttk.Frame(dashboard_top_row, style="Card.TFrame", padding=15)
dashboard_cloud_card.grid(row=0, column=2, sticky="nsew", padx=(8, 0))

ttk.Label(
    dashboard_cloud_card,
    text="Storage and Cloud",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 10))

ttk.Label(
    dashboard_cloud_card,
    textvariable=dashboard_google_var,
    background=CARD,
    foreground="#57f287"
).pack(anchor="w")

ttk.Label(
    dashboard_cloud_card,
    textvariable=dashboard_storage_var,
    background=CARD,
    foreground=MUTED
).pack(anchor="w", pady=(6, 0))

dashboard_last_card = ttk.Frame(dashboard_tab, style="Card.TFrame", padding=15)
dashboard_last_card.pack(fill=X, pady=(0, 10))

ttk.Label(
    dashboard_last_card,
    text="Latest Event",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 8))

ttk.Label(
    dashboard_last_card,
    textvariable=dashboard_last_backup_var,
    background=CARD,
    foreground=MUTED
).pack(anchor="w")

dashboard_history_card = ttk.Frame(dashboard_tab, style="Card.TFrame", padding=15)
dashboard_history_card.pack(fill=BOTH, expand=True)

ttk.Label(
    dashboard_history_card,
    text="Backup History",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 10))

dashboard_history_frame = ttk.Frame(dashboard_history_card, style="Card.TFrame")
dashboard_history_frame.pack(fill=BOTH, expand=True)

dashboard_history = ttk.Treeview(
    dashboard_history_frame,
    columns=("time", "type", "status", "format", "file", "message", "path", "destination"),
    show="headings",
    height=12
)

for column, heading, width_value in (
    ("time", "Time", 145),
    ("type", "Type", 90),
    ("status", "Status", 90),
    ("format", "Format", 70),
    ("file", "File", 180),
    ("message", "Message", 360),
    ("path", "Path", 0),
    ("destination", "Destination", 0),
):
    dashboard_history.heading(column, text=heading)
    dashboard_history.column(column, width=width_value, anchor="w")

dashboard_history.column("path", width=0, stretch=False)
dashboard_history.column("destination", width=0, stretch=False)

dashboard_history.pack(side=LEFT, fill=BOTH, expand=True)
dashboard_history.bind("<Double-1>", open_selected_dashboard_backup)

dashboard_history_scrollbar = ttk.Scrollbar(
    dashboard_history_frame,
    orient="vertical",
    command=dashboard_history.yview
)
dashboard_history_scrollbar.pack(side=RIGHT, fill=Y)
dashboard_history.configure(yscrollcommand=dashboard_history_scrollbar.set)

dashboard_history_button_row = ttk.Frame(dashboard_history_card, style="Card.TFrame")
dashboard_history_button_row.pack(fill=X, pady=(10, 0))

btn_open_dashboard_backup = ttk.Button(
    dashboard_history_button_row,
    text="Open Location",
    width=BTN_WIDTH,
    command=open_selected_dashboard_backup
)
btn_open_dashboard_backup.pack(side=LEFT)
main_buttons.append(btn_open_dashboard_backup)

# =========================================================
# Logs Tab
# =========================================================

logs_card = ttk.Frame(logs_tab, style="Card.TFrame", padding=15)
logs_card.pack(fill=BOTH, expand=True)  

ttk.Label(
    logs_card,
    text="Backup Logs",
    background="#2d2d2d",
    foreground="#ffffff",
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 10))

log_text = Text(
    logs_card,
    wrap=WORD,
    bg="#1f1f1f",
    fg="#ffffff",
    insertbackground="#ffffff",
    font=("Consolas", 10),
    relief=FLAT
)
log_text.pack(side=LEFT, fill=BOTH, expand=True)
log_text.config(state=DISABLED)

log_scrollbar = Scrollbar(logs_card)
log_scrollbar.pack(side=RIGHT, fill=Y)

log_text.config(yscrollcommand=log_scrollbar.set)
log_scrollbar.config(command=log_text.yview)

logs_button_row = ttk.Frame(logs_tab)
logs_button_row.pack(fill=X, pady=(10, 0))

btn_view_log = ttk.Button(logs_button_row, text="Refresh Backup Logs", command=refresh_logs_tab)
btn_view_log.pack(side=LEFT)

main_buttons.append(btn_view_log)

# =========================================================
# Cloud Backup Tab: SQL Settings
# =========================================================

sql_card = ttk.Frame(settings_tab, style="Card.TFrame", padding=15)
sql_card.pack(fill=X, pady=10)

ttk.Label(
    sql_card,
    text="SQL Backup Settings",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

ttk.Label(sql_card, text="Server:", background=CARD, foreground=TEXT).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)

sql_server_entry = ttk.Entry(sql_card, textvariable=sql_server_var, width=30)
sql_server_entry.grid(row=1, column=1, sticky="w", pady=5)

ttk.Label(
    sql_card,
    textvariable=sql_selected_count_var,
    background=CARD,
    foreground="#57f287",
    font=("Segoe UI", 9)
).grid(
    row=5,
    column=0,
    columnspan=3,
    sticky="w",
    pady=(4, 2)
)

ttk.Label(
    sql_card,
    text="If finding SQL Servers takes too long, type the server name above and press Enter. Always test the connection.",
    background=CARD,
    foreground="#ffb86c",
    font=("Segoe UI", 9, "bold")
).grid(
    row=6,
    column=0,
    columnspan=3,
    sticky="w",
    pady=(0, 8)
)

sql_button_row = ttk.Frame(sql_card, style="Card.TFrame")
sql_button_row.grid(
    row=4,
    column=0,
    columnspan=3,
    sticky="w", 
    pady=5
)

btn_find_servers = ttk.Button(
    sql_button_row,
    text="Find SQL Servers",
    width=BTN_WIDTH,
    command=start_sql_server_discovery
)

btn_test_sql = ttk.Button(
    sql_button_row,
    text="Test Connection",
    width=BTN_WIDTH,
    command=test_sql_connection
)

btn_load_databases = ttk.Button(
    sql_button_row,
    text="Load Databases",
    width=BTN_WIDTH,
    command=load_sql_databases
)

btn_find_servers.pack(side=LEFT, padx=(0, 8))
btn_test_sql.pack(side=LEFT, padx=(0, 8))
btn_load_databases.pack(side=LEFT)

main_buttons.extend([
    btn_find_servers,
    btn_test_sql,
    btn_load_databases
])

# =========================================================
# Cloud Backup Tab: Cloud Schedule
# =========================================================

cloud_schedule_card = ttk.Frame(settings_tab, style="Card.TFrame", padding=15)
cloud_schedule_card.pack(fill=X, pady=10)

ttk.Label(
    cloud_schedule_card,
    text="Cloud Backup Scheduler",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))

cloud_time_row = ttk.Frame(cloud_schedule_card, style="Card.TFrame")
cloud_time_row.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 8))

ttk.Label(
    cloud_time_row,
    text="Cloud Backup Time:",
    background=CARD,
    foreground=TEXT
).pack(side=LEFT, padx=(0, 8))

ttk.Combobox(
    cloud_time_row,
    textvariable=cloud_hours_var,
    values=[f"{i:02d}" for i in range(1, 13)],
    width=4,
    state="readonly"
).pack(side=LEFT, padx=(0, 2))

ttk.Combobox(
    cloud_time_row,
    textvariable=cloud_minutes_var,
    values=[f"{i:02d}" for i in range(60)],
    width=4,
    state="readonly"
).pack(side=LEFT, padx=(0, 2))

ttk.Combobox(
    cloud_time_row,
    textvariable=cloud_ampm_var,
    values=["AM", "PM"],
    width=4,
    state="readonly"
).pack(side=LEFT)

cloud_days_frame = ttk.Frame(cloud_schedule_card, style="Card.TFrame")
cloud_days_frame.grid(row=2, column=0, columnspan=4, sticky="w", pady=(0, 8))

for i, (day, var) in enumerate(cloud_selected_days.items()):
    ttk.Checkbutton(cloud_days_frame, text=day, variable=var).grid(row=0, column=i, padx=3)

btn_add_cloud_time = ttk.Button(
    cloud_schedule_card,
    text="Add Time",
    width=BTN_WIDTH,
    command=add_cloud_backup_time
)
btn_add_cloud_time.grid(row=3, column=0, sticky="w", padx=(0, 8), pady=5)

btn_remove_cloud_time = ttk.Button(
    cloud_schedule_card,
    text="Remove Selected",
    width=BTN_WIDTH,
    command=remove_cloud_backup_time
)
btn_remove_cloud_time.grid(row=3, column=1, sticky="w", pady=5)

cloud_schedule_listbox = Listbox(
    cloud_schedule_card,
    height=6,
    width=75,
    bg="#1f1f1f",
    fg="#ffffff",
    selectbackground="#0078d4",
    selectforeground="#ffffff",
    font=("Segoe UI", 10),
    relief=FLAT
)
cloud_schedule_listbox.grid(row=1, column=4, rowspan=5, sticky="nw", padx=(20, 0))

btn_start_cloud_scheduler = ttk.Button(
    cloud_schedule_card,
    text="Start Schedule",
    width=BTN_WIDTH,
    command=start_cloud_scheduler,
    style="CompactAccent.TButton"
)
btn_start_cloud_scheduler.grid(row=5, column=0, sticky="w", pady=(10, 0))

btn_stop_cloud_scheduler = ttk.Button(
    cloud_schedule_card,
    text="Stop Schedule",
    width=BTN_WIDTH,
    command=stop_cloud_scheduler
)
btn_stop_cloud_scheduler.grid(row=5, column=1, sticky="w", padx=(8, 0), pady=(10, 0))

ttk.Label(
    cloud_schedule_card,
    textvariable=cloud_status_var,
    background=CARD,
    foreground="#57f287",
    font=("Segoe UI", 9, "bold")
).grid(row=5, column=2, sticky="w", padx=(10, 0), pady=(10, 0))

# =========================================================
# Cloud Backup Tab: Google Drive Settings
# =========================================================

google_drive_card = ttk.Frame(settings_tab, style="Card.TFrame", padding=15)
google_drive_card.pack(fill=X, pady=10)
google_drive_card.columnconfigure(4, weight=1)

ttk.Label(
    google_drive_card,
    text="Google Drive Backup Settings",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

btn_connect_google = ttk.Button(
    google_drive_card,
    text="Google Drive",
    width=BTN_WIDTH,
    command=connect_google_drive
)
btn_connect_google.grid(
    row=1,
    column=0,
    sticky="w",
    padx=(0, 8),
    pady=5
)   
btn_test_google = ttk.Button(
    google_drive_card,
    text="Upload Test",
    width=BTN_WIDTH,
    command=upload_test_to_google_drive
)
btn_test_google.grid(
    row=1,
    column=1,
    sticky="w",
    padx=(0, 8),
    pady=5
)

btn_disconnect_google = ttk.Button(
    google_drive_card,
    text="Disconnect",
    width=BTN_WIDTH,
    command=disconnect_google_drive
)

btn_select_cloud_items = ttk.Button(
    google_drive_card,
    text="Select Cloud Items",
    width=BTN_WIDTH,
    command=add_cloud_backup_items
)

btn_select_cloud_items.grid(row=1, column=2, sticky="w", padx=(0, 8), pady=5)

btn_disconnect_google.grid(row=1, column=3, sticky="w", padx=(0, 8), pady=5)

ttk.Label(
    google_drive_card,
    textvariable=cloud_selected_count_var,
    background=CARD,
    foreground="#57f287",
    font=("Segoe UI", 9, "bold")
).grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))

# Google Drive connection status.
ttk.Label(
    google_drive_card,
    textvariable=google_drive_status_var,
    background=CARD,
    foreground="#57f287",
    font=("Segoe UI", 10, "bold")
).grid(
    row=2,
    column=0,
    columnspan=3,
    sticky="w",
    pady=(8, 8)
)

# Folder name used when creating or finding the destination in Google Drive.
ttk.Entry(
    google_drive_card,
    textvariable=google_drive_folder_var,
    width=40
).grid(
    row=4,
    column=1,
    columnspan=2,
    sticky="w",
    padx=(8, 0),
    pady=(5, 5)
)
btn_start_cloud_schedule = ttk.Button(
    google_drive_card,
    text="Start Local + Cloud Schedule",
    width=BTN_WIDTH + 10,
    command=start_cloud_backup_schedule,
    style="Accent.TButton"
)
btn_start_cloud_schedule.grid(
    row=5,
    column=4,
    sticky="e",
    pady=(12, 0)
)
ttk.Label(
    google_drive_card,
    text="Cloud Folder:",
    background=CARD,
    foreground=TEXT
).grid(
    row=4,
    column=0,
    sticky="w",
    pady=(5, 5)
)

main_buttons.extend([
    btn_connect_google,
    btn_test_google,
    btn_select_cloud_items,
    btn_disconnect_google,
    btn_start_cloud_schedule
])


# =========================================================
# Backup Tab: File Selection
# =========================================================

files_card = ttk.Frame(backup_tab, style="Card.TFrame", padding=15)
files_card.pack(fill=BOTH, expand=True, pady=(0, 10))


ttk.Label(
    files_card,
    text="Selected Backup Items",
    background="#2d2d2d",
    foreground="#ffffff",
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 10))

button_row = ttk.Frame(files_card, style="Card.TFrame")
button_row.pack(fill=X, pady=(0, 10))

btn_add_files = ttk.Button(button_row, text="Add Files", command=add_files)
btn_add_files.pack(side=LEFT, padx=(0, 8))

btn_add_folder = ttk.Button(button_row, text="Add Folder", command=add_folder)
btn_add_folder.pack(side=LEFT, padx=(0, 8))

btn_clear_list = ttk.Button(button_row, text="Clear List", command=clear_list)
btn_clear_list.pack(side=LEFT, padx=(0, 8))


main_buttons.extend([
    btn_add_files,
    btn_add_folder,
    btn_clear_list
])

listbox_frame = Frame(files_card, bg="#2d2d2d")
listbox_frame.pack(fill=BOTH, expand=True)

listbox = Listbox(
    listbox_frame,
    bg="#1f1f1f",
    fg="#ffffff",
    selectbackground="#0078d4",
    selectforeground="#ffffff",
    font=("Segoe UI", 10),
    relief=FLAT,
    height=6
)
listbox.pack(side=LEFT, fill=BOTH, expand=True)

list_scrollbar = Scrollbar(listbox_frame)
list_scrollbar.pack(side=RIGHT, fill=Y)
listbox.config(yscrollcommand=list_scrollbar.set)
list_scrollbar.config(command=listbox.yview)    

# =========================================================
# Backup Tab: Destination and Format
# =========================================================

settings_card = ttk.Frame(backup_tab, style="Card.TFrame", padding=15)
settings_card.pack(fill=X, pady=10)


ttk.Label(
    files_card,
    textvariable=summary_var,
    background="#2d2d2d",
    foreground="#bdbdbd"
).pack(anchor="w", pady=(8, 0))

ttk.Label(
    settings_card,
    text="Backup Location",
    background="#2d2d2d",
    foreground="#ffffff",
    font=("Segoe UI", 12, "bold")
).grid(row=0, column=0, sticky="w", columnspan=4, pady=(0, 10))

ttk.Label(settings_card, text="Destination:", background="#2d2d2d", foreground="#ffffff").grid(row=1, column=0, sticky="w", padx=(0, 8))

destination_entry = ttk.Entry(settings_card, textvariable=destination_var)
destination_entry.grid(row=1, column=1, sticky="ew", padx=(0, 8))

btn_browse = ttk.Button(settings_card, text="Browse", command=choose_destination)
btn_browse.grid(row=1, column=2, sticky="ew")
main_buttons.append(btn_browse)
settings_card.columnconfigure(1, weight=1)

format_frame = ttk.Frame(settings_card, style="Card.TFrame")
format_frame.grid(row=2, column=0, columnspan=3, sticky="w", pady=(15, 0))

ttk.Label(format_frame, text="Format:", background="#2d2d2d", foreground="#ffffff").pack(side=LEFT, padx=(0, 10))
ttk.Radiobutton(format_frame, text="ZIP", variable=format_var, value="zip").pack(side=LEFT, padx=8)
ttk.Radiobutton(format_frame, text="7Z", variable=format_var, value="7z").pack(side=LEFT, padx=8)
ttk.Radiobutton(format_frame, text="RAR", variable=format_var, value="rar").pack(side=LEFT, padx=8)

startup_checkbox = ttk.Checkbutton(
    settings_card,
    text="Start Backup Compressor with Windows",
    variable=run_on_startup_var,
    command=toggle_run_on_startup,
    style="StartupDisabled.TCheckbutton"
)
startup_checkbox.grid(
    row=3,
    column=0,
    columnspan=3,
    sticky="w",
    pady=(15, 0)
)

# =========================================================
# Scheduler Tab
# =========================================================

scheduler_card = ttk.Frame(scheduler_tab, style="Card.TFrame", padding=15)
scheduler_card.pack(fill=X, pady=10)    

ttk.Label(
    scheduler_card,
    text="Backup Scheduler",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).grid(row=0, column=0, sticky="w", columnspan=4, pady=(0, 10)) 

hours_var = StringVar(value="12")
minutes_var = StringVar(value="00")
ampm_var = StringVar(value="AM")

time_row = ttk.Frame(scheduler_card, style="Card.TFrame")
time_row.grid(row=2, column=0, columnspan=4, sticky="w", pady=(0, 8))

ttk.Label(
    time_row,
    text="Backup Time:",
    background=CARD,
    foreground=TEXT
).pack(side=LEFT, padx=(0, 8))

hours_dropdown = ttk.Combobox(
    time_row,
    textvariable=hours_var,
    values=[f"{i:02d}" for i in range(1, 13)],
    width=4,
    state="readonly"
)
hours_dropdown.pack(side=LEFT, padx=(0, 2))

minutes_dropdown = ttk.Combobox(
    time_row,
    textvariable=minutes_var,
    values=[f"{i:02d}" for i in range(60)],
    width=4,
    state="readonly"
)
minutes_dropdown.pack(side=LEFT, padx=(0, 2))

ampm_dropdown = ttk.Combobox(
    time_row,
    textvariable=ampm_var,
    values=["AM", "PM"],
    width=4,
    state="readonly"
)
ampm_dropdown.pack(side=LEFT)

btn_add_time = ttk.Button(scheduler_card, text="Add Time/Day", command=add_backup_time, width=BTN_WIDTH)
btn_remove_time = ttk.Button(scheduler_card, text="Remove Selected", command=remove_selected_time, width=BTN_WIDTH)
btn_add_time.grid(row=4, column=0, sticky="w", padx=(0, 8), pady=5)
btn_remove_time.grid(row=4, column=1, sticky="w", pady=5)

schedule_listbox = Listbox(
    scheduler_card,
    height=10,
    width=75,
    bg="#1f1f1f",
    fg="#ffffff",
    selectbackground="#0078d4",
    selectforeground="#ffffff",
    font=("Segoe UI", 10),
    relief=FLAT
)
schedule_listbox.grid(
    row=1,
    column=4,
    rowspan=8,
    sticky="nw",
    padx=(20, 0),
    pady=(0, 10)
)

btn_start_scheduler = ttk.Button(
    scheduler_card,
    text="Start Schedule",
    width=BTN_WIDTH,
    command=start_scheduler,
    style="CompactAccent.TButton"
)
btn_stop_scheduler = ttk.Button(
    scheduler_card,
    text="Stop Schedule",
    width=BTN_WIDTH,
    command=stop_scheduler
)

status_label = ttk.Label(
    scheduler_card,
    textvariable=scheduler_status_var,
    image=icon_red,
    compound="left",
    background=CARD,
    foreground=TEXT
)

btn_start_scheduler.grid(row=8, column=0, sticky="w", pady=(10, 0))
btn_stop_scheduler.grid(row=8, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
status_label.grid(row=8, column=2, sticky="w", padx=(10, 0), pady=(10, 0))

main_buttons.extend([
    btn_add_time,
    btn_remove_time,
    btn_start_scheduler,
    btn_stop_scheduler
])

days_frame = ttk.Frame(scheduler_card, style="Card.TFrame")
days_frame.grid(row=3, column=0, columnspan=4, sticky="w", pady=(0, 8))

for i, (day, var) in enumerate(selected_days.items()):
    ttk.Checkbutton(days_frame, text=day, variable=var).grid(row=0, column=i, padx=3)

schedule_details_frame = ttk.Frame(scheduler_card, style="Card.TFrame")
schedule_details_frame.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 8))

ttk.Label(
    schedule_details_frame,
    text="Schedule Name:",
    background=CARD,
    foreground=TEXT
).pack(side=LEFT, padx=(0, 8))

ttk.Entry(
    schedule_details_frame,
    textvariable=schedule_name_var,
    width=20
).pack(side=LEFT)

scheduler_card.columnconfigure(0, weight=0)
scheduler_card.columnconfigure(1, weight=0)
scheduler_card.columnconfigure(2, weight=0)
scheduler_card.columnconfigure(3, weight=0)
scheduler_card.columnconfigure(4, weight=1)
for i in range(10):
    scheduler_card.rowconfigure(i, weight=0)


# =========================================================
# Backup Tab: Actions
# =========================================================
    
action_row = ttk.Frame(backup_tab)
action_row.pack(fill=X, pady=15)
action_row.columnconfigure(0, weight=1) 

btn_start_backup = ttk.Button(
    action_row,
    text="Start Backup",
    command=start_backup_async,
    style="Accent.TButton"
)
btn_start_backup.grid(row=0, column=0, sticky="e")

main_buttons.extend([   
    btn_start_backup
])

load_app_settings()
refresh_google_drive_status()
refresh_logs_tab()
update_startup_checkbox_style()
update_backup_summary()
root.protocol("WM_DELETE_WINDOW", on_app_close)

setup_tray_icon()
refresh_dashboard()
check_scheduled_backups()
check_cloud_scheduled_backups()
process_progress_queue()
process_ui_action_queue()
process_backup_result_queue()

root.mainloop()
