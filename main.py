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
import webbrowser
import shutil
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from PIL import Image as PILImage, ImageDraw
from datetime import datetime, timedelta
from tkinter import *
from tkinter import filedialog, messagebox
from tkinter import font as tkfont
from tkinter import ttk

# =========================================================
# Constants
# =========================================================
APP_NAME = "Backup Compressor"
APP_VERSION = "3.1.1"
TEST_MODE = "--test-mode" in sys.argv
ALWAYS_RUN_AS_ADMIN = not TEST_MODE
SINGLE_INSTANCE_MUTEX_NAME = r"Local\BackupCompressorSingleInstance"
if TEST_MODE:
    SINGLE_INSTANCE_MUTEX_NAME += "Test"
ERROR_ALREADY_EXISTS = 183

THEME_MODE = "dark"
THEMES = {
    "dark": {
        "bg": "#111827",
        "card": "#1b2535",
        "card_dark": "#243247",
        "text": "#f1f5f9",
        "muted": "#a9b8cd",
        "hover": "#30425c",
        "shell": "#28374c"
    }
}
BG = THEMES[THEME_MODE]["bg"]
CARD = THEMES[THEME_MODE]["card"]
CARD_DARK = THEMES[THEME_MODE]["card_dark"]
TEXT = THEMES[THEME_MODE]["text"]
MUTED = THEMES[THEME_MODE]["muted"]
ACCENT = "#5676e8"
ACCENT_HOVER = "#4563cb"
SOFT_BORDER = "#34465f"
INPUT_BG = "#131d2c"
TOGGLE_ENABLED = "#2e7d32"
TOGGLE_ENABLED_HOVER = "#256629"
TOGGLE_DISABLED = "#34465f"
TOGGLE_DISABLED_HOVER = "#425873"
LOCAL_SCHEDULE_COLOR = "#3498db"
CLOUD_SCHEDULE_COLOR = "#1abc9c"
STOPPED_COLOR = "#ffffff"
BTN_WIDTH = 16
SCHEDULER_POLL_INTERVAL_MS = 15000
DASHBOARD_CURRENT_BACKUP_LIMIT = 5
GOOGLE_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
OFFICE_FILE_EXTENSIONS = (
    ".doc", ".docx", ".docm",
    ".xls", ".xlsx", ".xlsm", ".xlsb",
    ".ppt", ".pptx", ".pptm",
    ".pdf", ".txt", ".rtf", ".csv",
    ".png", ".jpeg",
    ".one", ".pst", ".ost"
)
OFFICE_BACKUP_FOLDER_NAMES = ("Desktop", "Documents", "Downloads")

# Main color and sizing constants are kept together so UI sections share one
# visual language without repeating magic values.

# =========================================================
# Runtime State
# =========================================================
main_buttons = []
main_tab_buttons = []
scheduler_subtab_buttons = []
cloud_subtab_buttons = []
selected_items = []
scheduler_selected_items = []
auto_selected_items = []
cloud_selected_items = []
cloud_schedule_edit_index = None
sql_schedule_edit_index = None
scheduled_backup_times = []
schedule_destination_starts = {}
scheduler_running = False
last_run_time = None
schedule_last_run_times = {}
scheduler_section_order = ["manual", "auto", "scheduled"]
scheduler_section_widgets = {}
cloud_section_order = ["cloud_items", "google_drive", "cloud_schedule", "sql"]
cloud_section_widgets = {}
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
single_instance_mutex = None
notification_history = []
notification_unread_count = 0
active_toast = None
active_toast_after = None
toast_animation_after = None
notification_drawer_text = None
notifications_previous_tab = None
instructions_previous_tab = None

TAB_INSTRUCTIONS = {
    "Backup": (
        "Create a local/offline compressed backup now\n\n"
        "1. Select Add Files or Add Folder. The selected paths appear in the item list.\n\n"
        "2. Choose a destination folder. Add an optional description to make the archive name easier to identify.\n\n"
        "3. Select ZIP, 7Z, or RAR. ZIP works without an external archiver; 7Z and RAR require their supported tools.\n\n"
        "4. Select Start Backup. Progress and completion information appears in the app and notification drawer.\n\n"
        "Use Clear List to start a new selection. Removing items from this list never deletes the original files."
    ),
    "Schedule": (
        "Create local/offline backups now or later\n\n"
        "Choose files, folders, or the Office file preset, then choose a destination.\n\n"
        "Minutes: Enter an interval. No time or weekday is required; backups repeat while scheduling is enabled.\n\n"
        "Daily: Select weekdays. On a selected day, the backup starts when scheduling is enabled and runs once that day.\n\n"
        "Weekly and Bi-weekly: Select weekdays and a backup time.\n\n"
        "Monthly: Select a day of the month and a backup time.\n\n"
        "Add the schedule, review it in Scheduled Backups, then enable scheduling. Notifications show when it was enabled and the next expected backup."
    ),
    "Cloud Backup": (
        "Create online backups in Google Drive\n\n"
        "Cloud tab:\n"
        "1. Connect Google Drive and confirm the destination folder name.\n"
        "2. Add files or folders to the cloud item list.\n"
        "3. Upload directly to Google Drive immediately or configure the Cloud Backup Schedule. This workflow does not run the local Backup tab.\n\n"
        "SQL tab:\n"
        "1. Enter or discover the SQL Server name and test the connection.\n"
        "2. Load and select databases.\n"
        "3. Choose a temporary SQL staging folder and back up to Google Drive. The staging copy is removed after a successful upload.\n"
        "4. Use SQL Backup Schedule for unattended Google Drive database backups.\n\n"
        "Scrollable sections ensure all controls remain available in smaller windows."
    ),
    "Dashboard": (
        "Monitor backup activity\n\n"
        "Current Backup shows live progress and status. Schedule cards show whether local and cloud scheduling are active.\n\n"
        "Recent Backup History lists completed, failed, and informational events. Select an entry when you need to review its result or destination.\n\n"
        "The dashboard refreshes as backups and schedules run."
    ),
    "Logs": (
        "Review detailed backup records\n\n"
        "Use Logs to inspect local, cloud, SQL, and schedule activity. This is the best place to investigate skipped or failed backups.\n\n"
        "Refresh the view after an external change, and use the available log actions to open or clear records when needed.\n\n"
        "Notifications provide short summaries; Logs retain the detailed operational history."
    )
}


# App data lives in %APPDATA%\Backup Compressor so settings and logs persist
# even when the app is packaged into an executable.
app_data_folder = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), APP_NAME)
if TEST_MODE:
    app_data_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test-data")
os.makedirs(app_data_folder, exist_ok=True)

settings_file = os.path.join(app_data_folder, "app_settings.json")

logs_folder = os.path.join(app_data_folder, "logs")
os.makedirs(logs_folder, exist_ok=True)

backup_log_file = os.path.join(logs_folder, "backup_log.txt")
backup_events_file = os.path.join(logs_folder, "backup_events.jsonl")

# =========================================================
# File Paths
# =========================================================

def is_running_as_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def relaunch_as_admin_if_needed():
    if not ALWAYS_RUN_AS_ADMIN or is_running_as_admin():
        return

    executable = sys.executable
    if getattr(sys, "frozen", False):
        arguments = subprocess.list2cmdline(sys.argv[1:])
    else:
        arguments = subprocess.list2cmdline([os.path.abspath(sys.argv[0])] + sys.argv[1:])

    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            executable,
            arguments,
            os.getcwd(),
            1
        )

        if result > 32:
            sys.exit(0)

    except Exception:
        pass

    sys.exit(1)

def ensure_single_instance():
    global single_instance_mutex

    if os.name != "nt":
        return

    single_instance_mutex = ctypes.windll.kernel32.CreateMutexW(
        None,
        False,
        SINGLE_INSTANCE_MUTEX_NAME
    )

    if not single_instance_mutex:
        return

    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        ctypes.windll.user32.MessageBoxW(
            None,
            "Backup Compressor is already running.",
            APP_NAME,
            0x40
        )
        sys.exit(0)

def hide_console_window():
    if TEST_MODE or os.name != "nt":
        return

    try:
        console_window = ctypes.windll.kernel32.GetConsoleWindow()

        if console_window:
            ctypes.windll.user32.ShowWindow(console_window, 0)

    except Exception:
        pass

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
    add_unique_backup_items(files)
    update_list()

def add_folder():
    folder = filedialog.askdirectory(parent=root)
    if folder:
        add_unique_backup_items([folder])
        update_list()

def add_unique_backup_items(items):
    """Keep one entry per Windows path while preserving selection order."""
    existing = {os.path.normcase(os.path.abspath(path)) for path in selected_items}
    for path in items:
        key = os.path.normcase(os.path.abspath(path))
        if key not in existing:
            selected_items.append(path)
            existing.add(key)

def remove_selected_backup_items():
    for index in reversed(listbox.curselection()):
        del selected_items[index]
    update_list()

def update_list():
    listbox.delete(0, END)

    for item in selected_items:
        listbox.insert(END, item)

    item_count = len(selected_items)
    new_height = max(5, min(item_count, 8))
    listbox.config(height=new_height)

    update_backup_summary()

def choose_destination():
    folder = filedialog.askdirectory(parent=root)
    if folder:
        destination_var.set(folder)

def choose_manual_backup_destination():
    folder = filedialog.askdirectory(parent=root)
    if folder:
        manual_backup_destination_var.set(folder)
        record_schedule_destination_start(folder)
        save_app_settings()

def choose_auto_backup_destination():
    folder = filedialog.askdirectory(parent=root)
    if folder:
        auto_backup_destination_var.set(folder)
        record_schedule_destination_start(folder)
        save_app_settings()

def record_schedule_destination_start(destination):
    """Retain the original local start time for each selected backup root."""
    key = os.path.normcase(os.path.abspath(destination))
    started_at = schedule_destination_starts.setdefault(key, datetime.now().isoformat(timespec="seconds"))
    refresh_schedule_destination_caption()
    return started_at

def refresh_schedule_destination_caption(*args):
    destination = auto_backup_destination_var.get().strip()
    if not destination:
        schedule_destination_caption_var.set("Choose a root folder. Backups are grouped into YYYY-MM folders.")
        return
    key = os.path.normcase(os.path.abspath(destination))
    started_at = schedule_destination_starts.get(key)
    start_text = started_at.replace("T", " ") if started_at else "Recorded when you add the schedule"
    schedule_destination_caption_var.set(
        f"Start: {start_text}\nThis month's folder: {get_monthly_schedule_destination(destination)}"
    )

def get_monthly_schedule_destination(destination, backup_time=None):
    if not destination:
        return ""
    backup_time = backup_time or datetime.now()
    return os.path.join(destination, backup_time.strftime("%Y-%m"))

def choose_sql_staging_destination():
    folder = filedialog.askdirectory(parent=root)
    if folder:
        sql_staging_destination_var.set(folder)
        save_app_settings()

def add_scheduler_files():
    files = filedialog.askopenfilenames(parent=root)
    if files:
        scheduler_selected_items.extend(files)
        update_scheduler_item_list()
        save_app_settings()

def add_scheduler_folder():
    folder = filedialog.askdirectory(parent=root)
    if folder:
        scheduler_selected_items.append(folder)
        update_scheduler_item_list()
        save_app_settings()

def remove_selected_scheduler_item():
    selected = scheduler_items_listbox.curselection()

    if not selected:
        messagebox.showwarning("No Item Selected", "Select a schedule item to remove.")
        return

    for index in reversed(selected):
        scheduler_selected_items.pop(index)

    update_scheduler_item_list()
    save_app_settings()

def remove_selected_schedule_tab_item():
    selected = schedule_items_listbox.curselection()

    if not selected:
        messagebox.showwarning("No Item Selected", "Select a schedule item to remove.")
        return

    for index in reversed(selected):
        scheduler_selected_items.pop(index)

    update_scheduler_item_list()
    save_app_settings()

def clear_scheduler_items():
    scheduler_selected_items.clear()
    update_scheduler_item_list()
    save_app_settings()

def update_scheduler_item_list():
    listboxes = []

    if "scheduler_items_listbox" in globals():
        listboxes.append(scheduler_items_listbox)

    if "schedule_items_listbox" in globals():
        listboxes.append(schedule_items_listbox)

    for listbox_widget in listboxes:
        listbox_widget.delete(0, END)

        for item in scheduler_selected_items:
            listbox_widget.insert(END, item)

    file_count, folder_count = get_selected_item_counts(scheduler_selected_items)
    scheduler_selected_count_var.set(
        f"{file_count} file(s), {folder_count} folder(s) selected"
    )

def add_auto_scheduler_files():
    files = filedialog.askopenfilenames(parent=root)
    if files:
        auto_selected_items.extend(files)
        update_auto_scheduler_item_list()
        save_app_settings()

def add_auto_scheduler_folder():
    folder = filedialog.askdirectory(parent=root)
    if folder:
        auto_selected_items.append(folder)
        update_auto_scheduler_item_list()
        save_app_settings()

def remove_selected_auto_scheduler_item():
    selected = auto_scheduler_items_listbox.curselection()

    if not selected:
        messagebox.showwarning("No Item Selected", "Select an auto backup item to remove.")
        return

    for index in reversed(selected):
        auto_selected_items.pop(index)

    update_auto_scheduler_item_list()
    save_app_settings()

def clear_auto_scheduler_items():
    auto_selected_items.clear()
    update_auto_scheduler_item_list()
    save_app_settings()

def update_auto_scheduler_item_list():
    if "auto_scheduler_items_listbox" not in globals():
        return

    auto_scheduler_items_listbox.delete(0, END)

    for item in auto_selected_items:
        auto_scheduler_items_listbox.insert(END, item)

    file_count, folder_count = get_selected_item_counts(auto_selected_items)
    auto_selected_count_var.set(
        f"{file_count} file(s), {folder_count} folder(s) selected"
    )

def get_safe_filename_part(value):
    safe_value = "".join(
        char if char.isalnum() or char in (" ", "-", "_") else "_"
        for char in value.strip()
    )
    return "_".join(safe_value.split())

def get_safe_drive_folder_name(value):
    safe_value = "".join(
        "_" if char in ("\\", "/") or ord(char) < 32 else char
        for char in value.strip()
    )
    return " ".join(safe_value.split())

def format_backup_month_day_time(backup_time):
    return (
        f"{backup_time.strftime('%B')} "
        f"{backup_time.day} "
        f"{backup_time.strftime('%I-%M %p').lstrip('0')}"
    )

def get_cloud_backup_group_folder_name(backup_time):
    description = ""

    if "cloud_backup_description_var" in globals():
        description = get_safe_drive_folder_name(cloud_backup_description_var.get())

    return description or format_backup_month_day_time(backup_time)

def get_backup_name(extension, description=""):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    description = get_safe_filename_part(description)

    prefix = f"{description}_" if description else ""
    return f"{prefix}backup_{timestamp}.{extension}"

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
    skipped_files = []

    if total_files == 0:
        raise ValueError("No files found to back up.")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for item in items:
            if os.path.isfile(item):
                try:
                    zipf.write(item, os.path.basename(item))
                except (OSError, PermissionError) as e:
                    skipped_files.append((item, str(e)))

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

                        try:
                            zipf.write(full_path, arcname)
                        except (OSError, PermissionError) as e:
                            skipped_files.append((full_path, str(e)))

                        processed += 1
                        set_progress(
                            (processed / total_files) * 100,
                            f"Compressing: {file}"
                        )

    if skipped_files:
        write_backup_event(
            "local",
            "info",
            f"ZIP backup skipped {len(skipped_files)} file(s) that could not be read.",
            output_file=output_path,
            destination=os.path.dirname(output_path),
            backup_format="ZIP"
        )
        notify_tray(
            "Backup Completed With Skips",
            f"{len(skipped_files)} file(s) were in use or unreadable and were skipped."
        )
        set_progress(100, f"ZIP backup complete. Skipped {len(skipped_files)} file(s).")
    else:
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
        "-ssw",
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
        "-dh",
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
    summary_var.set(
        f"{total_files} files selected  ·  {format_size(total_size)}"
        if selected_items else "No files selected — use Add Files or Add Folder to begin."
    )

def queue_ui_action(callback, *args, **kwargs):
    if threading.get_ident() == ui_thread_id:
        callback(*args, **kwargs)
    else:
        ui_action_queue.put((callback, args, kwargs))

def update_notification_button():
    if "btn_notifications" in globals():
        suffix = f" ({notification_unread_count})" if notification_unread_count else ""
        btn_notifications.config(text=f"Notifications{suffix}")

def dismiss_active_toast(animated=True):
    global active_toast, active_toast_after, toast_animation_after

    if active_toast_after is not None:
        root.after_cancel(active_toast_after)
        active_toast_after = None
    if toast_animation_after is not None:
        root.after_cancel(toast_animation_after)
        toast_animation_after = None

    toast = active_toast
    if toast is None or not toast.winfo_exists():
        active_toast = None
        return

    if not animated:
        toast.destroy()
        active_toast = None
        return

    start_y = int(toast.place_info().get("y", -22))
    end_y = toast.winfo_reqheight() + 12
    steps = 10

    def slide_out(step=0):
        global active_toast, toast_animation_after
        if not toast.winfo_exists():
            return
        if step >= steps:
            toast.destroy()
            if active_toast is toast:
                active_toast = None
            toast_animation_after = None
            return
        progress = (step + 1) / steps
        eased = progress * progress
        toast.place_configure(y=round(start_y + (end_y - start_y) * eased))
        toast_animation_after = root.after(18, slide_out, step + 1)

    slide_out()

def show_app_notification(title, message, duration_ms=6500):
    global notification_unread_count, active_toast, active_toast_after, toast_animation_after

    timestamp = datetime.now()
    notification_history.insert(0, {
        "title": title,
        "message": message,
        "timestamp": timestamp
    })
    del notification_history[100:]
    notification_unread_count += 1
    update_notification_button()
    refresh_notification_drawer()
    dismiss_active_toast(animated=False)

    toast = ttk.Frame(root, style="Card.TFrame", padding=12, relief="solid", borderwidth=1)
    toast.columnconfigure(0, weight=1)
    ttk.Label(toast, text=title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Button(
        toast,
        text="×",
        width=2,
        command=dismiss_active_toast,
        style="ToastClose.TButton"
    ).grid(row=0, column=1, sticky="ne", padx=(8, 0))
    ttk.Label(
        toast,
        text=message,
        style="Muted.TLabel",
        wraplength=360,
        justify=LEFT
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 8))
    ttk.Button(toast, text="View notifications", command=show_notification_history).grid(
        row=2, column=0, columnspan=2, sticky="e"
    )
    toast.update_idletasks()
    start_y = toast.winfo_reqheight() + 12
    toast.place(relx=0, rely=1, x=22, y=start_y, anchor="sw", width=400)
    toast.lift()
    active_toast = toast

    steps = 12

    def slide_in(step=0):
        global toast_animation_after
        if active_toast is not toast or not toast.winfo_exists():
            return
        if step >= steps:
            toast.place_configure(y=-22)
            toast_animation_after = None
            return
        progress = (step + 1) / steps
        eased = 1 - (1 - progress) ** 3
        toast.place_configure(y=round(start_y + (-22 - start_y) * eased))
        toast_animation_after = root.after(16, slide_in, step + 1)

    slide_in()
    active_toast_after = root.after(duration_ms, dismiss_active_toast)

def refresh_notification_drawer():
    if notification_drawer_text is None or not notification_drawer_text.winfo_exists():
        return

    notification_drawer_text.config(state=NORMAL)
    for child in notification_drawer_text.winfo_children():
        child.destroy()
    notification_drawer_text.delete("1.0", END)

    if notification_history:
        for item in notification_history:
            card = ttk.Frame(notification_drawer_text, style="Card.TFrame", padding=5)
            card.columnconfigure(0, weight=1)
            ttk.Label(card, text=item["title"], style="CardTitle.TLabel").grid(
                row=0, column=0, sticky="w"
            )
            ttk.Button(
                card,
                text="×",
                width=2,
                command=lambda entry=item: remove_notification(entry)
            ).grid(row=0, column=1, sticky="ne", padx=(3, 0))
            ttk.Label(
                card,
                text=item["timestamp"].strftime("%b %d, %I:%M %p"),
                style="Muted.TLabel",
                wraplength=470,
                justify=LEFT
            ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
            ttk.Label(
                card,
                text=item["message"],
                style="Muted.TLabel",
                wraplength=470,
                justify=LEFT
            ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(3, 0))
            card.bind("<MouseWheel>", scroll_notification_drawer)
            for child in card.winfo_children():
                child.bind("<MouseWheel>", scroll_notification_drawer, add="+")
            notification_drawer_text.window_create(END, window=card, stretch=True)
            notification_drawer_text.insert(END, "\n")
    else:
        notification_drawer_text.insert(END, "No notifications yet.")

    notification_drawer_text.config(state=DISABLED)
    notification_drawer_text.yview_moveto(0)

def scroll_notification_drawer(event):
    notification_drawer_text.yview_scroll(int(-1 * (event.delta / 120)), "units")
    return "break"

def show_notification_history():
    global notification_unread_count, notifications_previous_tab

    notification_unread_count = 0
    update_notification_button()
    dismiss_active_toast()
    selected = notebook.select()
    if selected != str(notifications_tab):
        notifications_previous_tab = selected
    refresh_notification_drawer()
    notebook.select(notifications_tab)
    update_main_tab_buttons()

def close_notification_history():
    target = notifications_previous_tab or str(backup_tab)
    notebook.select(target)
    update_main_tab_buttons()

def remove_notification(item):
    global notification_unread_count

    if item in notification_history:
        notification_history.remove(item)
    notification_unread_count = min(notification_unread_count, len(notification_history))
    update_notification_button()
    refresh_notification_drawer()

def refresh_instructions(event=None):
    if "instructions_text" not in globals():
        return

    topic = instructions_topic_var.get()
    instructions_text.config(state=NORMAL)
    instructions_text.delete("1.0", END)
    instructions_text.insert(END, TAB_INSTRUCTIONS.get(topic, TAB_INSTRUCTIONS["Backup"]))
    instructions_text.config(state=DISABLED)
    instructions_text.yview_moveto(0)

def open_instructions():
    global instructions_previous_tab

    selected = notebook.select()
    if selected != str(instructions_tab):
        instructions_previous_tab = selected

    tab_topics = {
        str(backup_tab): "Backup",
        str(scheduler_tab): "Schedule",
        str(cloud_tab): "Cloud Backup",
        str(dashboard_tab): "Dashboard",
        str(settings_tab): "Logs"
    }
    instructions_topic_var.set(tab_topics.get(selected, instructions_topic_var.get() or "Backup"))
    refresh_instructions()
    notebook.select(instructions_tab)
    update_main_tab_buttons()

def close_instructions():
    target = instructions_previous_tab or str(backup_tab)
    notebook.select(target)
    update_main_tab_buttons()

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

def notify_tray(title, message):
    if not tray_icon:
        return

    try:
        tray_icon.notify(message, title)
    except Exception:
        pass

def notify_files_in_use_backup_continuing(locked_files):
    if not locked_files:
        return

    file_preview = ", ".join(os.path.basename(file_path) for file_path in locked_files[:5])
    extra_count = len(locked_files) - 5

    if extra_count > 0:
        file_preview = f"{file_preview}, and {extra_count} more"

    notify_tray(
        "Files In Use",
        f"Backup will continue. Busy files may be skipped: {file_preview}"
    )

def configure_auto_hide_scrollbar(widget, scrollbar, orient=VERTICAL, geometry="pack", show_options=None):
    show_options = show_options or {}
    visible = {"value": False}

    def show_scrollbar():
        if visible["value"]:
            return

        if geometry == "grid":
            scrollbar.grid(**show_options)
        else:
            scrollbar.pack(**show_options)

        visible["value"] = True

    def hide_scrollbar():
        if not visible["value"]:
            return

        if geometry == "grid":
            scrollbar.grid_remove()
        else:
            scrollbar.pack_forget()

        visible["value"] = False

    def scrollbar_set(first, last):
        scrollbar.set(first, last)

        try:
            first_value = float(first)
            last_value = float(last)
        except (TypeError, ValueError):
            show_scrollbar()
            return

        if first_value <= 0 and last_value >= 1:
            hide_scrollbar()
        else:
            show_scrollbar()

    if orient == HORIZONTAL:
        widget.configure(xscrollcommand=scrollbar_set)
        scrollbar.configure(command=widget.xview)
        view_callback = widget.xview
    else:
        widget.configure(yscrollcommand=scrollbar_set)
        scrollbar.configure(command=widget.yview)
        view_callback = widget.yview

    hide_scrollbar()
    widget.after_idle(lambda: scrollbar_set(*view_callback()))

# =========================================================
# App Settings, Profiles, and Logs
# =========================================================

def save_app_settings():
    # Persist lightweight preferences only; profile files keep their own item lists.
    settings = {
        "app_version": APP_VERSION,
        "show_cloud_tab": show_cloud_tab_var.get(),
        "last_profile": active_profile_path,
        "destination": destination_var.get(),
        "backup_description": backup_description_var.get(),
        "manual_backup_destination": manual_backup_destination_var.get(),
        "auto_backup_destination": auto_backup_destination_var.get(),
        "schedule_destination_starts": schedule_destination_starts,
        "format": format_var.get(),
        "scheduler_selected_items": scheduler_selected_items,
        "auto_selected_items": auto_selected_items,
        "schedule_times": scheduled_backup_times,
        "cloud_schedule_times": cloud_scheduled_backup_times,
        "sql_schedule_times": sql_scheduled_backup_times,
        "cloud_selected_items": cloud_selected_items,
        "google_drive_folder": google_drive_folder_var.get(),
        "cloud_backup_description": cloud_backup_description_var.get(),
        "sql_server": sql_server_var.get(),
        "sql_database": sql_database_var.get(),
        "sql_staging_destination": sql_staging_destination_var.get(),
        "sql_selected_databases": get_selected_sql_databases(),
        "sql_include_scheduler": sql_include_scheduler_var.get(),
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

        show_cloud_tab_var.set(bool(settings.get("show_cloud_tab", False)))
        settings_were_reset = reset_old_settings_after_update(settings)
        schedule_destination_starts.update(settings.get("schedule_destination_starts", {}))

        destination_var.set(settings.get("destination", ""))
        backup_description_var.set(settings.get("backup_description", ""))
        manual_backup_destination_var.set(
            settings.get("manual_backup_destination", settings.get("destination", ""))
        )
        auto_backup_destination_var.set(
            settings.get("auto_backup_destination", settings.get("destination", ""))
        )
        format_var.set(settings.get("format", "zip"))
        sql_server_var.set(settings.get("sql_server", r".\SQLEXPRESS"))
        sql_database_var.set(settings.get("sql_database", "BackupCompressorTest"))
        sql_staging_destination_var.set(
            settings.get("sql_staging_destination", os.path.join(app_data_folder, "sql_staging"))
        )
        restore_sql_selected_databases(settings.get("sql_selected_databases", []))
        sql_include_scheduler_var.set(settings.get("sql_include_scheduler", False))
        google_drive_folder_var.set(settings.get("google_drive_folder", "My Drive"))
        cloud_backup_description_var.set(settings.get("cloud_backup_description", ""))

        scheduled_backup_times.clear()
        scheduled_backup_times.extend(settings.get("schedule_times", []))
        update_schedule_list()

        scheduler_selected_items.clear()
        scheduler_selected_items.extend(settings.get("scheduler_selected_items", []))
        update_scheduler_item_list()

        auto_selected_items.clear()
        auto_selected_items.extend(settings.get("auto_selected_items", []))
        update_auto_scheduler_item_list()

        cloud_scheduled_backup_times.clear()
        cloud_scheduled_backup_times.extend(settings.get("cloud_schedule_times", []))
        update_cloud_schedule_list()

        sql_scheduled_backup_times.clear()
        sql_scheduled_backup_times.extend(settings.get("sql_schedule_times", []))
        update_sql_schedule_list()

        cloud_selected_items.clear()
        cloud_selected_items.extend(settings.get("cloud_selected_items", []))
        update_cloud_selection_count()

        active_profile_path = settings.get("last_profile")

        if settings_were_reset:
            save_app_settings()

    except Exception:
        pass

def reset_old_settings_after_update(settings):
    saved_version = settings.get("app_version")

    if saved_version == APP_VERSION:
        return False

    backup_settings_before_update(settings, saved_version)
    settings["app_version"] = APP_VERSION
    settings.setdefault("last_profile", None)
    settings.setdefault("destination", "")
    settings.setdefault("backup_description", "")
    settings.setdefault("manual_backup_destination", settings.get("destination", ""))
    settings.setdefault("auto_backup_destination", settings.get("destination", ""))
    settings.setdefault("format", "zip")
    settings.setdefault("scheduler_selected_items", [])
    settings.setdefault("auto_selected_items", [])
    settings.setdefault("schedule_times", [])
    settings.setdefault("cloud_schedule_times", [])
    settings.setdefault("sql_schedule_times", [])
    settings.setdefault("cloud_selected_items", [])
    settings.setdefault("google_drive_folder", "My Drive")
    settings.setdefault("cloud_backup_description", "")
    settings.setdefault("sql_server", r".\SQLEXPRESS")
    settings.setdefault("sql_database", "BackupCompressorTest")
    settings.setdefault("sql_staging_destination", os.path.join(app_data_folder, "sql_staging"))
    settings.setdefault("sql_selected_databases", [])
    settings.setdefault("sql_include_scheduler", False)

    return True

def backup_settings_before_update(settings, saved_version):
    settings_backup = {
        "backup_created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "from_app_version": saved_version or "unknown",
        "to_app_version": APP_VERSION,
        "settings": dict(settings)
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(
        app_data_folder,
        f"settings_backup_{saved_version or 'unknown'}_to_{APP_VERSION}_{timestamp}.json"
    )

    with open(backup_file, "w", encoding="utf-8") as file:
        json.dump(settings_backup, file, indent=4)

def on_app_close():
    save_app_settings()

    if app_should_exit or TEST_MODE:
        root.destroy()
    else:
        hide_window()

def perform_backup(items, destination, format_choice, event_type="local", backup_description=""):
    set_progress(0, "Starting backup...")

    total_files, total_size = get_file_count_and_size(items)
    set_progress(0, f"Backing up {total_files} files | {format_size(total_size)}")

    if format_choice == "zip":
        output = os.path.join(destination, get_backup_name("zip", backup_description))
        create_zip(output, items)

    elif format_choice == "7z":
        output = os.path.join(destination, get_backup_name("7z", backup_description))
        create_7z(output, items)

    elif format_choice == "rar":
        output = os.path.join(destination, get_backup_name("rar", backup_description))
        create_rar(output, items)

    else:
        raise ValueError("Unknown backup format selected.")

    write_backup_log(destination, output, format_choice, items, event_type=event_type)
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
    backup_description = backup_description_var.get().strip() if show_messages else ""

    locked_files = get_locked_files(items)

    if locked_files:
        notify_files_in_use_backup_continuing(locked_files)
        write_backup_event(
            "local",
            "info",
            f"Backup continuing with {len(locked_files)} file(s) in use.",
            destination=destination,
            backup_format=format_choice.upper()
        )

    try:
        set_ui_busy(True)
        if show_messages:
            show_compression_progress_window()

        output = perform_backup(
            items,
            destination,
            format_choice,
            backup_description=backup_description
        )
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

def write_backup_log(destination, output_file, format_choice, items=None, event_type="local"):
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
        event_type,
        "completed",
        f"{format_choice.upper()} backup completed",
        output_file=output_file,
        destination=destination,
        backup_format=format_choice.upper()
    )
    queue_ui_action(refresh_logs_tab)

def write_cloud_backup_log(status, local_file, message, drive_file_id=""):
    destination = google_drive_folder_var.get().strip() or "Backup Compressor"
    backup_format = os.path.splitext(local_file)[1].replace(".", "").upper() or "CLOUD"

    with open(backup_log_file, "a", encoding="utf-8") as log:
        log.write("====================================\n")
        log.write(f"Cloud Backup Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"Status: {status.upper()}\n")
        log.write(f"Local File: {local_file}\n")
        log.write(f"Google Drive Folder: {destination}\n")

        if drive_file_id:
            log.write(f"Google Drive File ID: {drive_file_id}\n")

        log.write(f"Message: {message}\n\n")

    write_backup_event(
        "cloud",
        status,
        message,
        output_file=local_file,
        destination=destination,
        backup_format=backup_format
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

    if limit is None:
        return events

    return events[-limit:]

def save_backup_events(events):
    with open(backup_events_file, "w", encoding="utf-8") as event_file:
        for event in events:
            event_file.write(json.dumps(event) + "\n")

def is_dashboard_backup_history_event(event):
    status = event.get("status", "")

    if status not in ("completed", "failed"):
        return False

    if event.get("type") == "scheduler":
        return False

    return bool(event.get("file") or event.get("format") or event.get("destination"))

def event_matches_dashboard_values(event, values):
    return (
        event.get("time", "") == values[0]
        and event.get("type", "") == values[1]
        and event.get("status", "") == values[2]
        and event.get("format", "") == values[3]
        and os.path.basename(event.get("file", "")) == values[4]
        and event.get("message", "") == values[5]
        and event.get("file", "") == values[6]
        and event.get("destination", "") == values[7]
    )

def get_dashboard_event_key_from_values(values):
    return tuple(str(value) for value in values)

def get_dashboard_event_key(event):
    return (
        event.get("time", ""),
        event.get("type", ""),
        event.get("status", ""),
        event.get("format", ""),
        os.path.basename(event.get("file", "")),
        event.get("message", ""),
        event.get("file", ""),
        event.get("destination", "")
    )

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
        "backup_description": backup_description_var.get(),
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
    backup_description_var.set(profile_data.get("backup_description", ""))
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
        bg=INPUT_BG,
        fg=TEXT,
        insertbackground=TEXT,
        font=("Consolas", 10),
        relief=FLAT
    )
    text_area.pack(side=LEFT, expand=True, fill=BOTH, padx=(10, 0), pady=10)

    scrollbar = Scrollbar(log_window)
    configure_auto_hide_scrollbar(
        text_area,
        scrollbar,
        VERTICAL,
        "pack",
        {"side": RIGHT, "fill": Y, "padx": (0, 10), "pady": 10}
    )

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

def create_page_header(parent, title, description):
    header = ttk.Frame(parent)
    header.pack(fill=X, pady=(0, 16))
    ttk.Label(header, text=title, font=("Segoe UI", 22, "bold")).pack(anchor="w")
    ttk.Label(header, text=description, foreground=MUTED,
              wraplength=560).pack(anchor="w", pady=(4, 0))
    return header


def apply_modern_style():

    style = ttk.Style()
    style.theme_use("clam")

    root.configure(bg=BG)
    root.option_add("*Listbox.highlightBackground", SOFT_BORDER)
    root.option_add("*Listbox.highlightColor", ACCENT)
    root.option_add("*Listbox.borderWidth", 0)
    style.configure("Horizontal.TProgressbar", troughcolor=INPUT_BG,
                    background=ACCENT, bordercolor=INPUT_BG,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)

    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD, relief="flat")
    style.configure("CardBody.TFrame", background=CARD, relief="flat")
    style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
    style.configure(
        "Muted.TLabel",
        background=CARD,
        foreground=MUTED,
        font=("Segoe UI", 9)
    )
    style.configure(
        "ReadableCaption.TLabel",
        background=CARD,
        foreground=TEXT,
        font=("Segoe UI", 9)
    )
    style.configure(
        "WarningCaption.TLabel",
        background=BG,
        foreground="#ff6b6b",
        font=("Segoe UI", 9, "bold")
    )
    style.configure(
        "CardTitle.TLabel",
        background=CARD,
        foreground=TEXT,
        font=("Segoe UI", 12, "bold")
    )
    style.configure(
        "ScheduleBadge.TLabel",
        background=CARD_DARK,
        foreground=MUTED,
        font=("Segoe UI", 9, "bold"),
        padding=(10, 4)
    )

    style.configure(
    "TButton",
    font=("Segoe UI", 10),
    padding=(14, 8),
    background=CARD_DARK,
    foreground=TEXT,
    bordercolor=SOFT_BORDER,
    lightcolor=SOFT_BORDER,
    darkcolor=SOFT_BORDER,
    focuscolor=CARD_DARK,
    borderwidth=1,
    relief="solid"
)

    style.map(
        "TButton",
        background=[
            ("disabled", CARD_DARK),
            ("pressed", THEMES[THEME_MODE]["hover"]),
            ("active", THEMES[THEME_MODE]["hover"])
        ],
        foreground=[
            ("disabled", MUTED),
            ("active", TEXT)
        ],
        bordercolor=[
            ("disabled", THEMES[THEME_MODE]["shell"]),
            ("active", MUTED)
        ],
        lightcolor=[
            ("disabled", THEMES[THEME_MODE]["shell"]),
            ("active", MUTED)
        ],
        darkcolor=[
            ("disabled", THEMES[THEME_MODE]["shell"]),
            ("active", MUTED)
        ]
    )

    style.configure(
        "ToastClose.TButton",
        background=CARD,
        foreground="#ff6b6b",
        font=("Segoe UI", 12, "bold"),
        padding=(3, 0),
        bordercolor=CARD,
        lightcolor=CARD,
        darkcolor=CARD,
        focuscolor=CARD,
        borderwidth=1,
        relief="flat"
    )
    style.map(
        "ToastClose.TButton",
        background=[("pressed", CARD), ("active", CARD)],
        foreground=[("pressed", "#ff3b3b"), ("active", "#ff8a8a")],
        bordercolor=[("pressed", CARD), ("active", CARD)],
        lightcolor=[("pressed", CARD), ("active", CARD)],
        darkcolor=[("pressed", CARD), ("active", CARD)]
    )

    style.configure(
    "Accent.TButton",
    background=CARD_DARK,
    foreground=TEXT,
    font=("Segoe UI", 11, "bold"),
    padding=(16, 10),
    bordercolor=SOFT_BORDER,
    lightcolor=SOFT_BORDER,
    darkcolor=SOFT_BORDER,
    focuscolor=CARD_DARK,
    borderwidth=1,
    relief="solid"
)

    style.map(
        "Accent.TButton",
        background=[
            ("pressed", THEMES[THEME_MODE]["hover"]),
            ("active", THEMES[THEME_MODE]["hover"])
        ],
        foreground=[("active", TEXT)],
        bordercolor=[("active", MUTED)],
        lightcolor=[("active", MUTED)],
        darkcolor=[("active", MUTED)]
    )

    style.configure(
    "CompactAccent.TButton",
    background=CARD_DARK,
    foreground=TEXT,
    font=("Segoe UI", 10),
    padding=(14, 8),
    bordercolor=SOFT_BORDER,
    lightcolor=SOFT_BORDER,
    darkcolor=SOFT_BORDER,
    focuscolor=CARD_DARK,
    borderwidth=1,
    relief="solid"
)

    style.map(
        "CompactAccent.TButton",
        background=[
            ("pressed", THEMES[THEME_MODE]["hover"]),
            ("active", THEMES[THEME_MODE]["hover"])
        ],
        foreground=[("active", TEXT)],
        bordercolor=[("active", MUTED)],
        lightcolor=[("active", MUTED)],
        darkcolor=[("active", MUTED)]
    )

    style.configure("TRadiobutton", background=CARD, foreground=TEXT, font=("Segoe UI", 10))
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
    style.configure(
        "TCheckbutton",
        background=CARD,
        foreground=TEXT,
        font=("Segoe UI", 10),
        padding=(6, 4),
        focuscolor=CARD
    )
    style.map(
        "TCheckbutton",
        background=[
            ("active", CARD),
            ("selected", CARD)
        ],
        foreground=[
            ("active", TEXT),
            ("selected", TEXT)
        ]
    )
    style.configure(
        "TEntry",
        fieldbackground=INPUT_BG,
        foreground=TEXT,
        bordercolor=SOFT_BORDER,
        lightcolor=SOFT_BORDER,
        darkcolor=SOFT_BORDER,
        insertcolor=TEXT,
        padding=(8, 6)
    )
    style.configure(
        "TCombobox",
        fieldbackground=INPUT_BG,
        background=CARD_DARK,
        foreground=TEXT,
        arrowcolor=TEXT,
        bordercolor=SOFT_BORDER,
        lightcolor=SOFT_BORDER,
        darkcolor=SOFT_BORDER,
        selectbackground=INPUT_BG,
        selectforeground=TEXT,
        padding=(6, 5)
    )
    style.map(
        "TCombobox",
        fieldbackground=[
            ("readonly", INPUT_BG),
            ("active", INPUT_BG)
        ],
        background=[
            ("readonly", CARD_DARK),
            ("active", THEMES[THEME_MODE]["hover"])
        ],
        foreground=[
            ("readonly", TEXT),
            ("active", TEXT)
        ],
        arrowcolor=[
            ("readonly", TEXT),
            ("active", TEXT)
        ],
        bordercolor=[
            ("active", MUTED),
            ("readonly", SOFT_BORDER)
        ]
    )
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
        background=BG,
        borderwidth=0,
        relief="flat"
    )

    style.configure(
        "Main.TNotebook",
        background=BG,
        borderwidth=0,
        relief="flat",
        tabposition="wn"
    )

    style.configure(
        "Content.TNotebook",
        background=CARD,
        bordercolor=BG,
        lightcolor=BG,
        darkcolor=BG,
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

    style.configure(
        "Main.TNotebook.Tab",
        background=CARD_DARK,
        foreground=MUTED,
        padding=(16, 12),
        borderwidth=0,
        relief="flat"
)

    style.map(
        "TNotebook.Tab",
        background=[
            ("selected", CARD),
            ("active", THEMES[THEME_MODE]["hover"])
        ],
        foreground=[
            ("selected", TEXT),
            ("active", TEXT)
    ]
)

    style.map(
        "Main.TNotebook.Tab",
        background=[
            ("selected", CARD),
            ("active", THEMES[THEME_MODE]["hover"])
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

    style.layout("Main.TNotebook.Tab", [
        ("Notebook.tab", {
            "sticky": "nswe",
            "children": [
                ("Notebook.padding", {
                    "children": [
                        ("Notebook.label", {"sticky": "w"})
                    ]
                })
            ]
        })
    ])

    style.layout("Content.TNotebook.Tab", [])

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
    backup_description = backup_description_var.get().strip()
    set_ui_busy(True)
    show_compression_progress_window()
    set_progress(0, "Starting backup...")

    backup_thread = threading.Thread(
        target=run_interactive_backup_worker,
        args=(items, destination, format_choice, backup_description),
        daemon=True
    )
    backup_thread.start()

def run_interactive_backup_worker(items, destination, format_choice, backup_description):
    try:
        if not os.path.exists(destination):
            os.makedirs(destination)

        locked_files = get_locked_files(items)

        if locked_files:
            notify_files_in_use_backup_continuing(locked_files)
            write_backup_event(
                "local",
                "info",
                f"Backup continuing with {len(locked_files)} file(s) in use.",
                destination=destination,
                backup_format=format_choice.upper()
            )

        output = perform_backup(
            items,
            destination,
            format_choice,
            backup_description=backup_description
        )

        backup_result_queue.put({
            "success": True,
            "output": output,
            "locked_files": locked_files,
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

def get_office_backup_roots():
    home = os.path.expanduser("~")
    possible_roots = []

    for folder_name in OFFICE_BACKUP_FOLDER_NAMES:
        possible_roots.append(os.path.join(home, folder_name))

    for env_name in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        one_drive_root = os.getenv(env_name)

        if one_drive_root:
            for folder_name in ("Desktop", "Documents"):
                possible_roots.append(os.path.join(one_drive_root, folder_name))

    roots = []
    seen = set()

    for path in possible_roots:
        normalized = os.path.normcase(os.path.abspath(path))

        if normalized not in seen and os.path.isdir(path):
            seen.add(normalized)
            roots.append(path)

    return roots

def get_office_backup_files():
    files_to_backup = []
    seen = set()

    for root_path in get_office_backup_roots():
        for root_dir, dirs, files in os.walk(root_path):
            dirs[:] = [
                folder for folder in dirs
                if not folder.startswith(".") and folder.lower() not in ("appdata", "node_modules")
            ]

            for file_name in files:
                if not file_name.lower().endswith(OFFICE_FILE_EXTENSIONS):
                    continue

                full_path = os.path.join(root_dir, file_name)
                normalized = os.path.normcase(os.path.abspath(full_path))

                if normalized not in seen and os.path.isfile(full_path):
                    seen.add(normalized)
                    files_to_backup.append(full_path)

    return files_to_backup

def get_schedule_source_items(schedule):
    if isinstance(schedule, dict) and schedule.get("source") == "office_files":
        return get_office_backup_files()

    if isinstance(schedule, dict) and "items" in schedule:
        return list(schedule.get("items") or [])

    return list(selected_items)

def get_schedule_destination(schedule=None):
    if isinstance(schedule, dict):
        scheduled_destination = str(schedule.get("destination") or "").strip()

        if scheduled_destination:
            return scheduled_destination

        if schedule.get("mode") == "interval":
            auto_destination = auto_backup_destination_var.get().strip()

            if auto_destination:
                return auto_destination

        manual_destination = manual_backup_destination_var.get().strip()

        if manual_destination:
            return manual_destination

        return ""

    return destination_var.get().strip()

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

def format_dashboard_current_backups(display_events):
    if not display_events:
        return "No backup files found."

    lines = []

    for event in display_events:
        file_path = event.get("file", "")
        destination = event.get("destination", "")
        file_name = os.path.basename(file_path) if file_path else event.get("format", "Backup")
        location = destination or os.path.dirname(file_path) or "Location not recorded"
        lines.append(f"{file_name} | {location}")

    return "\n".join(lines)

def has_any_schedules_set():
    return bool(scheduled_backup_times or cloud_scheduled_backup_times or sql_scheduled_backup_times)

def format_google_connection_status():
    if google_drive_status_var.get() == "Google Drive Connected":
        return "Google: Connected"

    return "Google: Not Connected"

def format_sql_connection_status():
    if sql_connection_status_var.get() == "SQL Connected":
        return "SQL: Connected"

    return "SQL: Not Connected"

def refresh_dashboard(schedule_next=True):
    global dashboard_current_backup_events

    dashboard_schedule_var.set(scheduler_status_var.get())
    dashboard_cloud_var.set(cloud_status_var.get())
    dashboard_google_var.set(format_google_connection_status())
    dashboard_sql_var.set(format_sql_connection_status())
    update_dashboard_card_layout()

    selected_keys = {
        get_dashboard_event_key_from_values(dashboard_history.item(item_id, "values"))
        for item_id in dashboard_history.selection()
    }

    if selected_keys and schedule_next:
        root.after(1000, refresh_dashboard)
        return

    events = [
        event for event in load_backup_events(limit=None)
        if is_dashboard_backup_history_event(event)
    ]

    dashboard_current_backup_events = list(reversed(events))[:DASHBOARD_CURRENT_BACKUP_LIMIT]
    dashboard_status_var.set(format_dashboard_current_backups(dashboard_current_backup_events))

    if events:
        last_event = events[-1]
        dashboard_last_backup_var.set(
            f"{last_event.get('time', '')} | {last_event.get('type', '')} | {last_event.get('status', '')}"
        )
    else:
        dashboard_last_backup_var.set("No backup events yet")

    dashboard_history.delete(*dashboard_history.get_children())

    for event in reversed(events):
        item_id = dashboard_history.insert(
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

        if get_dashboard_event_key(event) in selected_keys:
            dashboard_history.selection_add(item_id)

    if schedule_next:
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

def open_dashboard_backup_event_location(backup_event):
    file_path = backup_event.get("file", "")
    destination = backup_event.get("destination", "")

    if file_path and os.path.exists(file_path):
        open_folder_for_path(file_path)
        return

    open_folder_for_path(destination or file_path)

def open_clicked_dashboard_current_backup(event=None):
    if not dashboard_current_backup_events:
        messagebox.showwarning("No Backup Available", "No backup file location is available yet.")
        return "break"

    line_index = 0

    if event is not None:
        try:
            label_font = tkfont.Font(font=event.widget.cget("font"))
            line_height = max(label_font.metrics("linespace"), 1)
            line_index = max(0, min(event.y // line_height, len(dashboard_current_backup_events) - 1))
        except Exception:
            line_index = 0

    open_dashboard_backup_event_location(dashboard_current_backup_events[line_index])
    return "break"

def delete_selected_dashboard_history():
    selected = dashboard_history.selection()

    if not selected:
        messagebox.showwarning("No Backup Selected", "Select one or more backup history items to delete.")
        return

    selected_values = [
        tuple(str(value) for value in dashboard_history.item(item_id, "values"))
        for item_id in selected
    ]

    if not messagebox.askyesno(
        "Delete Backup History",
        f"Remove {len(selected_values)} selected backup history item(s)?\n\nThis removes the history entries only, not backup files."
    ):
        return

    events = load_backup_events(limit=None)
    remaining_events = []
    pending_deletes = list(selected_values)

    for event in events:
        delete_index = None

        for index, values in enumerate(pending_deletes):
            if is_dashboard_backup_history_event(event) and event_matches_dashboard_values(event, values):
                delete_index = index
                break

        if delete_index is None:
            remaining_events.append(event)
        else:
            pending_deletes.pop(delete_index)

    save_backup_events(remaining_events)
    refresh_logs_tab()
    refresh_dashboard(schedule_next=False)

def run_scheduled_file_backup(schedule=None):
    items = get_schedule_source_items(schedule)
    source_name = "office files" if isinstance(schedule, dict) and schedule.get("source") == "office_files" else "selected files"

    if not items:
        write_scheduler_status(f"Scheduled backup skipped: no {source_name} found.")
        return False

    destination = get_schedule_destination(schedule)

    if not destination:
        write_scheduler_status("Scheduled backup skipped: no destination selected.")
        return False

    locked_files = get_locked_files(items)

    if locked_files:
        notify_files_in_use_backup_continuing(locked_files)
        write_scheduler_status(
            f"Scheduled backup continuing with {len(locked_files)} file(s) in use."
        )

    try:
        set_ui_busy(True)
        destination = get_monthly_schedule_destination(destination)
        os.makedirs(destination, exist_ok=True)
        output = perform_backup(items, destination, format_var.get(), event_type="scheduled")
        queue_ui_action(save_app_settings)

        if tray_icon:
            tray_icon.notify(
                f"Backup completed: {os.path.basename(output)}",
                "Backup Compressor"
            )

        return True

    except Exception as e:
        write_scheduler_status(f"Scheduled backup failed: {e}")
        write_backup_event(
            "scheduled",
            "failed",
            str(e),
            destination=destination,
            backup_format=format_var.get().upper()
        )
        return False

    finally:
        set_ui_busy(False)
        queue_ui_action(update_scheduler_indicator_after_backup)

def run_backup_silent(schedule=None):
    return run_scheduled_file_backup(schedule)

def run_cloud_backup_silent():
    if not cloud_selected_items:
        write_scheduler_status("Cloud backup skipped: no cloud items selected.")
        return False

    write_scheduler_status("Cloud backup started for selected files and folders.")
    return upload_cloud_selected_source_files_to_google_drive() > 0

def upload_backup_files_to_google_drive(backup_files):
    if not backup_files:
        write_scheduler_status("Cloud upload skipped: no files found.")
        return 0

    write_scheduler_status(
        f"Cloud schedule enabled. Uploading {len(backup_files)} file(s) to Google Drive."
    )

    backup_time = datetime.now()

    upload_count = 0

    for file_path in backup_files:
        try:
            drive_file_id = upload_file_to_google_drive(file_path, backup_time=backup_time)
            message = f"Google Drive upload complete: {os.path.basename(file_path)}"
            write_scheduler_status(
                message
            )
            write_cloud_backup_log(
                "completed",
                file_path,
                message,
                drive_file_id
            )
            upload_count += 1
        except Exception as e:
            message = f"Google Drive upload failed for {os.path.basename(file_path)}: {e}"
            write_scheduler_status(
                message
            )
            write_cloud_backup_log(
                "failed",
                file_path,
                message
            )

    return upload_count

def get_cloud_selected_source_files():
    files_to_upload = []

    for item in cloud_selected_items:
        if os.path.isfile(item):
            files_to_upload.append(item)
        elif os.path.isdir(item):
            for root_dir, dirs, files in os.walk(item):
                for file_name in files:
                    files_to_upload.append(os.path.join(root_dir, file_name))

    return files_to_upload

def upload_cloud_selected_source_files_to_google_drive():
    source_files = get_cloud_selected_source_files()

    if not source_files:
        write_scheduler_status("Cloud upload skipped: no selected source files found.")
        return 0

    write_scheduler_status(
        f"Cloud schedule uploading {len(source_files)} selected source file(s) to Google Drive."
    )
    return upload_backup_files_to_google_drive(source_files)

def update_cloud_upload_button_state(*args):
    if "btn_upload_cloud_items" not in globals():
        return

    missing_requirements = []

    if google_drive_status_var.get() != "Google Drive Connected":
        missing_requirements.append("connect Google Drive")

    if not google_drive_folder_var.get().strip():
        missing_requirements.append("enter a File Name")

    if not cloud_selected_items:
        missing_requirements.append("add Cloud items")

    ready_to_upload = (
        google_drive_status_var.get() == "Google Drive Connected"
        and bool(google_drive_folder_var.get().strip())
        and bool(cloud_selected_items)
    )

    btn_upload_cloud_items.config(
        state=NORMAL if ready_to_upload else DISABLED,
        style="Ready.TButton" if ready_to_upload else "Accent.TButton"
    )

    if "cloud_upload_requirement_label" in globals():
        if ready_to_upload:
            cloud_upload_requirement_label.grid_remove()
        else:
            cloud_upload_requirement_label.config(
                text=f"Send to Google Drive is disabled: {', '.join(missing_requirements)}."
            )

def upload_cloud_items_now():
    if not google_drive_folder_var.get().strip():
        messagebox.showwarning("Missing File Name", "Enter a file name before uploading to Google Drive.")
        update_cloud_upload_button_state()
        return

    if google_drive_status_var.get() != "Google Drive Connected":
        messagebox.showwarning("Google Drive Not Connected", "Connect Google Drive before uploading cloud items.")
        update_cloud_upload_button_state()
        return

    if not cloud_selected_items:
        messagebox.showwarning("No Cloud Items", "Add at least one cloud item before uploading.")
        update_cloud_upload_button_state()
        return

    try:
        upload_cloud_selected_source_files_to_google_drive()
        messagebox.showinfo("Cloud Upload Complete", "Selected cloud items were sent to Google Drive.")
    except Exception as e:
        messagebox.showerror("Cloud Upload Failed", str(e))
    finally:
        update_cloud_upload_button_state()

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

def get_backup_notification_title(message):
    lowered = message.lower()

    if "google drive" in lowered or "cloud" in lowered:
        return "Cloud Backup"
    if "sql" in lowered:
        return "SQL Backup"
    if "auto backup" in lowered:
        return "Auto Backup"
    if "schedule" in lowered or "scheduled" in lowered:
        return "Local Schedule"
    return "Local Backup"

def write_scheduler_status(message, notification_title=None):
    with open(backup_log_file, "a", encoding="utf-8") as log:
        log.write(f"[Schedule] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - v{APP_VERSION} - {message}\n")

    status = "completed"
    lowered = message.lower()

    if "failed" in lowered:
        status = "failed"
    elif "skipped" in lowered or "stopped" in lowered:
        status = "info"
    elif "started" in lowered or "starting" in lowered:
        status = "running"

    write_backup_event("schedule", status, message)
    queue_ui_action(refresh_logs_tab)

    notification_terms = (
        "added", "removed", "enabled", "disabled", "started", "stopped",
        "complete", "finished", "failed"
    )
    if any(term in lowered for term in notification_terms):
        queue_ui_action(
            show_app_notification,
            notification_title or get_backup_notification_title(message),
            message
        )

# =========================================================
# Schedule and Tray Helpers
# =========================================================

def should_run_interval_schedule(schedule, schedule_key, now, current_time, today):
    recurrence = schedule.get("recurrence", "minutes")

    if recurrence == "minutes":
        try:
            interval_minutes = int(schedule.get("interval_minutes", 10))
        except (TypeError, ValueError):
            interval_minutes = 10

        last_run = schedule_last_run_times.get(schedule_key)
        return not last_run or (now - last_run).total_seconds() >= interval_minutes * 60

    schedule_days = schedule.get("days", [])

    if recurrence == "daily":
        if not schedule_days or today not in schedule_days:
            return False
        last_run = schedule_last_run_times.get(schedule_key)
        return last_run != now.strftime("%Y-%m-%d")

    schedule_time = schedule.get("time")

    if current_time != schedule_time:
        return False

    if recurrence in ("weekly", "biweekly") and schedule_days and today not in schedule_days:
        return False

    if recurrence == "biweekly" and now.isocalendar().week % 2 != 0:
        return False

    if recurrence == "monthly":
        try:
            month_day = int(schedule.get("month_day", 1))
        except (TypeError, ValueError):
            month_day = 1

        if now.day != month_day:
            return False

    last_run = schedule_last_run_times.get(schedule_key)
    return last_run != now.strftime("%Y-%m-%d %I:%M %p")

def check_scheduled_backups(schedule_next=True):
    global last_run_time

    if scheduler_running:
        now = datetime.now()
        current_time = now.strftime("%I:%M %p").lstrip("0")
        today = now.strftime("%a")

        for index, schedule in enumerate(scheduled_backup_times):
            schedule_key = f"{index}:{json.dumps(schedule, sort_keys=True) if isinstance(schedule, dict) else schedule}"

            if isinstance(schedule, dict):
                schedule_mode = schedule.get("mode", "time")
                schedule_days = schedule.get("days", [])

                if schedule_mode == "interval":
                    should_run = should_run_interval_schedule(
                        schedule,
                        schedule_key,
                        now,
                        current_time,
                        today
                    )
                elif today not in schedule_days:
                    should_run = False
                else:
                    schedule_time = schedule.get("time")
                    last_run = schedule_last_run_times.get(schedule_key)
                    should_run = (
                        current_time == schedule_time
                        and last_run != now.strftime("%Y-%m-%d %I:%M %p")
                    )
            else:
                should_run = (
                    current_time == schedule
                    and selected_days[today].get()
                    and current_time != last_run_time
                )

            if should_run:
                last_run_time = current_time

                if (
                    isinstance(schedule, dict)
                    and schedule.get("mode") == "interval"
                    and schedule.get("recurrence", "minutes") == "minutes"
                ):
                    schedule_last_run_times[schedule_key] = now
                elif (
                    isinstance(schedule, dict)
                    and schedule.get("mode") == "interval"
                    and schedule.get("recurrence") == "daily"
                ):
                    schedule_last_run_times[schedule_key] = now.strftime("%Y-%m-%d")
                elif isinstance(schedule, dict):
                    schedule_last_run_times[schedule_key] = now.strftime("%Y-%m-%d %I:%M %p")

                scheduler_status_var.set("Running Backup")
                status_label.config(image=icon_blue)
                update_tray_icon(LOCAL_SCHEDULE_COLOR)

                schedule_name = schedule.get("name", "Local Backup") if isinstance(schedule, dict) else "Local Backup"
                write_scheduler_status(
                    "Scheduled backup started.",
                    notification_title=schedule_name
                )

                backup_thread = threading.Thread(
                    target=run_backup_silent,
                    args=(schedule if isinstance(schedule, dict) else None,),
                    daemon=True
                )
                backup_thread.start()
                break

        else:
            if scheduler_status_var.get() != "Running Backup":
                scheduler_status_var.set("Idle")
                status_label.config(image=icon_blue)
                refresh_schedule_tray_icon()

    if schedule_next:
        root.after(SCHEDULER_POLL_INTERVAL_MS, check_scheduled_backups)

def start_cloud_backup_schedule():
    global cloud_scheduler_running

    if not cloud_scheduled_backup_times:
        messagebox.showwarning(
            "No Cloud Schedule",
            "Add at least one cloud backup time in the Cloud Backup tab first."
        )
        return

    if not cloud_selected_items:
        messagebox.showwarning(
            "No Cloud Items",
            "Add files or folders in Cloud Backup > Cloud first."
        )
        return

    try:
        google_drive_root_folder = ensure_google_drive_schedule_root_folder()
    except Exception as e:
        messagebox.showerror("Google Drive Folder Failed", str(e))
        return

    cloud_scheduler_running = True
    cloud_status_var.set("Enabled")
    cloud_schedule_enabled_var.set(True)
    refresh_schedule_tray_icon()

    google_email = get_google_drive_account_email()
    file_count, folder_count = get_selected_item_counts(cloud_selected_items)

    write_scheduler_status(
        f"Google Drive schedule enabled for {file_count} file(s) and {folder_count} folder(s). "
        f"Account: {google_email}. Folder: {google_drive_root_folder}."
    )
    check_cloud_scheduled_backups(schedule_next=False)

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
    selected_databases = get_checked_sql_databases()

    if selected_databases:
        return selected_databases

    typed_db = sql_database_var.get().strip()

    if typed_db:
        return [db.strip() for db in typed_db.split(",") if db.strip()]

    return []

def get_checked_sql_databases():
    return [
        db for db, var in sql_database_vars.items()
        if var.get()
    ]

def restore_sql_selected_databases(database_names):
    sql_database_vars.clear()

    selected_databases = [
        db.strip()
        for db in database_names
        if str(db).strip()
    ]

    if not selected_databases:
        selected_databases = [
            db.strip()
            for db in sql_database_var.get().split(",")
            if db.strip()
        ]

    for db_name in selected_databases:
        sql_database_vars[db_name] = BooleanVar(value=True)

    update_sql_selected_count()
    update_sql_backup_button_state()

def update_sql_selected_count():
    selected_count = len(get_selected_sql_databases())

    if selected_count == 0:
        sql_selected_count_var.set("No databases selected")
    else:
        sql_selected_count_var.set(f"{selected_count} database(s) selected")

def update_schedule_list():
    schedule_listbox.delete(0, END)

    for schedule in scheduled_backup_times:
        if isinstance(schedule, dict):
            name = schedule.get("name", "Local Backup")
            category = "Auto" if schedule.get("mode") == "interval" else "Manual"
            source = "Office files" if schedule.get("source") == "office_files" else "Selected items"
            description = schedule.get("description", "")
            destination = str(schedule.get("destination") or "").strip()
            destination_name = os.path.basename(os.path.normpath(destination)) if destination else "Default location"
            days = ", ".join(schedule.get("days", []))

            if schedule.get("mode") == "interval":
                recurrence = schedule.get("recurrence", "minutes")

                if recurrence == "minutes":
                    timing = f"Every {schedule.get('interval_minutes', 10)} min"
                elif recurrence == "daily":
                    timing = "Daily when enabled"
                elif recurrence == "biweekly":
                    timing = f"Bi-weekly at {schedule.get('time', '')}"
                else:
                    timing = f"{recurrence.title()} at {schedule.get('time', '')}"
            else:
                timing = schedule.get("time", "")

            display_text = f"{category} | {name} | {timing} | {source} | {destination_name} | {description} | {days}"
            schedule_listbox.insert(END, display_text)
        else:
            # old saved schedules support
            schedule_listbox.insert(END, schedule)

def add_backup_time():
    hour = hours_var.get()
    minute = minutes_var.get()
    ampm = ampm_var.get()
    selected_schedule_days = [day for day, var in selected_days.items() if var.get()]

    if not selected_schedule_days:
        messagebox.showwarning("No Days Selected", "Select at least one backup day.")
        return

    backup_time = f"{int(hour)}:{minute} {ampm}"
    uses_office_files = office_schedule_var.get()
    destination = manual_backup_destination_var.get().strip()

    if not uses_office_files and not scheduler_selected_items:
        messagebox.showwarning("No Items", "Add files or folders in the Schedule tab before adding a manual schedule.")
        return

    if not destination:
        messagebox.showwarning("No Manual Destination", "Choose a manual backup location first.")
        return

    new_schedule = {
        "name": schedule_name_var.get().strip() or ("Office Files Backup" if uses_office_files else "Schedule Backup"),
        "mode": "time",
        "started_at": record_schedule_destination_start(destination),
        "source": "office_files" if uses_office_files else "selected_items",
        "time": backup_time,
        "destination": destination,
        "description": schedule_description_var.get().strip() or "No description",
        "days": selected_schedule_days
    }

    if not uses_office_files:
        new_schedule["items"] = list(scheduler_selected_items)

    scheduled_backup_times.append(new_schedule)

    update_schedule_list()
    save_app_settings()

    write_scheduler_status(
        f"Backup schedule added at {backup_time}.",
        notification_title=new_schedule["name"]
    )

def add_office_auto_backup_schedule():
    selected_schedule_days = [day for day, var in auto_selected_days.items() if var.get()]
    recurrence = auto_backup_recurrence_var.get()
    uses_office_files = auto_office_schedule_var.get()

    destination = auto_backup_destination_var.get().strip()

    if not destination:
        messagebox.showwarning("No Auto Destination", "Choose an auto backup location first.")
        return

    if not uses_office_files and not auto_selected_items:
        messagebox.showwarning("No Items", "Add files or folders in the Schedule tab first.")
        return

    if recurrence in ("Daily", "Weekly", "Bi-weekly") and not selected_schedule_days:
        messagebox.showwarning("No Days Selected", "Select at least one backup day.")
        return

    backup_time = f"{int(auto_hours_var.get())}:{auto_minutes_var.get()} {auto_ampm_var.get()}"
    recurrence_key = {
        "Minutes": "minutes",
        "Every X minutes": "minutes",
        "Daily": "daily",
        "Weekly": "weekly",
        "Bi-weekly": "biweekly",
        "Monthly": "monthly"
    }.get(recurrence, "minutes")

    if recurrence_key == "minutes":
        try:
            interval_minutes = int(auto_backup_interval_var.get())
        except ValueError:
            messagebox.showwarning("Invalid Interval", "Enter a whole number of minutes.")
            return

        if interval_minutes < 1:
            messagebox.showwarning("Invalid Interval", "Interval must be at least 1 minute.")
            return

    new_schedule = {
        "name": auto_schedule_name_var.get().strip() or ("Office Auto Backup" if uses_office_files else "Auto Backup"),
        "mode": "interval",
        "started_at": record_schedule_destination_start(destination),
        "source": "office_files" if uses_office_files else "selected_items",
        "recurrence": recurrence_key,
        "destination": destination,
        "description": auto_schedule_description_var.get().strip() or "Automatic backup",
        "days": selected_schedule_days
    }

    if recurrence_key == "minutes":
        new_schedule["interval_minutes"] = interval_minutes
        new_schedule["days"] = []
    elif recurrence_key != "daily":
        new_schedule["time"] = backup_time

    if recurrence_key == "monthly":
        try:
            month_day = int(auto_month_day_var.get())
        except ValueError:
            messagebox.showwarning("Invalid Month Day", "Enter a day of month from 1 to 31.")
            return

        if month_day < 1 or month_day > 31:
            messagebox.showwarning("Invalid Month Day", "Enter a day of month from 1 to 31.")
            return

        new_schedule["month_day"] = month_day
        new_schedule["days"] = []

    if not uses_office_files:
        new_schedule["items"] = list(auto_selected_items)

    scheduled_backup_times.append(new_schedule)

    update_schedule_list()
    save_app_settings()

    if recurrence_key == "minutes":
        schedule_summary = f"Every {interval_minutes} minutes"
    elif recurrence_key == "daily":
        schedule_summary = f"Daily on {', '.join(selected_schedule_days)}"
    else:
        schedule_summary = recurrence

    write_scheduler_status(
        f"{schedule_summary} schedule added.",
        notification_title=new_schedule["name"]
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
    elif sql_scheduler_running:
        update_tray_icon(CLOUD_SCHEDULE_COLOR)
    elif scheduler_running:
        update_tray_icon(LOCAL_SCHEDULE_COLOR)
    else:
        update_tray_icon(STOPPED_COLOR)

def update_scheduler_action_buttons():
    button_text = "Stop Schedule" if scheduler_running else "Start Schedule"
    auto_button_text = "Disable" if scheduler_running else "Enable"

    if "btn_toggle_scheduler" in globals():
        btn_toggle_scheduler.config(
            text=button_text,
            style="Ready.TButton" if scheduler_running else "CompactAccent.TButton"
        )

    if "btn_auto_toggle_scheduler" in globals():
        btn_auto_toggle_scheduler.config(
            text=auto_button_text,
            style="Accent.TButton" if scheduler_running else "Ready.TButton"
        )

    if "auto_schedule_toggle" in globals():
        auto_schedule_toggle._draw()

def get_google_drive_save_location_caption(uploads_enabled=True):
    root_folder_name = google_drive_folder_var.get().strip() or "Backup Compressor"
    description = get_safe_filename_part(cloud_backup_description_var.get())
    example_file_name = f"{description}_backup_2026-06-13_14-30-00.zip" if description else "backup_2026-06-13_14-30-00.zip"
    example_group_folder = get_cloud_backup_group_folder_name(datetime(2026, 6, 13, 14, 30, 0))
    status_line = (
        "Cloud backups will be saved in Google Drive under:"
        if uploads_enabled
        else "Google Drive uploads are not enabled for this schedule yet. When enabled, files save under:"
    )

    return (
        f"{status_line}\n"
        f"{root_folder_name}\\<DESCRIPTION OR BACKUP TIME>\\<BACKUP DATE>\\<BACKUP TIME>\\<BACKUP FILE>\n"
        "Example:\n"
        f"{root_folder_name}\\{example_group_folder}\\2026-06-13\\14-30-00\\{example_file_name}"
    )

def toggle_scheduler():
    if scheduler_running:
        stop_scheduler()
    else:
        start_scheduler()

def show_google_drive_save_location_window(title="Google Drive Save Location", uploads_enabled=True):
    messagebox.showinfo(title, get_google_drive_save_location_caption(uploads_enabled))

def ensure_google_drive_schedule_root_folder():
    if google_drive_service is None:
        connect_google_drive()

    root_folder_name = google_drive_folder_var.get().strip() or "Backup Compressor"
    get_or_create_google_drive_folder(root_folder_name)
    return root_folder_name

def open_google_drive_schedule_location():
    try:
        if google_drive_service is None:
            connect_google_drive()

        root_folder_name = google_drive_folder_var.get().strip() or "Backup Compressor"
        folder_id = get_or_create_google_drive_folder(root_folder_name)
        webbrowser.open(f"https://drive.google.com/drive/folders/{folder_id}")
    except Exception as e:
        messagebox.showerror("Open Google Drive Failed", str(e))

def get_next_local_backup_text(now=None):
    now = now or datetime.now()
    candidates = []

    for schedule in scheduled_backup_times:
        if not isinstance(schedule, dict):
            continue

        recurrence = schedule.get("recurrence")
        days = schedule.get("days", [])

        if recurrence == "minutes":
            candidates.append((now, schedule.get("name", "Minute backup")))
            continue

        if recurrence == "daily":
            for offset in range(8):
                candidate_date = now + timedelta(days=offset)
                if candidate_date.strftime("%a") in days:
                    candidate = now if offset == 0 else candidate_date.replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                    candidates.append((candidate, schedule.get("name", "Daily backup")))
                    break
            continue

        time_text = schedule.get("time")
        if not time_text:
            continue
        try:
            parsed_time = datetime.strptime(time_text, "%I:%M %p")
        except ValueError:
            continue

        for offset in range(32):
            candidate_date = now + timedelta(days=offset)
            candidate = candidate_date.replace(
                hour=parsed_time.hour,
                minute=parsed_time.minute,
                second=0,
                microsecond=0
            )
            if candidate < now:
                continue
            if recurrence == "monthly" and candidate.day != int(schedule.get("month_day", 1)):
                continue
            if days and candidate.strftime("%a") not in days:
                continue
            if recurrence == "biweekly" and candidate.isocalendar().week % 2 != 0:
                continue
            candidates.append((candidate, schedule.get("name", "Scheduled backup")))
            break

    if not candidates:
        return "No upcoming backup could be calculated."

    next_run, schedule_name = min(candidates, key=lambda item: item[0])
    if next_run <= now:
        return f"{schedule_name}: immediately"
    return f"{schedule_name}: {next_run.strftime('%b %d, %Y at %I:%M %p')}"

def start_scheduler():  # start
    global scheduler_running

    if not scheduled_backup_times:
        messagebox.showwarning("No Schedule", "Add at least one backup time first.")
        return

    scheduler_running = True

    scheduler_status_var.set("Running")   # when started
    status_label.config(image=icon_blue)
    refresh_schedule_tray_icon()

    enabled_at = datetime.now()
    next_backup = get_next_local_backup_text(enabled_at)
    next_backup_name = next_backup.split(":", 1)[0] if ":" in next_backup else "Local Schedule"
    write_scheduler_status(
        f"Enabled {enabled_at.strftime('%b %d, %Y at %I:%M %p')}. "
        f"Next backup: {next_backup}.",
        notification_title=next_backup_name
    )
    check_scheduled_backups(schedule_next=False)
    update_scheduler_action_buttons()

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

    write_scheduler_status("Schedule stopped")
    update_scheduler_action_buttons()

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
            pystray.MenuItem("Schedule", show_window, default=True),  # KEY
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
                text="Find Server",
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
        bg=INPUT_BG,
        fg=TEXT,
        selectbackground=ACCENT,
        selectforeground=TEXT,
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

    window_width = min(1200, max(760, screen_width - 80))
    window_height = min(860, max(560, screen_height - 100))

    x = max(0, int((screen_width - window_width) / 2))
    y = max(0, int((screen_height - window_height) / 2))

    root.geometry(f"{window_width}x{window_height}+{x}+{y}")
    root.minsize(min(900, window_width), min(640, window_height))

def draw_rounded_rectangle(canvas, x1, y1, x2, y2, radius, fill, outline, width=1, tags=None):
    radius = min(radius, max(0, (x2 - x1) / 2), max(0, (y2 - y1) / 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
        x1 + radius, y1
    ]
    canvas.create_polygon(points, fill=fill, outline="", smooth=True, splinesteps=24, tags=tags)

    if outline and width:
        canvas.create_line(
            points,
            fill=outline,
            width=width,
            smooth=True,
            splinesteps=24,
            joinstyle=ROUND,
            tags=tags
        )

class RoundedButton(Canvas):
    def __init__(self, master=None, **kwargs):
        self.command = kwargs.pop("command", None)
        self.text = kwargs.pop("text", "")
        self.button_state = kwargs.pop("state", NORMAL)
        self.style_name = kwargs.pop("style", "TButton")
        width = kwargs.pop("width", BTN_WIDTH)
        kwargs.pop("padding", None)

        try:
            pixel_width = max(120, int(width) * 10)
        except (TypeError, ValueError):
            pixel_width = 150

        pixel_height = 39 if self.style_name != "Accent.TButton" else 43

        super().__init__(
            master,
            width=pixel_width,
            height=pixel_height,
            bg=CARD,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs
        )

        self.is_hovered = False
        self.is_pressed = False
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.configure(takefocus=True)
        self.bind("<Return>", lambda event: self.invoke())
        self.bind("<space>", lambda event: self.invoke())
        self.bind("<FocusIn>", lambda event: self._draw())
        self.bind("<FocusOut>", lambda event: self._draw())
        self.bind("<Configure>", lambda event: self._draw())
        self._draw()

    def _draw(self):
        canvas_width = max(self.winfo_width(), 1)
        canvas_height = max(self.winfo_height(), 1)
        is_disabled = self.button_state in (DISABLED, "disabled")
        is_ready = self.style_name in ("Ready.TButton", "Accent.TButton", "CompactAccent.TButton")
        is_success = self.style_name == "Success.TButton"

        if is_success and not is_disabled:
            fill = "#2e7d32"
        elif is_ready and not is_disabled:
            fill = ACCENT
        else:
            fill = CARD_DARK

        if (self.is_hovered or self.is_pressed) and not is_disabled:
            if is_success:
                fill = "#256629"
            elif is_ready:
                fill = ACCENT_HOVER
            else:
                fill = THEMES[THEME_MODE]["hover"]

        outline = ACCENT if self.focus_get() == self else (fill if is_ready or is_success else SOFT_BORDER)
        text_color = MUTED if is_disabled else TEXT

        self.delete("all")
        draw_rounded_rectangle(
            self,
            2,
            2,
            canvas_width - 2,
            canvas_height - 2,
            9,
            fill,
            outline,
            1,
            "button_shape"
        )
        self.create_text(
            canvas_width / 2,
            canvas_height / 2,
            text=self.text,
            anchor="center",
            fill=text_color,
            font=("Segoe UI", 10, "bold")
        )

    def _on_press(self, event):
        if self.button_state in (DISABLED, "disabled"):
            return

        self.is_pressed = True
        self._draw()

    def _on_release(self, event):
        if self.button_state in (DISABLED, "disabled"):
            return

        was_pressed = self.is_pressed
        self.is_pressed = False
        self._draw()

        if was_pressed and self.command:
            self.command()

    def _on_enter(self, event):
        self.is_hovered = True
        self._draw()

    def _on_leave(self, event):
        self.is_hovered = False
        self.is_pressed = False
        self._draw()

    def config(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)

        if "text" in kwargs:
            self.text = kwargs.pop("text")

        if "command" in kwargs:
            self.command = kwargs.pop("command")

        if "state" in kwargs:
            self.button_state = kwargs.pop("state")

        if "style" in kwargs:
            self.style_name = kwargs.pop("style")

        if "width" in kwargs:
            try:
                super().config(width=max(120, int(kwargs.pop("width")) * 10))
            except (TypeError, ValueError):
                kwargs.pop("width", None)

        if kwargs:
            super().config(**kwargs)

        self._draw()

    configure = config

    def cget(self, key):
        if key == "text":
            return self.text

        if key == "command":
            return self.command

        if key == "state":
            return self.button_state

        return super().cget(key)

    def invoke(self):
        if self.button_state not in (DISABLED, "disabled") and self.command:
            return self.command()

        return None

class ToggleSwitch(Canvas):
    def __init__(self, master=None, command=None, is_on_callback=None, width=168, height=38, **kwargs):
        self.command = command
        self.is_on_callback = is_on_callback or (lambda: False)
        self.is_hovered = False
        self.switch_state = kwargs.pop("state", NORMAL)
        kwargs.pop("text", None)
        kwargs.pop("style", None)

        super().__init__(
            master,
            width=width,
            height=height,
            bg=CARD,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs
        )

        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Configure>", lambda event: self._draw())
        self._draw()

    def _draw(self):
        canvas_width = max(self.winfo_width(), 1)
        canvas_height = max(self.winfo_height(), 1)
        is_on = bool(self.is_on_callback())
        is_disabled = self.switch_state in (DISABLED, "disabled")
        track_fill = TOGGLE_ENABLED if is_on else TOGGLE_DISABLED
        track_outline = TOGGLE_ENABLED_HOVER if is_on else TOGGLE_DISABLED_HOVER

        if is_disabled:
            track_fill = THEMES[THEME_MODE]["shell"]
            track_outline = SOFT_BORDER
        elif self.is_hovered:
            track_fill = TOGGLE_ENABLED_HOVER if is_on else TOGGLE_DISABLED_HOVER

        label = "Disable" if is_on else "Enable"

        self.delete("all")
        draw_rounded_rectangle(
            self,
            2,
            3,
            canvas_width - 2,
            canvas_height - 3,
            (canvas_height - 6) / 2,
            track_fill,
            track_outline,
            1,
            "toggle_track"
        )
        self.create_text(
            canvas_width / 2,
            canvas_height / 2,
            text=label,
            anchor="center",
            fill=MUTED if is_disabled else TEXT,
            font=("Segoe UI", 9, "bold")
        )

    def _on_click(self, event):
        if self.switch_state in (DISABLED, "disabled"):
            return

        if self.command:
            self.command()
        self._draw()

    def _on_enter(self, event):
        self.is_hovered = True
        self._draw()

    def _on_leave(self, event):
        self.is_hovered = False
        self._draw()

    def config(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)

        if "command" in kwargs:
            self.command = kwargs.pop("command")

        if "state" in kwargs:
            self.switch_state = kwargs.pop("state")

        kwargs.pop("text", None)
        kwargs.pop("style", None)
        kwargs.pop("width", None)

        if kwargs:
            super().config(**kwargs)

        self._draw()

    configure = config

class ToggleCheckbutton(Canvas):
    def __init__(self, master=None, **kwargs):
        self.text = kwargs.pop("text", "")
        self.variable = kwargs.pop("variable", None)
        self.command = kwargs.pop("command", None)
        self.onvalue = kwargs.pop("onvalue", True)
        self.offvalue = kwargs.pop("offvalue", False)
        self.toggle_state = kwargs.pop("state", NORMAL)
        kwargs.pop("style", None)
        requested_width = kwargs.pop("width", None)
        kwargs.pop("padding", None)

        if self.variable is None:
            self.variable = BooleanVar(value=False)

        try:
            pixel_width = max(78, int(requested_width) * 10) if requested_width else min(max(78, len(self.text) * 7 + 44), 320)
        except (TypeError, ValueError):
            pixel_width = min(max(78, len(self.text) * 7 + 44), 320)

        super().__init__(
            master,
            width=pixel_width,
            height=34,
            bg=CARD,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs
        )

        self.is_hovered = False
        self._trace_id = None

        try:
            self._trace_id = self.variable.trace_add("write", lambda *args: self._draw())
        except (AttributeError, TclError):
            pass

        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Configure>", lambda event: self._draw())
        self._draw()

    def _is_on(self):
        try:
            return self.variable.get() == self.onvalue
        except TclError:
            return False

    def _set_value(self, value):
        try:
            self.variable.set(self.onvalue if value else self.offvalue)
        except TclError:
            pass

    def _draw(self):
        canvas_width = max(self.winfo_width(), 1)
        canvas_height = max(self.winfo_height(), 1)
        is_on = self._is_on()
        is_disabled = self.toggle_state in (DISABLED, "disabled")
        fill = TOGGLE_ENABLED if is_on else TOGGLE_DISABLED
        outline = TOGGLE_ENABLED_HOVER if is_on else TOGGLE_DISABLED_HOVER

        if is_disabled:
            fill = THEMES[THEME_MODE]["shell"]
            outline = SOFT_BORDER
        elif self.is_hovered:
            fill = TOGGLE_ENABLED_HOVER if is_on else TOGGLE_DISABLED_HOVER

        self.delete("all")
        draw_rounded_rectangle(
            self,
            2,
            3,
            canvas_width - 2,
            canvas_height - 3,
            (canvas_height - 6) / 2,
            fill,
            outline,
            1,
            "toggle_check_shape"
        )
        self.create_text(
            canvas_width / 2,
            canvas_height / 2,
            text=self.text,
            anchor="center",
            fill=MUTED if is_disabled else TEXT,
            font=("Segoe UI", 9, "bold" if is_on else "normal")
        )

    def _on_click(self, event):
        if self.toggle_state in (DISABLED, "disabled"):
            return

        self._set_value(not self._is_on())

        if self.command:
            self.command()

        self._draw()

    def _on_enter(self, event):
        self.is_hovered = True
        self._draw()

    def _on_leave(self, event):
        self.is_hovered = False
        self._draw()

    def config(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)

        if "text" in kwargs:
            self.text = kwargs.pop("text")

        if "variable" in kwargs:
            self.variable = kwargs.pop("variable")

        if "command" in kwargs:
            self.command = kwargs.pop("command")

        if "state" in kwargs:
            self.toggle_state = kwargs.pop("state")

        if "onvalue" in kwargs:
            self.onvalue = kwargs.pop("onvalue")

        if "offvalue" in kwargs:
            self.offvalue = kwargs.pop("offvalue")

        kwargs.pop("style", None)
        kwargs.pop("width", None)

        if kwargs:
            super().config(**kwargs)

        self._draw()

    configure = config

    def cget(self, key):
        if key == "text":
            return self.text

        if key == "variable":
            return self.variable

        if key == "command":
            return self.command

        if key == "state":
            return self.toggle_state

        return super().cget(key)

    def select(self):
        self._set_value(True)

    def deselect(self):
        self._set_value(False)

    def invoke(self):
        self._on_click(None)

ttk.Button = RoundedButton
ttk.Checkbutton = ToggleCheckbutton

def update_main_notebook_shell(event=None):
    if "notebook_shell" not in globals():
        return

    notebook_shell.update_idletasks()
    shell_width = notebook_shell.winfo_width()
    shell_height = notebook_shell.winfo_height()

    if shell_width <= 2 or shell_height <= 2:
        return

    inset = 3
    content_inset = inset + 2
    notebook_shell.delete("rounded_notebook_shell")
    draw_rounded_rectangle(
        notebook_shell,
        inset,
        inset,
        shell_width - inset,
        shell_height - inset,
        14,
        CARD,
        THEMES[THEME_MODE]["shell"],
        1,
        "rounded_notebook_shell"
    )
    notebook_shell.tag_lower("rounded_notebook_shell")
    notebook_shell.coords(notebook_shell_window, content_inset, content_inset)
    notebook_shell.itemconfigure(
        notebook_shell_window,
        width=max(1, shell_width - (content_inset * 2)),
        height=max(1, shell_height - (content_inset * 2))
    )

def draw_main_tab_button(tab_button, hovered=False):
    canvas = tab_button["canvas"]

    if "notebook" not in globals():
        return

    canvas.configure(bg=CARD)
    canvas.update_idletasks()
    canvas_width = max(canvas.winfo_width(), 1)
    canvas_height = max(canvas.winfo_height(), 1)
    is_selected = notebook.select() == str(tab_button["tab"])
    fill = ACCENT if is_selected else (THEMES[THEME_MODE]["hover"] if hovered else CARD)
    outline = fill
    text_color = "#ffffff" if is_selected else TEXT

    canvas.delete("all")
    draw_rounded_rectangle(
        canvas,
        2,
        2,
        canvas_width - 2,
        canvas_height - 2,
        10,
        fill,
        outline,
        1,
        "main_tab_shape"
    )
    canvas.create_text(
        18,
        canvas_height / 2,
        text=tab_button["text"],
        anchor="w",
        fill=text_color,
        font=("Segoe UI", 10, "bold")
    )

def update_main_tab_buttons(event=None):
    for tab_button in main_tab_buttons:
        draw_main_tab_button(tab_button)

def apply_cloud_tab_visibility():
    visible = show_cloud_tab_var.get()
    if visible:
        notebook.tab(cloud_tab, state="normal")
    else:
        if notebook.select() == str(cloud_tab):
            notebook.select(settings_tab)
        notebook.hide(cloud_tab)
    # Repack in the original order, ahead of the sidebar's flexible spacer.
    for button in main_tab_buttons:
        button["canvas"].pack_forget()
    for button in main_tab_buttons:
        if button["tab"] != cloud_tab or visible:
            button["canvas"].pack(fill=X, pady=(0, 8), before=main_tabs_spacer)
    update_main_tab_buttons()

def toggle_cloud_tab_visibility():
    apply_cloud_tab_visibility()
    save_app_settings()

def select_main_tab(tab):
    notebook.select(tab)
    update_main_tab_buttons()

def create_main_tab_button(parent, text, tab):
    canvas = Canvas(
        parent,
        width=158,
        height=44,
        bg=CARD,
        highlightthickness=0,
        bd=0,
        cursor="hand2",
        takefocus=True
    )
    canvas.pack(fill=X, pady=(0, 8))

    tab_button = {
        "canvas": canvas,
        "text": text,
        "tab": tab
    }
    main_tab_buttons.append(tab_button)

    canvas.bind("<Button-1>", lambda event, target=tab: select_main_tab(target))
    canvas.bind("<Return>", lambda event, target=tab: select_main_tab(target))
    canvas.bind("<space>", lambda event, target=tab: select_main_tab(target))
    canvas.bind("<FocusIn>", lambda event, button=tab_button: draw_main_tab_button(button, True))
    canvas.bind("<FocusOut>", lambda event, button=tab_button: draw_main_tab_button(button))
    canvas.bind("<Enter>", lambda event, button=tab_button: draw_main_tab_button(button, True))
    canvas.bind("<Leave>", lambda event, button=tab_button: draw_main_tab_button(button, False))
    canvas.bind("<Configure>", lambda event, button=tab_button: draw_main_tab_button(button))
    draw_main_tab_button(tab_button)

    return canvas

def draw_scheduler_subtab_button(tab_button, hovered=False):
    canvas = tab_button["canvas"]

    if "scheduler_subtabs" not in globals():
        return

    canvas.configure(bg=BG)
    canvas.update_idletasks()
    canvas_width = max(canvas.winfo_width(), 1)
    canvas_height = max(canvas.winfo_height(), 1)
    is_selected = scheduler_subtabs.select() == str(tab_button["tab"])
    fill = ACCENT if is_selected else (THEMES[THEME_MODE]["hover"] if hovered else CARD_DARK)
    outline = TEXT
    text_color = TEXT if is_selected or hovered else TEXT

    canvas.delete("all")
    draw_rounded_rectangle(
        canvas,
        2,
        2,
        canvas_width - 2,
        canvas_height - 2,
        10,
        fill,
        outline,
        1,
        "scheduler_subtab_shape"
    )
    canvas.create_text(
        canvas_width / 2,
        canvas_height / 2,
        text=tab_button["text"],
        anchor="center",
        fill=text_color,
        font=("Segoe UI", 10, "bold" if is_selected else "normal")
    )

def update_scheduler_subtab_buttons(event=None):
    for tab_button in scheduler_subtab_buttons:
        draw_scheduler_subtab_button(tab_button)

def select_scheduler_subtab(tab):
    scheduler_subtabs.select(tab)
    update_scheduler_subtab_buttons()
    update_scheduler_scroll_region()

def create_scheduler_subtab_button(parent, text, tab):
    canvas = Canvas(
        parent,
        width=170,
        height=42,
        bg=BG,
        highlightthickness=0,
        bd=0,
        cursor="hand2"
    )
    canvas.pack(side=LEFT, padx=(0, 8))

    tab_button = {
        "canvas": canvas,
        "text": text,
        "tab": tab
    }
    scheduler_subtab_buttons.append(tab_button)

    canvas.bind("<Button-1>", lambda event, target=tab: select_scheduler_subtab(target))
    canvas.bind("<Enter>", lambda event, button=tab_button: draw_scheduler_subtab_button(button, True))
    canvas.bind("<Leave>", lambda event, button=tab_button: draw_scheduler_subtab_button(button, False))
    canvas.bind("<Configure>", lambda event, button=tab_button: draw_scheduler_subtab_button(button))
    draw_scheduler_subtab_button(tab_button)

    return canvas

def draw_cloud_subtab_button(tab_button, hovered=False):
    canvas = tab_button["canvas"]

    if "cloud_subtabs" not in globals():
        return

    canvas.configure(bg=BG)
    canvas.update_idletasks()
    canvas_width = max(canvas.winfo_width(), 1)
    canvas_height = max(canvas.winfo_height(), 1)
    is_selected = cloud_subtabs.select() == str(tab_button["tab"])
    fill = ACCENT if is_selected else (THEMES[THEME_MODE]["hover"] if hovered else CARD_DARK)
    outline = TEXT

    canvas.delete("all")
    draw_rounded_rectangle(
        canvas,
        2,
        2,
        canvas_width - 2,
        canvas_height - 2,
        10,
        fill,
        outline,
        1,
        "cloud_subtab_shape"
    )
    canvas.create_text(
        canvas_width / 2,
        canvas_height / 2,
        text=tab_button["text"],
        anchor="center",
        fill=TEXT,
        font=("Segoe UI", 10, "bold")
    )

def update_cloud_subtab_buttons(event=None):
    for tab_button in cloud_subtab_buttons:
        draw_cloud_subtab_button(tab_button)

def select_cloud_subtab(tab):
    if "cloud_subtabs" not in globals():
        return

    cloud_subtabs.select(tab)
    update_cloud_subtab_buttons()
    update_cloud_action_buttons()

def update_cloud_scheduler_controls():
    if "btn_toggle_cloud_schedule" not in globals():
        return

    if cloud_scheduler_running:
        btn_toggle_cloud_schedule.config(
            text="Disable",
            style="Accent.TButton"
        )
    else:
        btn_toggle_cloud_schedule.config(
            text="Enable",
            style="Ready.TButton"
        )

def create_cloud_subtab_button(parent, text, tab):
    canvas = Canvas(
        parent,
        width=230,
        height=42,
        bg=BG,
        highlightthickness=0,
        bd=0,
        cursor="hand2"
    )
    canvas.pack(side=LEFT, padx=(0, 8))

    tab_button = {
        "canvas": canvas,
        "text": text,
        "tab": tab
    }
    cloud_subtab_buttons.append(tab_button)

    canvas.bind("<Button-1>", lambda event, target=tab: select_cloud_subtab(target))
    canvas.bind("<Enter>", lambda event, button=tab_button: draw_cloud_subtab_button(button, True))
    canvas.bind("<Leave>", lambda event, button=tab_button: draw_cloud_subtab_button(button, False))
    canvas.bind("<Configure>", lambda event, button=tab_button: draw_cloud_subtab_button(button))
    draw_cloud_subtab_button(tab_button)

    return canvas

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

    if "btn_test_sql" in globals():
        btn_test_sql.config(style="Accent.TButton")

    if not server:
        sql_connection_status_var.set("SQL Not Connected")
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
        sql_connection_status_var.set("SQL Connected")
        btn_test_sql.config(style="Success.TButton")
        messagebox.showinfo("SQL Connection", "SQL Server connection successful.")
    except Exception as e:
        sql_connection_status_var.set("SQL Not Connected")
        btn_test_sql.config(style="Accent.TButton")
        messagebox.showerror("SQL Connection Failed", str(e))

def backup_single_sql_database(database, show_messages=True):
    server = sql_server_var.get().strip()
    destination = sql_staging_destination_var.get().strip()

    if not server or not database:
        if show_messages:
            messagebox.showwarning("Missing SQL Info", "Enter SQL Server and database name.")
        return False

    if not destination:
        if show_messages:
            messagebox.showwarning("No Staging Folder", "Choose a temporary SQL staging folder first.")
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
        write_scheduler_status(f"SQL staging file created: {database}")
        write_backup_event(
            "sql",
            "completed",
            f"SQL backup complete: {database}",
            output_file=output_file,
            destination=destination,
            backup_format="BAK"
        )
        return output_file

    except Exception as e:
        write_scheduler_status(f"SQL backup failed for {database}: {e}")
        write_backup_event(
            "sql",
            "failed",
            f"SQL backup failed for {database}: {e}",
            output_file=output_file,
            destination=destination,
            backup_format="BAK"
        )

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

def backup_selected_sql_databases(show_messages=True):
    selected_databases = get_selected_sql_databases()

    if not selected_databases:
        if show_messages:
            messagebox.showwarning("No Database", "Select or enter at least one database.")
        return []

    destination = sql_staging_destination_var.get().strip()

    if not destination:
        if show_messages:
            messagebox.showwarning("No Staging Folder", "Choose a temporary SQL staging folder first.")
        return []

    output_files = []

    for database in selected_databases:
        output_file = backup_single_sql_database(database, show_messages=False)

        if output_file:
            output_files.append(output_file)

    if show_messages:
        messagebox.showinfo(
            "SQL Backup Complete",
            f"Backed up {len(output_files)} of {len(selected_databases)} database(s)."
        )

    return output_files

def backup_sql_databases_now():
    backup_selected_sql_databases(show_messages=True)
    update_sql_backup_button_state()

def backup_sql_databases_to_google_drive(show_messages=True):
    if google_drive_status_var.get() != "Google Drive Connected":
        if show_messages:
            messagebox.showwarning("Google Drive Not Connected", "Connect Google Drive before sending SQL backups to Google Drive.")
        queue_ui_action(update_sql_backup_button_state)
        return 0, 0

    output_files = backup_selected_sql_databases(show_messages=False)

    if not output_files:
        queue_ui_action(update_sql_backup_button_state)
        return 0, 0

    backup_time = datetime.now()
    upload_count = 0

    for index, output_file in enumerate(output_files, start=1):
        queue_ui_action(
            sql_cloud_action_status_var.set,
            f"Uploading database {index} of {len(output_files)}..."
        )
        try:
            drive_file_id = upload_file_to_google_drive(output_file, backup_time=backup_time)
            message = f"SQL Google Drive upload complete: {os.path.basename(output_file)}"
            write_scheduler_status(message)
            write_cloud_backup_log(
                "completed",
                output_file,
                message,
                drive_file_id
            )
            try:
                os.remove(output_file)
            except OSError:
                pass
            upload_count += 1
        except Exception as e:
            message = f"SQL Google Drive upload failed for {os.path.basename(output_file)}: {e}"
            write_scheduler_status(message)
            write_cloud_backup_log(
                "failed",
                output_file,
                message
            )

    if show_messages:
        messagebox.showinfo(
            "SQL Cloud Backup Complete",
            f"Uploaded {upload_count} of {len(output_files)} SQL backup file(s) to Google Drive."
        )
    queue_ui_action(update_sql_backup_button_state)
    return upload_count, len(output_files)

def start_sql_cloud_backup_async():
    if google_drive_status_var.get() != "Google Drive Connected":
        messagebox.showwarning(
            "Google Drive Not Connected",
            "Connect Google Drive before backing up SQL databases."
        )
        update_sql_backup_button_state()
        return

    if not get_selected_sql_databases():
        messagebox.showwarning("No Database", "Select or enter at least one SQL database.")
        update_sql_backup_button_state()
        return

    def worker():
        set_ui_busy(True)
        queue_ui_action(sql_cloud_action_status_var.set, "Creating temporary SQL backup files...")
        try:
            uploaded, total = backup_sql_databases_to_google_drive(show_messages=False)
            if total and uploaded == total:
                result = f"Complete: {uploaded} database(s) uploaded to Google Drive."
            elif total:
                result = f"Finished with errors: {uploaded} of {total} uploaded."
            else:
                result = "No SQL backup files were created."
            queue_ui_action(sql_cloud_action_status_var.set, result)
        except Exception as error:
            queue_ui_action(sql_cloud_action_status_var.set, f"Failed: {error}")
            write_scheduler_status(f"SQL Google Drive backup failed: {error}")
        finally:
            set_ui_busy(False)
            queue_ui_action(update_sql_backup_button_state)

    threading.Thread(target=worker, daemon=True).start()

def load_sql_databases():
    server = sql_server_var.get().strip()

    if not server:
        sql_connection_status_var.set("SQL Not Connected")
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
        sql_connection_status_var.set("SQL Connected")

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
        sql_connection_status_var.set("SQL Not Connected")
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

    list_frame = ttk.Frame(db_window, style="Card.TFrame", padding=16)
    list_frame.pack(fill=BOTH, expand=True, padx=15, pady=(0, 10))

    canvas = Canvas(list_frame, bg=CARD, highlightthickness=0)
    scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
    checkbox_frame = ttk.Frame(canvas, style="Card.TFrame")

    canvas.create_window((0, 0), window=checkbox_frame, anchor="nw")
    configure_auto_hide_scrollbar(
        canvas,
        scrollbar,
        VERTICAL,
        "pack",
        {"side": RIGHT, "fill": Y}
    )

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
        update_sql_selected_count()
        save_app_settings()

        messagebox.showinfo(
            "Databases Selected",
            f"{len(selected_databases)} database(s) selected."
        )

        close_database_selection_window()
        update_sql_database_button_state()
        update_cloud_action_buttons()

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
    update_cloud_upload_button_state()
    update_sql_backup_button_state()
    update_cloud_next_button_state()

    messagebox.showinfo(
        "Google Drive",
        "Google Drive connected successfully."
    )

def upload_file_to_google_drive(file_path, folder_id=None, backup_time=None):
    global google_drive_service

    if google_drive_service is None:
        connect_google_drive()

    if folder_id is None:
        folder_id = get_google_drive_backup_folder_id(file_path, backup_time=backup_time)

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

def get_google_drive_backup_folder_id(file_path, backup_time=None):
    root_folder_name = google_drive_folder_var.get().strip() or "Backup Compressor"
    root_folder_id = get_or_create_google_drive_folder(root_folder_name)

    if backup_time is None:
        backup_time = datetime.now()

    group_folder_name = get_cloud_backup_group_folder_name(backup_time)
    group_folder_id = get_or_create_google_drive_folder(group_folder_name, parent_id=root_folder_id)

    date_folder_id = get_or_create_google_drive_folder(
        backup_time.strftime("%Y-%m-%d"),
        parent_id=group_folder_id
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
        upload_test_folder_id = get_or_create_google_drive_folder("Upload Test")
        upload_file_to_google_drive(test_file, folder_id=upload_test_folder_id)
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
        drive_file_id = upload_file_to_google_drive(latest_file)
        message = f"Google Drive upload complete: {os.path.basename(latest_file)}"
        write_scheduler_status(message)
        write_cloud_backup_log(
            "completed",
            latest_file,
            message,
            drive_file_id
        )
        return True
    except Exception as e:
        message = f"Google Drive upload failed for {os.path.basename(latest_file)}: {e}"
        write_scheduler_status(message)
        write_cloud_backup_log(
            "failed",
            latest_file,
            message
        )
        return False

def disconnect_google_drive():
    global google_drive_service

    token_path = os.path.join(app_data_folder, "token.json")

    try:
        if os.path.exists(token_path):
            os.remove(token_path)

        google_drive_service = None
        google_drive_status_var.set("Not Connected")
        update_cloud_upload_button_state()
        update_sql_backup_button_state()
        update_cloud_next_button_state()

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

    update_cloud_upload_button_state()
    update_sql_backup_button_state()
    update_cloud_next_button_state()

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
# Cloud Backup Schedule
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
            add_unique_cloud_items(files)

        update_cloud_selection_count()
        save_app_settings()
        selection_window.destroy()

    def select_folder():
        folder = filedialog.askdirectory(parent=selection_window)

        if folder:
            add_unique_cloud_items([folder])

        update_cloud_selection_count()
        save_app_settings()
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

def get_normalized_cloud_item_path(item):
    return os.path.normcase(os.path.normpath(str(item)))

def dedupe_cloud_selected_items():
    unique_items = []
    seen_paths = set()

    for item in cloud_selected_items:
        normalized_path = get_normalized_cloud_item_path(item)

        if normalized_path in seen_paths:
            continue

        seen_paths.add(normalized_path)
        unique_items.append(item)

    if len(unique_items) != len(cloud_selected_items):
        cloud_selected_items[:] = unique_items
        return True

    return False

def add_unique_cloud_items(items):
    existing_paths = {
        get_normalized_cloud_item_path(item)
        for item in cloud_selected_items
    }

    added_count = 0

    for item in items:
        normalized_path = get_normalized_cloud_item_path(item)

        if normalized_path in existing_paths:
            continue

        existing_paths.add(normalized_path)
        cloud_selected_items.append(item)
        added_count += 1

    return added_count

def add_cloud_backup_time():
    global cloud_schedule_edit_index

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

    schedule = {
        "time": backup_time,
        "days": selected_cloud_days
    }

    if cloud_schedule_edit_index is not None and 0 <= cloud_schedule_edit_index < len(cloud_scheduled_backup_times):
        cloud_scheduled_backup_times[cloud_schedule_edit_index] = schedule
        cloud_schedule_edit_index = None
        btn_add_cloud_time.config(text="Add Time")
    else:
        cloud_scheduled_backup_times.append(schedule)

    update_cloud_schedule_list()
    save_app_settings()

def update_cloud_schedule_list():
    selected_index = cloud_schedule_edit_index

    cloud_schedule_listbox.delete(0, END)

    for schedule in cloud_scheduled_backup_times:
        if isinstance(schedule, dict):
            backup_time = schedule.get("time", "")
            days = ", ".join(schedule.get("days", []))
            cloud_schedule_listbox.insert(END, f"{backup_time} | {days}")
        else:
            # Keep old saved time-only schedules readable.
            cloud_schedule_listbox.insert(END, schedule)

    if selected_index is not None and 0 <= selected_index < cloud_schedule_listbox.size():
        cloud_schedule_listbox.selection_set(selected_index)
        cloud_schedule_listbox.see(selected_index)

def edit_selected_cloud_backup_time(event=None):
    global cloud_schedule_edit_index

    selected = cloud_schedule_listbox.curselection()

    if not selected:
        return

    index = selected[0]
    schedule = cloud_scheduled_backup_times[index]

    if isinstance(schedule, dict):
        backup_time = schedule.get("time", "")
        schedule_days = schedule.get("days", [])
    else:
        backup_time = schedule
        schedule_days = list(cloud_selected_days.keys())

    try:
        time_part, ampm = backup_time.split()
        hour, minute = time_part.split(":")
    except ValueError:
        return

    cloud_hours_var.set(f"{int(hour):02d}")
    cloud_minutes_var.set(minute)
    cloud_ampm_var.set(ampm)

    for day, var in cloud_selected_days.items():
        var.set(day in schedule_days)

    cloud_schedule_edit_index = index
    btn_add_cloud_time.config(text="Update Time")

def remove_cloud_backup_time():
    global cloud_schedule_edit_index

    selected = cloud_schedule_listbox.curselection()

    if not selected:
        messagebox.showwarning("No Time Selected", "Select a cloud backup time to remove.")
        return

    index = selected[0]
    cloud_scheduled_backup_times.pop(index)
    cloud_schedule_edit_index = None
    btn_add_cloud_time.config(text="Add Time")

    update_cloud_schedule_list()
    save_app_settings()

def remove_selected_cloud_items():
    selected = list(cloud_items_listbox.curselection())

    if not selected:
        messagebox.showwarning("No Items Selected", "Select one or more cloud backup items to remove.")
        return

    for index in reversed(selected):
        cloud_selected_items.pop(index)

    update_cloud_selection_count()
    save_app_settings()

def clear_cloud_items():
    if not cloud_selected_items:
        return

    cloud_selected_items.clear()
    update_cloud_selection_count()
    save_app_settings()

def start_cloud_scheduler():
    global cloud_scheduler_running

    if not cloud_scheduled_backup_times:
        messagebox.showwarning("No Cloud Schedule", "Add at least one cloud backup time first.")
        return

    try:
        ensure_google_drive_schedule_root_folder()
    except Exception as e:
        messagebox.showerror("Google Drive Folder Failed", str(e))
        return

    cloud_scheduler_running = True
    cloud_status_var.set("Enabled")
    cloud_schedule_enabled_var.set(True)
    refresh_schedule_tray_icon()

    show_google_drive_save_location_window(
        "Cloud Schedule Google Drive Location",
        uploads_enabled=True
    )

    messagebox.showinfo(
        "Cloud Backup Schedule Started",
        "Cloud backup schedule is now running."
    )
    check_cloud_scheduled_backups(schedule_next=False)
    update_cloud_scheduler_controls()
    update_cloud_action_buttons()

def stop_cloud_scheduler():
    global cloud_scheduler_running

    cloud_scheduler_running = False
    cloud_status_var.set("Disabled")
    cloud_schedule_enabled_var.set(False)
    refresh_schedule_tray_icon()
    update_cloud_scheduler_controls()
    update_cloud_action_buttons()

def toggle_cloud_scheduler():
    if cloud_scheduler_running:
        stop_cloud_scheduler()
    else:
        start_cloud_scheduler()

def update_sql_scheduler_controls():
    if "btn_toggle_sql_schedule" not in globals():
        return

    btn_toggle_sql_schedule._draw()

def add_sql_backup_time():
    global sql_schedule_edit_index

    hour = sql_hours_var.get()
    minute = sql_minutes_var.get()
    ampm = sql_ampm_var.get()
    backup_time = f"{int(hour)}:{minute} {ampm}"
    selected_sql_days = [
        day for day, var in sql_selected_days.items()
        if var.get()
    ]

    if not selected_sql_days:
        messagebox.showwarning("No Days Selected", "Select at least one SQL backup day.")
        return

    schedule = {
        "time": backup_time,
        "days": selected_sql_days
    }

    if sql_schedule_edit_index is not None and 0 <= sql_schedule_edit_index < len(sql_scheduled_backup_times):
        sql_scheduled_backup_times[sql_schedule_edit_index] = schedule
        sql_schedule_edit_index = None
        btn_add_sql_time.config(text="Add Time")
    else:
        sql_scheduled_backup_times.append(schedule)

    update_sql_schedule_list()
    save_app_settings()

def update_sql_schedule_list():
    if "sql_schedule_listbox" not in globals():
        return

    selected_index = sql_schedule_edit_index
    sql_schedule_listbox.delete(0, END)

    for schedule in sql_scheduled_backup_times:
        if isinstance(schedule, dict):
            backup_time = schedule.get("time", "")
            days = ", ".join(schedule.get("days", []))
            sql_schedule_listbox.insert(END, f"{backup_time} | {days}")
        else:
            sql_schedule_listbox.insert(END, schedule)

    if selected_index is not None and 0 <= selected_index < sql_schedule_listbox.size():
        sql_schedule_listbox.selection_set(selected_index)
        sql_schedule_listbox.see(selected_index)

def edit_selected_sql_backup_time(event=None):
    global sql_schedule_edit_index

    selected = sql_schedule_listbox.curselection()

    if not selected:
        return

    index = selected[0]
    schedule = sql_scheduled_backup_times[index]

    if isinstance(schedule, dict):
        backup_time = schedule.get("time", "")
        schedule_days = schedule.get("days", [])
    else:
        backup_time = schedule
        schedule_days = list(sql_selected_days.keys())

    try:
        time_part, ampm = backup_time.split()
        hour, minute = time_part.split(":")
    except ValueError:
        return

    sql_hours_var.set(f"{int(hour):02d}")
    sql_minutes_var.set(minute)
    sql_ampm_var.set(ampm)

    for day, var in sql_selected_days.items():
        var.set(day in schedule_days)

    sql_schedule_edit_index = index
    btn_add_sql_time.config(text="Update Time")

def remove_sql_backup_time():
    global sql_schedule_edit_index

    selected = sql_schedule_listbox.curselection()

    if not selected:
        messagebox.showwarning("No Time Selected", "Select a SQL backup time to remove.")
        return

    index = selected[0]
    sql_scheduled_backup_times.pop(index)
    sql_schedule_edit_index = None
    btn_add_sql_time.config(text="Add Time")

    update_sql_schedule_list()
    save_app_settings()

def run_sql_scheduled_backup_silent():
    if google_drive_status_var.get() != "Google Drive Connected":
        write_scheduler_status("SQL Google Drive backup skipped: Google Drive is not connected.")
        return False

    output_files = backup_selected_sql_databases(show_messages=False)

    if not output_files:
        write_scheduler_status("SQL scheduled backup skipped: no database backup was created.")
        return False

    backup_time = datetime.now()

    for index, output_file in enumerate(output_files, start=1):
        queue_ui_action(
            sql_cloud_action_status_var.set,
            f"Uploading database {index} of {len(output_files)}..."
        )
        try:
            drive_file_id = upload_file_to_google_drive(output_file, backup_time=backup_time)
            message = f"SQL scheduled Google Drive upload complete: {os.path.basename(output_file)}"
            write_scheduler_status(message)
            write_cloud_backup_log("completed", output_file, message, drive_file_id)
            try:
                os.remove(output_file)
            except OSError:
                pass
        except Exception as e:
            message = f"SQL scheduled Google Drive upload failed for {os.path.basename(output_file)}: {e}"
            write_scheduler_status(message)
            write_cloud_backup_log("failed", output_file, message)

    return True

def start_sql_scheduler():
    global sql_scheduler_running

    if not sql_scheduled_backup_times:
        messagebox.showwarning("No SQL Schedule", "Add at least one SQL backup time first.")
        return

    if not get_selected_sql_databases():
        messagebox.showwarning("No Database", "Select or enter at least one SQL database first.")
        return

    if google_drive_status_var.get() != "Google Drive Connected":
        messagebox.showwarning(
            "Google Drive Not Connected",
            "Connect Google Drive before enabling the SQL backup schedule."
        )
        return

    sql_scheduler_running = True
    sql_schedule_status_var.set("Enabled")
    refresh_schedule_tray_icon()
    write_scheduler_status("SQL backup schedule started")
    check_sql_scheduled_backups(schedule_next=False)
    update_sql_scheduler_controls()

def stop_sql_scheduler():
    global sql_scheduler_running

    sql_scheduler_running = False
    sql_schedule_status_var.set("Disabled")
    refresh_schedule_tray_icon()
    write_scheduler_status("SQL backup schedule stopped")
    update_sql_scheduler_controls()

def toggle_sql_scheduler():
    if sql_scheduler_running:
        stop_sql_scheduler()
    else:
        start_sql_scheduler()

def update_cloud_action_buttons(event=None):
    if (
        "btn_cloud_next" not in globals()
        or "btn_sql_cloud_backup" not in globals()
        or "cloud_subtabs" not in globals()
    ):
        return

    selected_tab = cloud_subtabs.select()

    btn_cloud_next.grid_remove()
    btn_cloud_toggle_schedule.grid_remove()
    cloud_drive_location_link.grid_remove()
    cloud_footer_status_label.grid_remove()
    btn_upload_cloud_items.grid_remove()
    cloud_upload_requirement_label.grid_remove()
    sql_cloud_status_label.grid_remove()
    btn_sql_cloud_backup.grid_remove()
    update_cloud_scheduler_controls()

    if selected_tab == str(cloud_location_items_tab):
        cloud_action_row.pack(fill=X, pady=8)
        if btn_upload_cloud_items.cget("state") in (DISABLED, "disabled"):
            cloud_upload_requirement_label.grid(row=0, column=0, sticky="e", padx=(0, 12))
        btn_upload_cloud_items.grid(row=0, column=1, sticky="e")
    elif selected_tab == str(cloud_sql_tab):
        cloud_action_row.pack(fill=X, pady=8)
        sql_cloud_status_label.grid(row=0, column=0, sticky="e", padx=(0, 12))
        btn_sql_cloud_backup.grid(row=0, column=2, sticky="e")
    else:
        cloud_action_row.pack_forget()

def update_cloud_next_button_state():
    update_cloud_action_buttons()

def update_sql_database_button_state():
    if "btn_load_databases" not in globals():
        return

    btn_load_databases.config(
        style="Success.TButton" if len(get_checked_sql_databases()) > 0 else "Accent.TButton"
    )
    update_sql_backup_button_state()

def update_sql_backup_button_state(*args):
    if "btn_sql_cloud_backup" not in globals():
        return

    has_databases = bool(get_selected_sql_databases())
    google_connected = google_drive_status_var.get() == "Google Drive Connected"
    has_server = bool(sql_server_var.get().strip())
    has_staging = bool(sql_staging_destination_var.get().strip())
    missing = []

    if not has_server:
        missing.append("enter a SQL Server")
    if not has_databases:
        missing.append("select a database")
    if not google_connected:
        missing.append("connect Google Drive")
    if not has_staging:
        missing.append("choose a staging folder")

    if missing and "sql_cloud_action_status_var" in globals():
        sql_cloud_action_status_var.set(f"Unavailable: {', '.join(missing)}.")

    btn_sql_cloud_backup.config(
        state=NORMAL if has_server and has_databases and google_connected and has_staging else DISABLED,
        style="Ready.TButton" if has_server and has_databases and google_connected and has_staging else "Accent.TButton"
    )

def update_cloud_selection_count():
    dedupe_cloud_selected_items()

    if "cloud_items_listbox" in globals():
        cloud_items_listbox.delete(0, END)

        for item in cloud_selected_items:
            cloud_items_listbox.insert(END, item)

    cloud_selected_count_var.set(
        f"{len(cloud_selected_items)} cloud item(s) selected"
    )
    update_cloud_upload_button_state()
    update_cloud_next_button_state()

def update_office_preset_caption():
    if office_schedule_var.get():
        office_preset_caption_var.set(
            "Office preset saves Word, Excel, PowerPoint, PDF, text/CSV/RTF, OneNote, and Outlook files from Desktop, Documents, Downloads, and OneDrive."
        )
    else:
        office_preset_caption_var.set("Selected items saves the files and folders listed below.")

def refresh_scheduler_sections():
    update_scheduler_scroll_region()

def set_scheduler_layout(layout_name):
    scheduler_layout_var.set(layout_name)
    refresh_scheduler_sections()

def update_scheduler_tab_layout(event=None):
    if "scheduler_content" not in globals():
        return

    refresh_scheduler_sections()
    update_scheduler_item_buttons_layout()
    update_scheduler_days_layout()
    update_scheduler_control_layout()

def update_scheduler_item_buttons_layout(event=None):
    if "scheduler_items_button_row" not in globals():
        return

    width = scheduler_items_button_row.winfo_width()
    columns = 1 if width < 460 else 4
    arrange_responsive_button_grid(
        scheduler_items_button_row,
        [
            btn_add_scheduler_files,
            btn_add_scheduler_folder,
            btn_remove_scheduler_item,
            btn_clear_scheduler_items
        ],
        columns
    )

    if "schedule_items_button_row" in globals():
        schedule_width = schedule_items_button_row.winfo_width()
        arrange_responsive_button_grid(
            schedule_items_button_row,
            [
                btn_add_schedule_files,
                btn_add_schedule_folder,
                btn_remove_schedule_item,
                btn_clear_schedule_items
            ],
            1 if schedule_width < 460 else 4
        )

    if "auto_scheduler_items_button_row" in globals():
        auto_width = auto_scheduler_items_button_row.winfo_width()
        arrange_responsive_button_grid(
            auto_scheduler_items_button_row,
            [
                btn_add_auto_scheduler_files,
                btn_add_auto_scheduler_folder,
                btn_remove_auto_scheduler_item,
                btn_clear_auto_scheduler_items
            ],
            1 if auto_width < 460 else 4
        )

def update_scheduler_control_layout(event=None):
    if "manual_button_row" not in globals() or "scheduler_button_group" not in globals():
        return

    arrange_responsive_button_grid(
        manual_button_row,
        [btn_add_time, btn_remove_time],
        1 if manual_button_row.winfo_width() < 330 else 2
    )
    arrange_responsive_button_grid(
        scheduler_button_group,
        [btn_toggle_scheduler],
        1
    )

def update_scheduler_days_layout(event=None):
    if "days_frame" not in globals():
        return

    width = days_frame.winfo_width()
    max_columns = 4 if width < 520 else 8

    for index, (day, var) in enumerate(selected_days.items()):
        checkbutton = scheduler_day_checkbuttons[day]
        row = 1 + index // max_columns
        column = index % max_columns
        checkbutton.grid(row=row, column=column, sticky="w", padx=(0, 10), pady=(4, 0))

    if "auto_days_frame" in globals():
        auto_width = auto_days_frame.winfo_width()
        auto_max_columns = 4 if auto_width < 520 else 8

        for index, (day, var) in enumerate(auto_selected_days.items()):
            checkbutton = auto_scheduler_day_checkbuttons[day]
            row = 1 + index // auto_max_columns
            column = index % auto_max_columns
            checkbutton.grid(row=row, column=column, sticky="w", padx=(0, 10), pady=(4, 0))

def update_auto_recurrence_fields(event=None):
    if "auto_interval_group" not in globals():
        return

    recurrence = auto_backup_recurrence_var.get()

    if recurrence in ("Minutes", "Every X minutes"):
        auto_interval_group.pack(side=LEFT)
        auto_time_group.grid_remove()
        auto_month_day_group.grid_remove()
        auto_days_frame.grid_remove()
    elif recurrence == "Daily":
        auto_interval_group.pack_forget()
        auto_time_group.grid_remove()
        auto_month_day_group.grid_remove()
        auto_days_frame.grid()
    elif recurrence == "Monthly":
        auto_interval_group.pack_forget()
        auto_time_group.grid()
        auto_month_day_group.grid()
        auto_days_frame.grid_remove()
    else:
        auto_interval_group.pack_forget()
        auto_time_group.grid()
        auto_month_day_group.grid_remove()
        auto_days_frame.grid()

    update_scheduler_scroll_region()

def update_scheduler_scroll_region(event=None):
    if "scheduler_canvas" not in globals():
        return

    scheduler_canvas.update_idletasks()
    scroll_region = scheduler_canvas.bbox("all")
    scheduler_canvas.configure(scrollregion=scroll_region)

    canvas_width = scheduler_canvas.winfo_width()
    canvas_height = scheduler_canvas.winfo_height()
    # Match the canvas width so Schedule controls shrink and reflow with the window.
    content_width = max(canvas_width, 1)
    scheduler_canvas.itemconfigure(scheduler_content_window, width=content_width)

    if scroll_region:
        _, _, scroll_width, scroll_height = scroll_region
        horizontal_needed = scroll_width > canvas_width + 2
        vertical_needed = scroll_height > canvas_height + 2
    else:
        horizontal_needed = False
        vertical_needed = False

    if vertical_needed:
        scheduler_vertical_scrollbar.grid(row=0, column=1, sticky="ns")
    else:
        scheduler_vertical_scrollbar.grid_remove()

    if horizontal_needed:
        scheduler_horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
    else:
        scheduler_horizontal_scrollbar.grid_remove()

def scroll_scheduler_canvas(event):
    if "scheduler_canvas" not in globals():
        return

    scheduler_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

def bind_scheduler_mousewheel(event=None):
    if "scheduler_canvas" in globals():
        scheduler_canvas.bind_all("<MouseWheel>", scroll_scheduler_canvas)

def unbind_scheduler_mousewheel(event=None):
    if "scheduler_canvas" in globals():
        scheduler_canvas.unbind_all("<MouseWheel>")

def create_scheduler_section_header(parent, section_key, title):
    header = ttk.Frame(parent, style="Card.TFrame")
    header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
    header.columnconfigure(0, weight=1)

    ttk.Label(
        header,
        text=title,
        style="CardTitle.TLabel"
    ).grid(row=0, column=0, sticky="w")

    return header

def refresh_cloud_sections():
    if "cloud_top_split" not in globals() or "cloud_bottom_split" not in globals():
        return

    for pane in cloud_top_split.panes():
        cloud_top_split.forget(pane)

    for pane in cloud_bottom_split.panes():
        cloud_bottom_split.forget(pane)

    min_sizes = {
        "cloud_items": 320,
        "google_drive": 300,
        "sql": 320,
        "cloud_schedule": 360
    }

    for index, section_key in enumerate(cloud_section_order):
        section = cloud_section_widgets.get(section_key)

        if not section:
            continue

        target_split = cloud_top_split if index < 2 else cloud_bottom_split
        target_split.add(section, minsize=min_sizes.get(section_key, 260))

def move_cloud_section(section_key, direction):
    if section_key not in cloud_section_order:
        return

    current_index = cloud_section_order.index(section_key)
    new_index = current_index + direction

    if new_index < 0 or new_index >= len(cloud_section_order):
        return

    cloud_section_order[current_index], cloud_section_order[new_index] = (
        cloud_section_order[new_index],
        cloud_section_order[current_index]
    )
    refresh_cloud_sections()

def create_cloud_section_header(parent, section_key, title, manager="grid", columnspan=3):
    header = ttk.Frame(parent, style="Card.TFrame")
    header.columnconfigure(0, weight=1)

    if manager == "pack":
        header.pack(fill=X, pady=(0, 6))
    else:
        header.grid(row=0, column=0, columnspan=columnspan, sticky="ew", pady=(0, 6))

    ttk.Label(
        header,
        text=title,
        background=CARD,
        foreground=TEXT,
        font=("Segoe UI", 12, "bold")
    ).grid(row=0, column=0, sticky="w")

#    ttk.Button(
#        header,
#        text="Move Up",
#        width=10,
#       command=lambda: move_cloud_section(section_key, -1)
#    ).grid(row=0, column=1, sticky="e", padx=(8, 0))

#    ttk.Button(
#        header,
#        text="Move Down",
#        width=11,
#        command=lambda: move_cloud_section(section_key, 1)
#    ).grid(row=0, column=2, sticky="e", padx=(8, 0))

    return header

def update_dashboard_card_layout(event=None):
    if "dashboard_top_row" not in globals() or "dashboard_schedule_card" not in globals():
        return

    width = dashboard_top_row.winfo_width()
    visible_cards = [dashboard_status_card, dashboard_cloud_card]

    if has_any_schedules_set():
        visible_cards.insert(1, dashboard_schedule_card)

    for card in (dashboard_status_card, dashboard_schedule_card, dashboard_cloud_card):
        card.grid_forget()

    if width < 900:
        for index, card in enumerate(visible_cards):
            card.grid(row=index, column=0, sticky="ew", pady=(0, 8))

        dashboard_top_row.columnconfigure(0, weight=1)
        dashboard_top_row.columnconfigure(1, weight=0)
        dashboard_top_row.columnconfigure(2, weight=0)
    else:
        for index, card in enumerate(visible_cards):
            if len(visible_cards) == 1:
                padx = 0
            elif index == 0:
                padx = (0, 8)
            elif index == len(visible_cards) - 1:
                padx = (8, 0)
            else:
                padx = 8

            card.grid(row=0, column=index, sticky="nsew", padx=padx)

        for column in range(3):
            dashboard_top_row.columnconfigure(
                column,
                weight=1 if column < len(visible_cards) else 0
            )

def arrange_schedule_card(card, listbox, days_frame, row):
    """Keep schedules in one full-width column at every window size."""
    listbox.grid(row=row, column=0, columnspan=5, rowspan=1,
                 sticky="ew", padx=0, pady=(8, 0))
    card.columnconfigure(4, weight=1)
    days_frame.grid_configure(sticky="ew")
    days = days_frame.winfo_children()
    day_width = max((day.winfo_reqwidth() + 10 for day in days), default=70)
    columns = max(1, min(7, (card.winfo_width() - 32) // day_width))
    for index, day in enumerate(days):
        day.grid(row=index // columns, column=index % columns,
                 sticky="w", padx=(0, 10), pady=(0, 4))


def update_cloud_schedule_layout(event=None):
    if "cloud_schedule_listbox" in globals():
        arrange_schedule_card(cloud_schedule_card, cloud_schedule_listbox,
                              cloud_days_frame, 8)


def update_sql_schedule_layout(event=None):
    if "sql_schedule_listbox" in globals():
        arrange_schedule_card(sql_schedule_card, sql_schedule_listbox,
                              sql_days_frame, 7)


def walk_widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from walk_widgets(child)

def create_scrollable_tab_content(parent):
    """Create a width-following, vertically scrollable content area for a tab."""
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(0, weight=1)

    canvas = Canvas(parent, bg=BG, highlightthickness=0, bd=0)
    scrollbar = Scrollbar(parent, orient=VERTICAL, command=canvas.yview)
    content = ttk.Frame(canvas)
    content_window = canvas.create_window((0, 0), window=content, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    def sync_scroll_area(event=None):
        canvas_width = max(canvas.winfo_width(), 1)
        canvas.itemconfigure(content_window, width=canvas_width)
        canvas.configure(scrollregion=canvas.bbox("all"))

        for widget in (content, *tuple(walk_widgets(content))):
            if not getattr(widget, "_cloud_wheel_bound", False):
                widget.bind("<MouseWheel>", scroll_content, add="+")
                widget._cloud_wheel_bound = True

        if content.winfo_reqheight() > canvas.winfo_height() + 2:
            scrollbar.grid()
        else:
            scrollbar.grid_remove()
            canvas.yview_moveto(0)

    def scroll_content(event):
        # Let inputs and nested lists handle their own wheel gestures.
        if isinstance(event.widget, (Listbox, Text, ttk.Combobox, ttk.Spinbox)):
            return
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    content.bind("<Configure>", sync_scroll_area)
    canvas.bind("<Configure>", sync_scroll_area)
    canvas.bind("<MouseWheel>", scroll_content, add="+")
    return content

def update_responsive_tab_layout(event=None):
    """Refresh reflow rules and wrap long captions to their current card width."""
    root._responsive_layout_after = None
    update_dashboard_card_layout()
    update_backup_tab_layout()
    update_cloud_items_layout()
    update_sql_card_layout()
    update_google_drive_layout()
    update_cloud_schedule_layout()
    update_sql_schedule_layout()
    update_scheduler_tab_layout()

    for tab in (dashboard_tab, backup_tab, scheduler_tab, cloud_tab, settings_tab):
        for widget in walk_widgets(tab):
            if not isinstance(widget, ttk.Label):
                continue
            try:
                current_wrap = int(widget.cget("wraplength"))
            except (TclError, ValueError, TypeError):
                continue
            if current_wrap <= 0:
                continue
            if not hasattr(widget, "_responsive_max_wrap"):
                widget._responsive_max_wrap = current_wrap
            available = max(180, widget.master.winfo_width() - 24)
            target = min(widget._responsive_max_wrap, available)
            if abs(current_wrap - target) > 2:
                widget.configure(wraplength=target)

def queue_responsive_tab_layout(event=None):
    pending = getattr(root, "_responsive_layout_after", None)
    if pending is not None:
        root.after_cancel(pending)
    root._responsive_layout_after = root.after(40, update_responsive_tab_layout)

def arrange_responsive_button_grid(frame, buttons, columns):
    # Use actual font/DPI measurements, then give every button equal space.
    required_width = max((button.winfo_reqwidth() for button in buttons), default=120)
    available = max(1, frame.winfo_width())
    columns = max(1, min(len(buttons), (available + 8) // (required_width + 8)))
    layout = (tuple(buttons), columns)
    if getattr(frame, "_button_layout", None) == layout:
        return
    frame._button_layout = layout

    for child in frame.winfo_children():
        child.pack_forget()
        child.grid_forget()

    # Clear weights left behind by a previous, wider column arrangement.
    for column in range(max(len(buttons), columns)):
        frame.columnconfigure(column, weight=0, minsize=0, uniform="")

    for index, button in enumerate(buttons):
        row = index // columns
        column = index % columns
        padx = (0, 8) if column < columns - 1 else (0, 0)
        pady = (0, 8) if row < (len(buttons) - 1) // columns else (0, 0)
        button.grid(row=row, column=column, sticky="ew", padx=padx, pady=pady)

    for column in range(columns):
        frame.columnconfigure(column, weight=1, minsize=0, uniform="actions")

def update_backup_tab_layout(event=None):
    if "button_row" not in globals() or "btn_clear_list" not in globals():
        return

    width = button_row.winfo_width()
    arrange_responsive_button_grid(
        button_row,
        [btn_add_files, btn_add_folder, btn_remove_backup_items, btn_clear_list],
        2 if width < 620 else 4
    )

def update_cloud_items_layout(event=None):
    if "cloud_items_button_row" not in globals() or "btn_clear_cloud_items" not in globals():
        return

    width = cloud_items_button_row.winfo_width()
    arrange_responsive_button_grid(
        cloud_items_button_row,
        [btn_select_cloud_items, btn_remove_cloud_items, btn_clear_cloud_items],
        1 if width < 430 else 3
    )

def update_sql_card_layout(event=None):
    if "sql_button_row" not in globals() or "btn_load_databases" not in globals():
        return

    width = sql_button_row.winfo_width()
    arrange_responsive_button_grid(
        sql_button_row,
        [btn_load_databases, btn_test_sql, btn_find_servers],
        1 if width < 470 else 3
    )

def update_google_drive_layout(event=None):
    if "google_drive_button_row" not in globals() or "btn_disconnect_google" not in globals():
        return

    width = google_drive_button_row.winfo_width()
    arrange_responsive_button_grid(
        google_drive_button_row,
        [btn_connect_google, btn_test_google, btn_disconnect_google],
        1 if width < 430 else 3
    )

def check_cloud_scheduled_backups(schedule_next=True):
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
            cloud_status_var.set("Enabled")
            refresh_schedule_tray_icon()

    if schedule_next:
        root.after(SCHEDULER_POLL_INTERVAL_MS, check_cloud_scheduled_backups)

def check_sql_scheduled_backups(schedule_next=True):
    global sql_last_run_time

    if sql_scheduler_running:
        current_time = datetime.now().strftime("%I:%M %p").lstrip("0")
        today = datetime.now().strftime("%a")

        for schedule in sql_scheduled_backup_times:
            if isinstance(schedule, dict):
                schedule_time = schedule.get("time")
                schedule_days = schedule.get("days", [])
                should_run = current_time == schedule_time and today in schedule_days
            else:
                should_run = current_time == schedule and sql_selected_days[today].get()

            if should_run and current_time != sql_last_run_time:
                sql_last_run_time = current_time
                sql_schedule_status_var.set("Running Backup")
                update_tray_icon(CLOUD_SCHEDULE_COLOR)

                backup_thread = threading.Thread(target=run_sql_scheduled_backup_silent)
                backup_thread.daemon = True
                backup_thread.start()
                break

        else:
            sql_schedule_status_var.set("Enabled")
            refresh_schedule_tray_icon()

    if schedule_next:
        root.after(SCHEDULER_POLL_INTERVAL_MS, check_sql_scheduled_backups)

# =========================================================
# Tkinter App Bootstrap
# =========================================================

relaunch_as_admin_if_needed()
ensure_single_instance()
hide_console_window()

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

auto_selected_days = {
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

sql_selected_days = {
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
if TEST_MODE:
    root.title(f"{APP_NAME} v{APP_VERSION} — Testing")
configure_main_window()


# Tkinter variables shared by callbacks and UI widgets.
apply_modern_style()
destination_var = StringVar()
backup_description_var = StringVar()
manual_backup_destination_var = StringVar()
auto_backup_destination_var = StringVar()
schedule_destination_caption_var = StringVar()
auto_backup_destination_var.trace_add("write", refresh_schedule_destination_caption)
sql_staging_destination_var = StringVar(value=os.path.join(app_data_folder, "sql_staging"))
format_var = StringVar(value="zip")
sql_server_var = StringVar(value=r".\SQLEXPRESS")
sql_database_var = StringVar(value="BackupCompressorTest")
sql_database_var.trace_add("write", lambda *args: update_cloud_action_buttons())
sql_database_var.trace_add("write", update_sql_backup_button_state)
sql_database_vars = {}
sql_include_scheduler_var = BooleanVar(value=False)
sql_selected_count_var = StringVar(value="0 databases selected")
sql_connection_status_var = StringVar(value="SQL Not Connected")
sql_cloud_action_status_var = StringVar(value="Select databases and connect Google Drive.")
sql_server_var.trace_add("write", lambda *args: sql_connection_status_var.set("SQL Not Connected"))
sql_server_var.trace_add("write", update_sql_backup_button_state)
sql_staging_destination_var.trace_add("write", update_sql_backup_button_state)

google_drive_status_var = StringVar(value="Not Connected")
cloud_schedule_enabled_var = BooleanVar(value=False)
google_drive_folder_var = StringVar(value="My Drive")
google_drive_folder_var.trace_add("write", update_cloud_upload_button_state)
cloud_backup_description_var = StringVar()
cloud_selected_count_var = StringVar(value="0 cloud item(s) selected")
cloud_scheduled_backup_times = []

cloud_hours_var = StringVar(value="12")
cloud_minutes_var = StringVar(value="00")
cloud_ampm_var = StringVar(value="AM")
cloud_scheduler_running = False
cloud_last_run_time = None
cloud_status_var = StringVar(value="Disabled")

sql_scheduled_backup_times = []
sql_hours_var = StringVar(value="12")
sql_minutes_var = StringVar(value="00")
sql_ampm_var = StringVar(value="AM")
sql_scheduler_running = False
sql_last_run_time = None
sql_schedule_status_var = StringVar(value="Disabled")

run_on_startup_var = BooleanVar(value=False)
show_cloud_tab_var = BooleanVar(value=False)

schedule_time_var = StringVar()
scheduler_status_var = StringVar(value="Stopped")
schedule_name_var = StringVar(value="Local Backup")
schedule_description_var = StringVar(value="Files/Folders backup")
office_schedule_var = BooleanVar(value=False)
office_preset_caption_var = StringVar(value="")
auto_backup_interval_var = StringVar(value="10")
auto_backup_recurrence_var = StringVar(value="Minutes")
auto_schedule_name_var = StringVar(value="Auto Backup")
auto_schedule_description_var = StringVar(value="Automatic backup")
auto_office_schedule_var = BooleanVar(value=False)
auto_hours_var = StringVar(value="12")
auto_minutes_var = StringVar(value="00")
auto_ampm_var = StringVar(value="AM")
auto_month_day_var = StringVar(value="1")
scheduler_selected_count_var = StringVar(value="0 file(s), 0 folder(s) selected")
auto_selected_count_var = StringVar(value="0 file(s), 0 folder(s) selected")
scheduler_layout_var = StringVar(value="Balanced split")

progress_var = DoubleVar(value=0)
status_var = StringVar(value="Ready")
summary_var = StringVar(value="Selected: 0 files | Total size: 0 B")
dashboard_progress_var = DoubleVar(value=0)
dashboard_status_var = StringVar(value="Ready")
dashboard_last_backup_var = StringVar(value="No backup events yet")
dashboard_schedule_var = StringVar(value="Stopped")
dashboard_cloud_var = StringVar(value="Disabled")
dashboard_google_var = StringVar(value="Google: Not Connected")
dashboard_sql_var = StringVar(value="SQL: Not Connected")
dashboard_current_backup_events = []

# Main tab container.
notebook_shell = Canvas(
    root,
    bg=BG,
    highlightthickness=0,
    bd=0
)
notebook_shell.pack(fill=BOTH, expand=True, padx=10, pady=10)
notebook_shell_inner = ttk.Frame(notebook_shell, style="Card.TFrame")
notebook_shell_window = notebook_shell.create_window(
    (5, 5),
    window=notebook_shell_inner,
    anchor="nw"
)
notebook_shell.bind("<Configure>", update_main_notebook_shell)

main_tabs_layout = ttk.Frame(notebook_shell_inner, style="Card.TFrame")
main_tabs_layout.pack(fill=BOTH, expand=True)

main_tabs_nav = ttk.Frame(main_tabs_layout, style="Card.TFrame", width=170)
main_tabs_nav.pack(side=LEFT, fill=Y, padx=(8, 10), pady=8)
main_tabs_nav.pack_propagate(False)

ttk.Label(main_tabs_nav, text="BACKUP\nCOMPRESSOR", style="CardTitle.TLabel").pack(
    anchor="w", padx=10, pady=(12, 24)
)

notebook = ttk.Notebook(main_tabs_layout)
notebook.pack_propagate(False)
notebook.configure(style="Content.TNotebook")
notebook.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 8), pady=8)
notebook.bind("<Configure>", queue_responsive_tab_layout)

dashboard_tab = ttk.Frame(notebook, padding=12)
backup_tab = ttk.Frame(notebook, padding=12)
scheduler_tab = ttk.Frame(notebook, padding=12)
cloud_tab = ttk.Frame(notebook, padding=12)
settings_tab = ttk.Frame(notebook, padding=12)
create_page_header(settings_tab, "Settings", "Manage your workspace and review backup logs.")
settings_notebook = ttk.Notebook(settings_tab)
settings_notebook.pack(fill=BOTH, expand=True)
preferences_tab = ttk.Frame(settings_notebook, padding=16)
logs_tab = ttk.Frame(settings_notebook, padding=16)
settings_notebook.add(preferences_tab, text="General")
settings_notebook.add(logs_tab, text="Logs")
settings_notebook.bind("<<NotebookTabChanged>>", lambda event: refresh_logs_tab())
preferences_card = ttk.Frame(preferences_tab, style="Card.TFrame", padding=16)
preferences_card.pack(fill=X)
ttk.Label(preferences_card, text="Navigation", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 12))
ttk.Checkbutton(preferences_card, text="Show Cloud Backup tab", variable=show_cloud_tab_var,
                command=toggle_cloud_tab_visibility).pack(anchor="w")
ttk.Label(preferences_card, text="Show or hide Google Drive and SQL backup controls. Existing schedules are unchanged.",
          style="Muted.TLabel", wraplength=480).pack(anchor="w", pady=(8, 0))
instructions_tab = ttk.Frame(notebook, padding=12)
notifications_tab = ttk.Frame(notebook, padding=12)

# Notebook tabs.
notebook.add(backup_tab, text="Backup")
notebook.add(scheduler_tab, text="Schedule")
notebook.add(cloud_tab, text="Cloud Backup")
notebook.add(dashboard_tab, text="Dashboard")
notebook.add(settings_tab, text="Settings")
notebook.add(instructions_tab, text="Instructions")
notebook.add(notifications_tab, text="Notifications")
notebook.bind("<<NotebookTabChanged>>", update_main_tab_buttons)

for tab_text, tab_frame in (
    ("Backup", backup_tab),
    ("Schedule", scheduler_tab),
    ("Cloud Backup", cloud_tab),
    ("Dashboard", dashboard_tab),
    ("Settings", settings_tab),
):
    create_main_tab_button(main_tabs_nav, tab_text, tab_frame)

main_tabs_spacer = ttk.Frame(main_tabs_nav, style="Card.TFrame")
main_tabs_spacer.pack(fill=BOTH, expand=True)

btn_notifications = ttk.Button(
    main_tabs_nav,
    text="Notifications",
    command=show_notification_history
)
btn_notifications.pack(fill=X, padx=6, pady=(0, 8))

btn_instructions = ttk.Button(
    main_tabs_nav,
    text="📖  Instructions",
    command=open_instructions
)
btn_instructions.pack(fill=X, padx=6, pady=(0, 8))

root.after_idle(update_main_tab_buttons)

# =========================================================
# In-App Notifications
# =========================================================

notifications_header = ttk.Frame(notifications_tab)
notifications_header.pack(fill=X, pady=(0, 10))
ttk.Label(
    notifications_header,
    text="Notifications",
    font=("Segoe UI", 16, "bold")
).pack(side=LEFT)
ttk.Button(
    notifications_header,
    text="Back",
    command=close_notification_history
).pack(side=RIGHT)

notifications_body = ttk.Frame(notifications_tab, style="Card.TFrame", padding=16)
notifications_body.pack(fill=BOTH, expand=True)
notification_drawer_text = Text(
    notifications_body,
    bg=CARD_DARK,
    fg=TEXT,
    insertbackground=TEXT,
    font=("Segoe UI", 10),
    relief=FLAT,
    wrap=WORD,
    padx=8,
    pady=8,
    state=DISABLED,
    cursor="arrow"
)
notification_drawer_text.bind("<MouseWheel>", scroll_notification_drawer)
notification_drawer_text.pack(fill=BOTH, expand=True)
root.after_idle(refresh_notification_drawer)

# =========================================================
# In-App Instructions
# =========================================================

instructions_header = ttk.Frame(instructions_tab)
instructions_header.pack(fill=X, pady=(0, 10))
ttk.Label(
    instructions_header,
    text="📖 Help & Instructions",
    font=("Segoe UI", 16, "bold")
).pack(side=LEFT)
ttk.Button(
    instructions_header,
    text="Back",
    command=close_instructions
).pack(side=RIGHT)

instructions_toolbar = ttk.Frame(instructions_tab, style="Card.TFrame", padding=16)
instructions_toolbar.pack(fill=X, pady=(0, 8))
ttk.Label(instructions_toolbar, text="Help for:").pack(side=LEFT, padx=(0, 8))
instructions_topic_var = StringVar(value="Backup")
instructions_topic_dropdown = ttk.Combobox(
    instructions_toolbar,
    textvariable=instructions_topic_var,
    values=list(TAB_INSTRUCTIONS.keys()),
    state="readonly",
    width=20
)
instructions_topic_dropdown.pack(side=LEFT)
instructions_topic_dropdown.bind("<<ComboboxSelected>>", refresh_instructions)

instructions_body = ttk.Frame(instructions_tab, style="Card.TFrame", padding=16)
instructions_body.pack(fill=BOTH, expand=True)
instructions_text = Text(
    instructions_body,
    bg=INPUT_BG,
    fg=TEXT,
    insertbackground=TEXT,
    font=("Segoe UI", 11),
    relief=FLAT,
    wrap=WORD,
    padx=14,
    pady=14,
    spacing1=2,
    spacing3=5,
    state=DISABLED,
    cursor="arrow"
)
instructions_scrollbar = Scrollbar(
    instructions_body,
    orient=VERTICAL,
    command=instructions_text.yview
)
instructions_text.configure(yscrollcommand=instructions_scrollbar.set)
instructions_scrollbar.pack(side=RIGHT, fill=Y)
instructions_text.pack(side=LEFT, fill=BOTH, expand=True)
root.after_idle(refresh_instructions)

# =========================================================
# Dashboard Tab
# =========================================================

create_page_header(dashboard_tab, "Activity overview", "Check recent backups, schedules, and connections.")
dashboard_top_row = ttk.Frame(dashboard_tab)
dashboard_top_row.pack(fill=X, pady=(0, 6))
dashboard_top_row.columnconfigure(0, weight=1)
dashboard_top_row.columnconfigure(1, weight=1)
dashboard_top_row.columnconfigure(2, weight=1)
dashboard_top_row.bind("<Configure>", update_dashboard_card_layout)

dashboard_status_card = ttk.Frame(dashboard_top_row, style="Card.TFrame", padding=16)
dashboard_status_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

ttk.Label(
    dashboard_status_card,
    text="Current Backup",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 6))

dashboard_current_backup_label = ttk.Label(
    dashboard_status_card,
    textvariable=dashboard_status_var,
    background=CARD,
    foreground="#57f287",
    font=("Segoe UI", 10, "bold"),
    justify=LEFT,
    wraplength=300,
    cursor="hand2"
)
dashboard_current_backup_label.pack(anchor="w", pady=(0, 8))
dashboard_current_backup_label.bind("<Button-1>", open_clicked_dashboard_current_backup)

dashboard_schedule_card = ttk.Frame(dashboard_top_row, style="Card.TFrame", padding=16)
dashboard_schedule_card.grid(row=0, column=1, sticky="nsew", padx=8)

ttk.Label(
    dashboard_schedule_card,
    text="Schedules Set",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 6))

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

dashboard_cloud_card = ttk.Frame(dashboard_top_row, style="Card.TFrame", padding=16)
dashboard_cloud_card.grid(row=0, column=2, sticky="nsew", padx=(8, 0))

ttk.Label(
    dashboard_cloud_card,
    text="Connections",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 6))

ttk.Label(
    dashboard_cloud_card,
    textvariable=dashboard_google_var,
    background=CARD,
    foreground="#57f287"
).pack(anchor="w")

ttk.Label(
    dashboard_cloud_card,
    textvariable=dashboard_sql_var,
    background=CARD,
    foreground="#57f287"
).pack(anchor="w", pady=(6, 0))

dashboard_last_card = ttk.Frame(dashboard_tab, style="Card.TFrame", padding=16)
dashboard_last_card.pack(fill=X, pady=(0, 6))

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

dashboard_history_card = ttk.Frame(dashboard_tab, style="Card.TFrame", padding=16)
dashboard_history_card.pack(fill=BOTH, expand=True)

ttk.Label(
    dashboard_history_card,
    text="Backup History",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 6))

dashboard_history_frame = ttk.Frame(dashboard_history_card, style="Card.TFrame")
dashboard_history_frame.pack(fill=BOTH, expand=True)

dashboard_history = ttk.Treeview(
    dashboard_history_frame,
    columns=("time", "type", "status", "format", "file", "message", "path", "destination"),
    show="headings",
    height=9,
    selectmode="extended"
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
configure_auto_hide_scrollbar(
    dashboard_history,
    dashboard_history_scrollbar,
    VERTICAL,
    "pack",
    {"side": RIGHT, "fill": Y}
)

dashboard_history_button_row = ttk.Frame(dashboard_history_card, style="Card.TFrame")
dashboard_history_button_row.pack(fill=X, pady=(6, 0))

btn_open_dashboard_backup = ttk.Button(
    dashboard_history_button_row,
    text="Open Location",
    width=BTN_WIDTH,
    command=open_selected_dashboard_backup
)
btn_open_dashboard_backup.pack(side=LEFT)

btn_delete_dashboard_history = ttk.Button(
    dashboard_history_button_row,
    text="Delete Selected",
    width=BTN_WIDTH,
    command=delete_selected_dashboard_history
)
btn_delete_dashboard_history.pack(side=LEFT, padx=(8, 0))

main_buttons.extend([
    btn_open_dashboard_backup,
    btn_delete_dashboard_history
])

# =========================================================
# Logs Tab
# =========================================================

create_page_header(logs_tab, "Backup logs", "Review the details of completed and failed operations.")
logs_card = ttk.Frame(logs_tab, style="Card.TFrame", padding=16)
logs_card.pack(fill=BOTH, expand=True)

ttk.Label(
    logs_card,
    text="Backup Logs",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 6))

log_text = Text(
    logs_card,
    wrap=WORD,
    bg=INPUT_BG,
    fg=TEXT,
    insertbackground=TEXT,
    font=("Consolas", 10),
    relief=FLAT
)
log_text.pack(side=LEFT, fill=BOTH, expand=True)
log_text.config(state=DISABLED)

log_scrollbar = Scrollbar(logs_card)
configure_auto_hide_scrollbar(
    log_text,
    log_scrollbar,
    VERTICAL,
    "pack",
    {"side": RIGHT, "fill": Y}
)

logs_button_row = ttk.Frame(logs_tab)
logs_button_row.pack(fill=X, pady=(6, 0))

btn_view_log = ttk.Button(logs_button_row, text="Refresh Backup Logs", command=refresh_logs_tab)
btn_view_log.pack(side=LEFT)

main_buttons.append(btn_view_log)

# =========================================================
# Cloud Backup Tab: Step Layout
# =========================================================

create_page_header(cloud_tab, "Cloud backup", "Save files or SQL databases to your Google Drive.")
cloud_subtab_nav = ttk.Frame(cloud_tab)
cloud_subtab_nav.pack(fill=X, pady=(0, 8))

cloud_subtabs = ttk.Notebook(cloud_tab)
cloud_subtabs.configure(style="Content.TNotebook")
cloud_subtabs.pack(fill=BOTH, expand=True, pady=(0, 6))
cloud_subtabs.bind("<<NotebookTabChanged>>", update_cloud_subtab_buttons)
cloud_subtabs.bind("<<NotebookTabChanged>>", update_cloud_action_buttons, add="+")

cloud_location_items_tab = ttk.Frame(cloud_subtabs)
cloud_sql_tab = ttk.Frame(cloud_subtabs)

for cloud_subtab in (cloud_location_items_tab, cloud_sql_tab):
    cloud_subtab.columnconfigure(0, weight=1)
    cloud_subtab.rowconfigure(0, weight=1)

cloud_location_content = create_scrollable_tab_content(cloud_location_items_tab)
cloud_sql_content = create_scrollable_tab_content(cloud_sql_tab)

cloud_subtabs.add(cloud_location_items_tab, text="Cloud")
cloud_subtabs.add(cloud_sql_tab, text="SQL")

for tab_text, tab_frame in (
    ("Cloud", cloud_location_items_tab),
    ("SQL", cloud_sql_tab),
):
    create_cloud_subtab_button(cloud_subtab_nav, tab_text, tab_frame)

root.after_idle(update_cloud_subtab_buttons)

# =========================================================
# Cloud Backup Tab: File Selection
# =========================================================

cloud_files_card = ttk.Frame(cloud_location_content, style="Card.TFrame", padding=16)
cloud_files_card.pack(fill=X, pady=(0, 6))

create_cloud_section_header(
    cloud_files_card,
    "cloud_items",
    "2. Choose files and folders",
    manager="pack"
)

cloud_items_button_row = ttk.Frame(cloud_files_card, style="Card.TFrame")
cloud_items_button_row.pack(fill=X, pady=(0, 6))
cloud_items_button_row.bind("<Configure>", update_cloud_items_layout)

btn_select_cloud_items = ttk.Button(
    cloud_items_button_row,
    text="Add Cloud Items",
    command=add_cloud_backup_items
)

btn_remove_cloud_items = ttk.Button(
    cloud_items_button_row,
    text="Remove Selected",
    command=remove_selected_cloud_items
)

btn_clear_cloud_items = ttk.Button(
    cloud_items_button_row,
    text="Clear List",
    command=clear_cloud_items
)

cloud_items_list_frame = Frame(cloud_files_card, bg=CARD)
cloud_items_list_frame.pack(fill=X)

cloud_items_listbox = Listbox(
    cloud_items_list_frame,
    bg=INPUT_BG,
    fg=TEXT,
    selectbackground=ACCENT,
    selectforeground=TEXT,
    font=("Segoe UI", 10),
    relief=FLAT,
    height=8,
    selectmode=EXTENDED
)
cloud_items_listbox.pack(side=LEFT, fill=X, expand=True)

cloud_items_scrollbar = Scrollbar(cloud_items_list_frame)
configure_auto_hide_scrollbar(
    cloud_items_listbox,
    cloud_items_scrollbar,
    VERTICAL,
    "pack",
    {"side": RIGHT, "fill": Y}
)

cloud_selected_count_label = ttk.Label(
    cloud_files_card,
    textvariable=cloud_selected_count_var,
    background=CARD,
    foreground=MUTED
)
cloud_selected_count_label.pack(anchor="w", pady=(5, 0))

main_buttons.extend([
    btn_select_cloud_items,
    btn_remove_cloud_items,
    btn_clear_cloud_items
])

# =========================================================
# Cloud Backup Tab: Google Drive Location
# =========================================================

google_drive_card = ttk.Frame(cloud_location_content, style="Card.TFrame", padding=16)
google_drive_card.columnconfigure(1, weight=1)
google_drive_card.pack(fill=X, pady=(0, 6), before=cloud_files_card)

create_cloud_section_header(
    google_drive_card,
    "google_drive",
    "1. Connect Google Drive",
    columnspan=3
)

google_drive_caption = ttk.Label(
    google_drive_card,
    text="Connect your Google Drive account, choose the Drive folder name, then send selected cloud items immediately or let enabled schedules upload them automatically.",
    style="Muted.TLabel",
    wraplength=760,
    justify=LEFT
)
google_drive_caption.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))

google_drive_folder_label = ttk.Label(
    google_drive_card,
    text="Drive folder:",
    background=CARD,
    foreground=TEXT
)
google_drive_folder_label.grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)

google_drive_folder_entry = ttk.Entry(
    google_drive_card,
    textvariable=google_drive_folder_var,
    width=36
)
google_drive_folder_entry.grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=5)

cloud_backup_description_label = ttk.Label(
    google_drive_card,
    text="Description (optional):",
    background=CARD,
    foreground=TEXT
)
cloud_backup_description_label.grid(row=3, column=0, sticky="w", padx=(0, 8), pady=5)

cloud_backup_description_entry = ttk.Entry(
    google_drive_card,
    textvariable=cloud_backup_description_var,
    width=36
)
cloud_backup_description_entry.grid(row=3, column=1, sticky="ew", padx=(0, 8), pady=5)

google_drive_status_label = ttk.Label(
    google_drive_card,
    textvariable=google_drive_status_var,
    background=CARD,
    foreground="#57f287",
    font=("Segoe UI", 9, "bold")
)
google_drive_status_label.grid(row=4, column=0, columnspan=3, sticky="w", pady=(5, 0))

google_drive_button_row = ttk.Frame(google_drive_card, style="Card.TFrame")
google_drive_button_row.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(5, 0))
google_drive_button_row.bind("<Configure>", update_google_drive_layout)

btn_connect_google = ttk.Button(
    google_drive_button_row,
    text="Connect Google Drive",
    command=connect_google_drive
)

btn_test_google = ttk.Button(
    google_drive_button_row,
    text="Upload Test",
    command=upload_test_to_google_drive
)

btn_disconnect_google = ttk.Button(
    google_drive_button_row,
    text="Disconnect",
    command=disconnect_google_drive
)

main_buttons.extend([
    btn_connect_google,
    btn_test_google,
    btn_disconnect_google
])

# =========================================================
# Cloud Backup Tab: SQL Settings
# =========================================================

sql_card = ttk.Frame(cloud_sql_content, style="Card.TFrame", padding=16)
sql_card.columnconfigure(1, weight=1)
sql_card.pack(fill=X, pady=(0, 6))

create_cloud_section_header(
    sql_card,
    "sql",
    "SQL Backup Settings",
    columnspan=3
)

ttk.Label(
    sql_card,
    text="Create a temporary SQL .bak staging file, upload it to Google Drive, then remove the local staging copy after a successful upload.",
    style="Muted.TLabel",
    wraplength=760,
    justify=LEFT
).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))

ttk.Label(sql_card, text="Server:", background=CARD, foreground=TEXT).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)

sql_server_entry = ttk.Entry(sql_card, textvariable=sql_server_var, width=18)
sql_server_entry.grid(row=2, column=1, sticky="ew", pady=5)

ttk.Label(
    sql_card,
    text="You can type the SQL Server name directly if it is not listed.",
    style="Muted.TLabel",
    wraplength=520,
    justify=LEFT
).grid(row=3, column=1, columnspan=2, sticky="w", pady=(0, 8))

sql_button_row = ttk.Frame(sql_card, style="Card.TFrame")
sql_button_row.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(6, 4))
sql_button_row.bind("<Configure>", update_sql_card_layout)

btn_find_servers = ttk.Button(
    sql_button_row,
    text="Find Server",
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
    command=load_sql_databases,
    style="Accent.TButton"
)

ttk.Label(
    sql_card,
    textvariable=sql_selected_count_var,
    background=CARD,
    foreground="#57f287",
    font=("Segoe UI", 9)
).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 2))

ttk.Label(
    sql_card,
    text="If finding SQL Servers takes too long, type the server name above and press Enter. Always test the connection.",
    background=CARD,
    foreground="#ffb86c",
    font=("Segoe UI", 9, "bold"),
    wraplength=760,
    justify=LEFT
).grid(row=6, column=0, columnspan=3, sticky="w", pady=(0, 8))

sql_backup_location_label = ttk.Label(
    sql_card,
    text="Temporary SQL Staging",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
)
sql_backup_location_label.grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 6))

ttk.Label(
    sql_card,
    text="Staging Folder:",
    background=CARD,
    foreground=TEXT
).grid(row=8, column=0, sticky="w", padx=(0, 8), pady=5)

sql_destination_entry = ttk.Entry(sql_card, textvariable=sql_staging_destination_var, width=45)
sql_destination_entry.grid(row=8, column=1, sticky="ew", padx=(0, 8), pady=5)

btn_sql_browse_destination = ttk.Button(
    sql_card,
    text="Browse",
    command=choose_sql_staging_destination,
    width=BTN_WIDTH
)
btn_sql_browse_destination.grid(row=8, column=2, sticky="ew", pady=5)

main_buttons.extend([
    btn_find_servers,
    btn_test_sql,
    btn_load_databases,
    btn_sql_browse_destination
])

# =========================================================
# Cloud Backup Tab: SQL Schedule Section
# =========================================================

sql_schedule_card = ttk.Frame(cloud_sql_content, style="Card.TFrame", padding=16)
sql_schedule_card.columnconfigure(0, weight=0)
sql_schedule_card.columnconfigure(1, weight=0)
sql_schedule_card.columnconfigure(2, weight=0)
sql_schedule_card.columnconfigure(3, weight=0)
sql_schedule_card.columnconfigure(4, weight=1)
sql_schedule_card.bind("<Configure>", update_sql_schedule_layout)
sql_schedule_card.pack(fill=X, pady=(0, 6))

create_cloud_section_header(
    sql_schedule_card,
    "sql_schedule",
    "SQL Backup Schedule",
    columnspan=5
)

sql_time_row = ttk.Frame(sql_schedule_card, style="CardBody.TFrame", padding=(0, 2))
sql_time_row.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 10))

ttk.Label(
    sql_time_row,
    text="Backup time:",
    background=CARD,
    foreground=TEXT
).pack(side=LEFT, padx=(0, 8))

ttk.Combobox(
    sql_time_row,
    textvariable=sql_hours_var,
    values=[f"{i:02d}" for i in range(1, 13)],
    width=5,
    state="readonly"
).pack(side=LEFT, padx=(0, 6))

ttk.Combobox(
    sql_time_row,
    textvariable=sql_minutes_var,
    values=[f"{i:02d}" for i in range(60)],
    width=5,
    state="readonly"
).pack(side=LEFT, padx=(0, 6))

ttk.Combobox(
    sql_time_row,
    textvariable=sql_ampm_var,
    values=["AM", "PM"],
    width=5,
    state="readonly"
).pack(side=LEFT)

sql_days_frame = ttk.Frame(sql_schedule_card, style="CardBody.TFrame")
sql_days_frame.grid(row=2, column=0, columnspan=4, sticky="w", pady=(0, 10))

for i, (day, var) in enumerate(sql_selected_days.items()):
    ttk.Checkbutton(sql_days_frame, text=day, variable=var).grid(
        row=0,
        column=i,
        sticky="w",
        padx=(0, 10),
        pady=(0, 2)
    )

btn_add_sql_time = ttk.Button(
    sql_schedule_card,
    text="Add Time",
    width=BTN_WIDTH,
    command=add_sql_backup_time
)
btn_add_sql_time.grid(row=3, column=0, sticky="w", padx=(0, 8), pady=5)

btn_remove_sql_time = ttk.Button(
    sql_schedule_card,
    text="Remove Selected",
    width=BTN_WIDTH,
    command=remove_sql_backup_time
)
btn_remove_sql_time.grid(row=3, column=1, sticky="w", pady=5)

sql_schedule_listbox = Listbox(
    sql_schedule_card,
    height=4,
    width=1,
    bg=INPUT_BG,
    fg=TEXT,
    selectbackground=ACCENT,
    selectforeground=TEXT,
    font=("Segoe UI", 10),
    relief=FLAT
)
sql_schedule_listbox.grid(row=1, column=4, rowspan=5, sticky="nw", padx=(20, 0))
sql_schedule_listbox.bind("<<ListboxSelect>>", edit_selected_sql_backup_time)
sql_schedule_listbox.bind("<Double-1>", edit_selected_sql_backup_time)

sql_schedule_toggle = ToggleSwitch(
    sql_schedule_card,
    command=toggle_sql_scheduler,
    is_on_callback=lambda: sql_scheduler_running
)
sql_schedule_toggle.grid(row=5, column=0, sticky="w", pady=(6, 0))
btn_toggle_sql_schedule = sql_schedule_toggle

sql_schedule_status_label = ttk.Label(
    sql_schedule_card,
    textvariable=sql_schedule_status_var,
    background=CARD,
    foreground="#57f287",
    font=("Segoe UI", 10, "bold")
)
sql_schedule_status_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

main_buttons.extend([
    btn_add_sql_time,
    btn_remove_sql_time,
    btn_toggle_sql_schedule
])

# =========================================================
# Cloud Backup Tab: Cloud Schedule Section
# =========================================================

cloud_schedule_card = ttk.Frame(cloud_location_content, style="Card.TFrame", padding=16)
cloud_schedule_card.columnconfigure(0, weight=0)
cloud_schedule_card.columnconfigure(1, weight=0)
cloud_schedule_card.columnconfigure(2, weight=0)
cloud_schedule_card.columnconfigure(3, weight=0)
cloud_schedule_card.columnconfigure(4, weight=1)
cloud_schedule_card.bind("<Configure>", update_cloud_schedule_layout)
cloud_schedule_card.pack(fill=X, pady=(0, 6), after=cloud_files_card)

create_cloud_section_header(
    cloud_schedule_card,
    "cloud_schedule",
    "Cloud Backup Schedule",
    columnspan=5
)

cloud_time_row = ttk.Frame(cloud_schedule_card, style="CardBody.TFrame", padding=(0, 2))
cloud_time_row.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 10))

ttk.Label(
    cloud_time_row,
    text="Backup time:",
    background=CARD,
    foreground=TEXT
).pack(side=LEFT, padx=(0, 8))

ttk.Combobox(
    cloud_time_row,
    textvariable=cloud_hours_var,
    values=[f"{i:02d}" for i in range(1, 13)],
    width=5,
    state="readonly"
).pack(side=LEFT, padx=(0, 6))

ttk.Combobox(
    cloud_time_row,
    textvariable=cloud_minutes_var,
    values=[f"{i:02d}" for i in range(60)],
    width=5,
    state="readonly"
).pack(side=LEFT, padx=(0, 6))

ttk.Combobox(
    cloud_time_row,
    textvariable=cloud_ampm_var,
    values=["AM", "PM"],
    width=5,
    state="readonly"
).pack(side=LEFT)

cloud_days_frame = ttk.Frame(cloud_schedule_card, style="CardBody.TFrame")
cloud_days_frame.grid(row=2, column=0, columnspan=4, sticky="w", pady=(0, 10))

for i, (day, var) in enumerate(cloud_selected_days.items()):
    ttk.Checkbutton(cloud_days_frame, text=day, variable=var).grid(
        row=0,
        column=i,
        sticky="w",
        padx=(0, 10),
        pady=(0, 2)
    )

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
    height=4,
    width=1,
    bg=INPUT_BG,
    fg=TEXT,
    selectbackground=ACCENT,
    selectforeground=TEXT,
    font=("Segoe UI", 10),
    relief=FLAT
)
cloud_schedule_listbox.grid(row=1, column=4, rowspan=5, sticky="nw", padx=(20, 0))
cloud_schedule_listbox.bind("<<ListboxSelect>>", edit_selected_cloud_backup_time)
cloud_schedule_listbox.bind("<Double-1>", edit_selected_cloud_backup_time)

btn_toggle_cloud_schedule = ttk.Button(
    cloud_schedule_card,
    text="Enable",
    width=BTN_WIDTH,
    command=toggle_cloud_scheduler,
    style="Ready.TButton"
)
btn_toggle_cloud_schedule.grid(row=5, column=0, sticky="w", pady=(6, 0))

cloud_schedule_status_label = ttk.Label(
    cloud_schedule_card,
    textvariable=cloud_status_var,
    background=CARD,
    foreground="#57f287",
    font=("Segoe UI", 10, "bold")
)
cloud_schedule_status_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

cloud_schedule_drive_location_link = ttk.Label(
    cloud_schedule_card,
    text="Open Google Drive Location",
    background=CARD,
    foreground="#57f287",
    font=("Segoe UI", 10, "underline"),
    cursor="hand2"
)
cloud_schedule_drive_location_link.grid(row=7, column=0, columnspan=2, sticky="w", pady=(3, 0))
cloud_schedule_drive_location_link.bind("<Button-1>", lambda event: open_google_drive_schedule_location())

main_buttons.extend([
    btn_add_cloud_time,
    btn_remove_cloud_time,
    btn_toggle_cloud_schedule
])

cloud_section_widgets.update({
    "cloud_items": cloud_files_card,
    "google_drive": google_drive_card,
    "sql": sql_card,
    "cloud_schedule": cloud_schedule_card
})
refresh_cloud_sections()

# =========================================================
# Cloud Backup Tab: Actions
# =========================================================

cloud_action_row = ttk.Frame(cloud_tab)
cloud_action_row.pack(fill=X, pady=8)
cloud_action_row.columnconfigure(0, weight=1)
cloud_action_row.columnconfigure(1, weight=0)
cloud_action_row.columnconfigure(2, weight=0)

cloud_drive_location_link = ttk.Label(
    cloud_action_row,
    text="Open Google Drive Location",
    background=BG,
    foreground="#57f287",
    font=("Segoe UI", 10, "underline"),
    cursor="hand2"
)
cloud_drive_location_link.bind("<Button-1>", lambda event: open_google_drive_schedule_location())

btn_cloud_next = ttk.Button(
    cloud_action_row,
    text="Next",
    width=BTN_WIDTH + 10,
    command=lambda: select_cloud_subtab(cloud_location_items_tab),
    style="Accent.TButton"
)
btn_cloud_next.grid(row=0, column=1, sticky="e")

btn_upload_cloud_items = ttk.Button(
    cloud_action_row,
    text="Send to Google Drive",
    width=BTN_WIDTH + 10,
    command=upload_cloud_items_now,
    style="Accent.TButton"
)

cloud_upload_requirement_label = ttk.Label(
    cloud_action_row,
    text="",
    style="WarningCaption.TLabel",
    wraplength=520,
    justify=RIGHT
)

sql_cloud_status_label = ttk.Label(
    cloud_action_row,
    textvariable=sql_cloud_action_status_var,
    style="ReadableCaption.TLabel",
    wraplength=520,
    justify=RIGHT
)

btn_sql_cloud_backup = ttk.Button(
    cloud_action_row,
    text="Back Up to Google Drive",
    width=BTN_WIDTH + 10,
    command=start_sql_cloud_backup_async,
    style="Accent.TButton"
)

btn_cloud_toggle_schedule = ttk.Button(
    cloud_action_row,
    text="Start Schedule",
    width=BTN_WIDTH + 10,
    command=toggle_cloud_scheduler,
    style="Ready.TButton"
)

cloud_footer_status_label = ttk.Label(
    cloud_action_row,
    textvariable=cloud_status_var,
    background=BG,
    foreground="#57f287",
    font=("Segoe UI", 13, "bold")
)

main_buttons.extend([
    btn_upload_cloud_items,
    btn_sql_cloud_backup,
    btn_cloud_next,
    btn_cloud_toggle_schedule
])

update_cloud_upload_button_state()
update_sql_backup_button_state()
update_cloud_action_buttons()


# =========================================================
# Backup Tab: File Selection
# =========================================================

backup_header = create_page_header(backup_tab, "Create a backup", "Choose files, pick a destination, and save an archive.")

# Reserve the action bar before allocating the expandable scrolling area.
action_row = ttk.Frame(backup_tab, padding=(0, 12, 0, 0))
action_row.pack(side=BOTTOM, fill=X)
action_row.columnconfigure(0, weight=1)
ttk.Label(action_row, textvariable=status_var).grid(row=0, column=0, sticky="w")
ttk.Progressbar(action_row, variable=progress_var, maximum=100).grid(
    row=1, column=0, sticky="ew", padx=(0, 20), pady=(6, 0)
)
backup_body = ttk.Frame(backup_tab)
backup_body.pack(fill=BOTH, expand=True)
backup_content = create_scrollable_tab_content(backup_body)

files_card = ttk.Frame(backup_content, style="Card.TFrame", padding=16)
files_card.pack(fill=X, pady=(0, 6))


ttk.Label(
    files_card,
    text="1. Choose files and folders",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 6))

button_row = ttk.Frame(files_card, style="Card.TFrame")
button_row.pack(fill=X, pady=(0, 6))
button_row.bind("<Configure>", update_backup_tab_layout)

btn_add_files = ttk.Button(button_row, text="Add Files", command=add_files)
btn_add_files.pack(side=LEFT, padx=(0, 8))

btn_add_folder = ttk.Button(button_row, text="Add Folder", command=add_folder)
btn_add_folder.pack(side=LEFT, padx=(0, 8))

btn_clear_list = ttk.Button(button_row, text="Clear List", command=clear_list)
btn_clear_list.pack(side=LEFT, padx=(0, 8))
btn_remove_backup_items = ttk.Button(button_row, text="Remove Selected", command=remove_selected_backup_items)
main_buttons.append(btn_remove_backup_items)


main_buttons.extend([
    btn_add_files,
    btn_add_folder,
    btn_clear_list
])

listbox_frame = Frame(files_card, bg=CARD)
listbox_frame.pack(fill=X)

listbox = Listbox(
    listbox_frame,
    bg=INPUT_BG,
    fg=TEXT,
    selectbackground=ACCENT,
    selectforeground=TEXT,
    font=("Segoe UI", 10),
    relief=FLAT,
    height=5,
    selectmode=EXTENDED,
    exportselection=False
)
listbox.pack(side=LEFT, fill=X, expand=True)

list_scrollbar = Scrollbar(listbox_frame)
configure_auto_hide_scrollbar(
    listbox,
    list_scrollbar,
    VERTICAL,
    "pack",
    {"side": RIGHT, "fill": Y}
)

# =========================================================
# Backup Tab: Destination and Format
# =========================================================

settings_card = ttk.Frame(backup_content, style="Card.TFrame", padding=16)
settings_card.pack(fill=X, pady=6)


ttk.Label(
    files_card,
    textvariable=summary_var,
    background=CARD,
    foreground=MUTED
).pack(anchor="w", pady=(5, 0))

ttk.Label(
    settings_card,
    text="2. Set up the archive",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 12, "bold")
).grid(row=0, column=0, sticky="w", columnspan=4, pady=(0, 6))

ttk.Label(settings_card, text="Destination:", background=CARD, foreground=TEXT).grid(row=1, column=0, sticky="w", padx=(0, 8))

destination_entry = ttk.Entry(settings_card, textvariable=destination_var, width=45)
destination_entry.grid(row=1, column=1, sticky="ew", padx=(0, 8))

btn_browse = ttk.Button(settings_card, text="Browse", command=choose_destination)
btn_browse.grid(row=1, column=2, sticky="ew")
main_buttons.append(btn_browse)
settings_card.columnconfigure(1, weight=1)

ttk.Label(
    settings_card,
    text="Description (optional):",
    background=CARD,
    foreground=TEXT
).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(6, 0))

backup_description_entry = ttk.Entry(settings_card, textvariable=backup_description_var, width=45)
backup_description_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(6, 0))

format_frame = ttk.Frame(settings_card, style="Card.TFrame")
format_frame.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

ttk.Label(format_frame, text="Format:", background=CARD, foreground=TEXT).pack(side=LEFT, padx=(0, 10))
ttk.Radiobutton(format_frame, text="ZIP", variable=format_var, value="zip").pack(side=LEFT, padx=8)
ttk.Radiobutton(format_frame, text="7Z", variable=format_var, value="7z").pack(side=LEFT, padx=8)
ttk.Radiobutton(format_frame, text="RAR", variable=format_var, value="rar").pack(side=LEFT, padx=8)

startup_checkbox = ttk.Checkbutton(
    settings_card,
    text="Launch with Windows",
    variable=run_on_startup_var,
    command=toggle_run_on_startup,
    style="StartupDisabled.TCheckbutton"
)
startup_checkbox.grid(
    row=4,
    column=0,
    columnspan=3,
    sticky="w",
    pady=(6, 0)
)

# =========================================================
# Schedule Tab
# =========================================================

scheduler_scroll_frame = ttk.Frame(scheduler_tab)
scheduler_scroll_frame.pack(fill=BOTH, expand=True)
scheduler_scroll_frame.columnconfigure(0, weight=1)
scheduler_scroll_frame.rowconfigure(0, weight=1)

scheduler_canvas = Canvas(
    scheduler_scroll_frame,
    bg=BG,
    highlightthickness=0,
    bd=0
)
scheduler_canvas.grid(row=0, column=0, sticky="nsew")

scheduler_vertical_scrollbar = Scrollbar(
    scheduler_scroll_frame,
    orient=VERTICAL,
    command=scheduler_canvas.yview
)
scheduler_vertical_scrollbar.grid(row=0, column=1, sticky="ns")
scheduler_vertical_scrollbar.grid_remove()

scheduler_horizontal_scrollbar = Scrollbar(
    scheduler_scroll_frame,
    orient=HORIZONTAL,
    command=scheduler_canvas.xview
)
scheduler_horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
scheduler_horizontal_scrollbar.grid_remove()

scheduler_canvas.configure(
    yscrollcommand=scheduler_vertical_scrollbar.set,
    xscrollcommand=scheduler_horizontal_scrollbar.set
)

scheduler_content = ttk.Frame(scheduler_canvas)
scheduler_content_window = scheduler_canvas.create_window(
    (0, 0),
    window=scheduler_content,
    anchor="nw"
)
scheduler_content.bind("<Configure>", update_scheduler_scroll_region)
scheduler_content.bind("<Configure>", update_scheduler_tab_layout, add="+")
scheduler_canvas.bind("<Configure>", update_scheduler_scroll_region)
scheduler_canvas.bind("<Enter>", bind_scheduler_mousewheel)
scheduler_canvas.bind("<Leave>", unbind_scheduler_mousewheel)

scheduler_toolbar = create_page_header(
    scheduler_content, "Schedule backups", "Choose what to save and how often to back it up."
)

scheduler_subtab_nav = ttk.Frame(scheduler_content)

scheduler_subtabs = ttk.Notebook(scheduler_content)
scheduler_subtabs.pack(fill=BOTH, expand=True, pady=(0, 6))
scheduler_subtabs.configure(style="Content.TNotebook")
scheduler_subtabs.bind("<<NotebookTabChanged>>", update_scheduler_scroll_region)
scheduler_subtabs.bind("<<NotebookTabChanged>>", update_scheduler_subtab_buttons, add="+")

manual_scheduler_tab = ttk.Frame(scheduler_subtabs)
auto_scheduler_tab = ttk.Frame(scheduler_subtabs)
scheduled_scheduler_tab = ttk.Frame(scheduler_subtabs)

for scheduler_subtab in (manual_scheduler_tab, auto_scheduler_tab, scheduled_scheduler_tab):
    scheduler_subtab.columnconfigure(0, weight=1)

scheduler_subtabs.add(auto_scheduler_tab, text="Schedule")

root.after_idle(update_scheduler_subtab_buttons)

manual_scheduler_card = ttk.Frame(manual_scheduler_tab, style="Card.TFrame", padding=16)
manual_scheduler_card.columnconfigure(0, weight=1)
manual_scheduler_card.columnconfigure(1, weight=1)
manual_scheduler_card.grid(row=0, column=0, sticky="ew", pady=(0, 6))

manual_header = create_scheduler_section_header(manual_scheduler_card, "manual", "Manual Backups")

ttk.Label(
    manual_header,
    text="Files, folders, or the Office preset at a specific time",
    style="Muted.TLabel"
).grid(row=1, column=0, sticky="w", pady=(3, 0))

hours_var = StringVar(value="12")
minutes_var = StringVar(value="00")
ampm_var = StringVar(value="AM")

schedule_details_frame = ttk.Frame(manual_scheduler_card, style="CardBody.TFrame")
schedule_details_frame.grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=(0, 6))
schedule_details_frame.columnconfigure(0, weight=1)

ttk.Label(
    schedule_details_frame,
    text="Schedule Name",
    background=CARD,
    foreground=MUTED
).grid(row=0, column=0, sticky="w", pady=(0, 4))

ttk.Entry(
    schedule_details_frame,
    textvariable=schedule_name_var,
    width=18
).grid(row=1, column=0, sticky="ew")

manual_source_frame = ttk.Frame(manual_scheduler_card, style="CardBody.TFrame")
manual_source_frame.grid(row=1, column=1, sticky="ew", pady=(0, 6))

ttk.Label(
    manual_source_frame,
    text="Source",
    background=CARD,
    foreground=MUTED
).grid(row=0, column=0, sticky="w", pady=(0, 4))

ttk.Checkbutton(
    manual_source_frame,
    text="Use Office file preset",
    variable=office_schedule_var,
    command=update_office_preset_caption
).grid(row=1, column=0, sticky="w")

ttk.Label(
    manual_scheduler_card,
    textvariable=office_preset_caption_var,
    style="Muted.TLabel",
    wraplength=430,
    justify=LEFT
).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 6))

scheduler_items_frame = ttk.Frame(manual_scheduler_card, style="CardBody.TFrame")
scheduler_items_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(0, 6))
scheduler_items_frame.columnconfigure(0, weight=1)

ttk.Label(
    scheduler_items_frame,
    text="Selected Items",
    style="CardTitle.TLabel"
).grid(row=0, column=0, sticky="w", pady=(0, 6))

scheduler_items_listbox = Listbox(
    scheduler_items_frame,
    height=4,
    width=20,
    bg=INPUT_BG,
    fg=TEXT,
    selectbackground=ACCENT,
    selectforeground=TEXT,
    font=("Segoe UI", 9),
    relief=FLAT,
    selectmode=EXTENDED
)
scheduler_items_listbox.grid(row=1, column=0, sticky="nsew")

scheduler_items_scrollbar = Scrollbar(scheduler_items_frame)
configure_auto_hide_scrollbar(
    scheduler_items_listbox,
    scheduler_items_scrollbar,
    VERTICAL,
    "grid",
    {"row": 1, "column": 1, "sticky": "ns"}
)

scheduler_items_button_row = ttk.Frame(scheduler_items_frame, style="CardBody.TFrame")
scheduler_items_button_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0))
scheduler_items_button_row.bind("<Configure>", update_scheduler_item_buttons_layout)

btn_add_scheduler_files = ttk.Button(
    scheduler_items_button_row,
    text="Add Files",
    command=add_scheduler_files
)

btn_add_scheduler_folder = ttk.Button(
    scheduler_items_button_row,
    text="Add Folder",
    command=add_scheduler_folder
)

btn_remove_scheduler_item = ttk.Button(
    scheduler_items_button_row,
    text="Remove Selected",
    command=remove_selected_scheduler_item
)

btn_clear_scheduler_items = ttk.Button(
    scheduler_items_button_row,
    text="Clear",
    command=clear_scheduler_items
)

ttk.Label(
    scheduler_items_frame,
    textvariable=scheduler_selected_count_var,
    style="Muted.TLabel"
).grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))

manual_destination_row = ttk.Frame(manual_scheduler_card, style="CardBody.TFrame")
manual_destination_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 6))

ttk.Label(
    manual_destination_row,
    text="Destination:",
    background=CARD,
    foreground=TEXT
).grid(row=0, column=0, sticky="w", padx=(0, 8))

ttk.Entry(
    manual_destination_row,
    textvariable=manual_backup_destination_var,
    width=24
).grid(row=0, column=1, sticky="w", padx=(0, 8))

btn_browse_manual_destination = ttk.Button(
    manual_destination_row,
    text="Browse",
    command=choose_manual_backup_destination
)
btn_browse_manual_destination.grid(row=0, column=2, sticky="w")

time_row = ttk.Frame(manual_scheduler_card, style="CardBody.TFrame")
time_row.grid(row=5, column=0, sticky="w", padx=(0, 10), pady=(0, 6))

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

days_frame = ttk.Frame(manual_scheduler_card, style="CardBody.TFrame")
days_frame.grid(row=5, column=1, sticky="ew", pady=(0, 6))
days_frame.bind("<Configure>", update_scheduler_days_layout)

ttk.Label(
    days_frame,
    text="Days:",
    background=CARD,
    foreground=TEXT
).grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 2))

scheduler_day_checkbuttons = {}
for day, var in selected_days.items():
    scheduler_day_checkbuttons[day] = ttk.Checkbutton(days_frame, text=day, variable=var)

manual_button_row = ttk.Frame(manual_scheduler_card, style="CardBody.TFrame")
manual_button_row.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(2, 0))
manual_button_row.columnconfigure(0, minsize=132)
manual_button_row.columnconfigure(1, minsize=132)
manual_button_row.bind("<Configure>", update_scheduler_control_layout)

btn_add_time = ttk.Button(
    manual_button_row,
    text="Add Manual",
    command=add_backup_time,
    width=BTN_WIDTH
)

btn_remove_time = ttk.Button(
    manual_button_row,
    text="Remove Selected",
    command=remove_selected_time,
    width=BTN_WIDTH
)

schedule_card = ttk.Frame(scheduled_scheduler_tab, style="Card.TFrame", padding=16)
schedule_card.columnconfigure(0, weight=1)
schedule_card.grid(row=0, column=0, sticky="ew", pady=(0, 6))

create_scheduler_section_header(schedule_card, "scheduled", "Scheduled Backups")

schedule_items_frame = ttk.Frame(schedule_card, style="CardBody.TFrame")
schedule_items_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
schedule_items_frame.columnconfigure(0, weight=1)

ttk.Label(
    schedule_items_frame,
    text="Selected Items",
    style="CardTitle.TLabel"
).grid(row=0, column=0, sticky="w", pady=(0, 6))

schedule_items_listbox = Listbox(
    schedule_items_frame,
    height=4,
    width=20,
    bg=INPUT_BG,
    fg=TEXT,
    selectbackground=ACCENT,
    selectforeground=TEXT,
    font=("Segoe UI", 9),
    relief=FLAT,
    selectmode=EXTENDED
)
schedule_items_listbox.grid(row=1, column=0, sticky="nsew")

schedule_items_scrollbar = Scrollbar(schedule_items_frame)
configure_auto_hide_scrollbar(
    schedule_items_listbox,
    schedule_items_scrollbar,
    VERTICAL,
    "grid",
    {"row": 1, "column": 1, "sticky": "ns"}
)

schedule_items_button_row = ttk.Frame(schedule_items_frame, style="CardBody.TFrame")
schedule_items_button_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0))
schedule_items_button_row.bind("<Configure>", update_scheduler_item_buttons_layout)

btn_add_schedule_files = ttk.Button(
    schedule_items_button_row,
    text="Add Files",
    command=add_scheduler_files
)

btn_add_schedule_folder = ttk.Button(
    schedule_items_button_row,
    text="Add Folder",
    command=add_scheduler_folder
)

btn_remove_schedule_item = ttk.Button(
    schedule_items_button_row,
    text="Remove Selected",
    command=remove_selected_schedule_tab_item
)

btn_clear_schedule_items = ttk.Button(
    schedule_items_button_row,
    text="Clear",
    command=clear_scheduler_items
)

ttk.Label(
    schedule_items_frame,
    textvariable=scheduler_selected_count_var,
    style="Muted.TLabel"
).grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))

schedule_list_frame = ttk.Frame(schedule_card, style="Card.TFrame")
schedule_list_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 6))
schedule_list_frame.columnconfigure(0, weight=1)

schedule_listbox = Listbox(
    schedule_list_frame,
    height=5,
    width=20,
    bg=INPUT_BG,
    fg=TEXT,
    selectbackground=ACCENT,
    selectforeground=TEXT,
    font=("Segoe UI", 10),
    relief=FLAT
)
schedule_listbox.grid(row=0, column=0, sticky="nsew")

schedule_scrollbar = Scrollbar(schedule_list_frame)
configure_auto_hide_scrollbar(
    schedule_listbox,
    schedule_scrollbar,
    VERTICAL,
    "grid",
    {"row": 0, "column": 1, "sticky": "ns"}
)

scheduler_control_row = ttk.Frame(schedule_card, style="CardBody.TFrame")
scheduler_control_row.grid(row=3, column=0, sticky="ew")
scheduler_control_row.columnconfigure(0, weight=1)
scheduler_control_row.columnconfigure(1, weight=0)

scheduler_button_group = ttk.Frame(scheduler_control_row, style="CardBody.TFrame")
scheduler_button_group.grid(row=0, column=0, sticky="ew")
scheduler_button_group.columnconfigure(0, minsize=132)
scheduler_button_group.bind("<Configure>", update_scheduler_control_layout)

btn_toggle_scheduler = ttk.Button(
    scheduler_button_group,
    text="Start Schedule",
    width=BTN_WIDTH,
    command=toggle_scheduler,
    style="CompactAccent.TButton"
)

status_label = ttk.Label(
    scheduler_control_row,
    textvariable=scheduler_status_var,
    image=icon_red,
    compound="left",
    background=CARD,
    foreground=TEXT
)

status_label.grid(row=0, column=1, sticky="e", padx=(12, 0))

auto_scheduler_card = ttk.Frame(auto_scheduler_tab, style="Card.TFrame", padding=16)
auto_scheduler_card.columnconfigure(0, weight=1)
auto_scheduler_card.grid(row=0, column=0, sticky="ew", pady=(0, 6))

auto_header = create_scheduler_section_header(auto_scheduler_card, "auto", "Create a schedule")

ttk.Label(
    auto_header,
    text="Selected items or the Office preset on an automatic recurrence",
    style="Muted.TLabel"
).grid(row=1, column=0, sticky="w", pady=(3, 0))

auto_settings_frame = ttk.Frame(auto_scheduler_card, style="CardBody.TFrame")
auto_settings_frame.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 6))
auto_settings_frame.columnconfigure(0, weight=1)
auto_settings_frame.columnconfigure(1, weight=1)

auto_name_frame = ttk.Frame(auto_settings_frame, style="CardBody.TFrame")
auto_name_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
auto_name_frame.columnconfigure(1, weight=1)

ttk.Label(
    auto_name_frame,
    text="Backup name:",
    background=CARD,
    foreground=TEXT
).grid(row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 6))

ttk.Entry(
    auto_name_frame,
    textvariable=auto_schedule_name_var,
    width=45
).grid(row=0, column=1, sticky="ew", pady=(0, 6))

ttk.Label(
    auto_name_frame,
    text="Description (optional):",
    background=CARD,
    foreground=TEXT
).grid(row=1, column=0, sticky="w", padx=(0, 12))

ttk.Entry(
    auto_name_frame,
    textvariable=auto_schedule_description_var,
    width=45
).grid(row=1, column=1, sticky="ew")

auto_source_frame = ttk.Frame(auto_settings_frame, style="CardBody.TFrame")
auto_source_frame.grid(row=1, column=0, columnspan=2, sticky="ew")

ttk.Label(
    auto_source_frame,
    text="Source",
    background=CARD,
    foreground=MUTED
).grid(row=0, column=0, sticky="w", pady=(0, 4))

ttk.Checkbutton(
    auto_source_frame,
    text="Use Office file preset",
    variable=auto_office_schedule_var
).grid(row=1, column=0, sticky="w")

ttk.Label(
    auto_source_frame,
    text="Checked saves common Office files; unchecked saves the selected items below.",
    style="Muted.TLabel",
    wraplength=360,
    justify=LEFT
).grid(row=2, column=0, sticky="w", pady=(4, 0))

auto_scheduler_items_frame = ttk.Frame(auto_scheduler_card, style="CardBody.TFrame")
auto_scheduler_items_frame.grid(row=2, column=0, columnspan=4, sticky="nsew", pady=(0, 6))
auto_scheduler_items_frame.columnconfigure(0, weight=1)

ttk.Label(
    auto_scheduler_items_frame,
    text="Selected Items",
    style="CardTitle.TLabel"
).grid(row=0, column=0, sticky="w", pady=(0, 6))

auto_scheduler_items_listbox = Listbox(
    auto_scheduler_items_frame,
    height=4,
    width=20,
    bg=INPUT_BG,
    fg=TEXT,
    selectbackground=ACCENT,
    selectforeground=TEXT,
    font=("Segoe UI", 9),
    relief=FLAT,
    selectmode=EXTENDED
)
auto_scheduler_items_listbox.grid(row=1, column=0, sticky="nsew")

auto_scheduler_items_scrollbar = Scrollbar(auto_scheduler_items_frame)
configure_auto_hide_scrollbar(
    auto_scheduler_items_listbox,
    auto_scheduler_items_scrollbar,
    VERTICAL,
    "grid",
    {"row": 1, "column": 1, "sticky": "ns"}
)

auto_scheduler_items_button_row = ttk.Frame(auto_scheduler_items_frame, style="CardBody.TFrame")
auto_scheduler_items_button_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0))
auto_scheduler_items_button_row.bind("<Configure>", update_scheduler_item_buttons_layout)

btn_add_auto_scheduler_files = ttk.Button(
    auto_scheduler_items_button_row,
    text="Add Files",
    command=add_auto_scheduler_files
)

btn_add_auto_scheduler_folder = ttk.Button(
    auto_scheduler_items_button_row,
    text="Add Folder",
    command=add_auto_scheduler_folder
)

btn_remove_auto_scheduler_item = ttk.Button(
    auto_scheduler_items_button_row,
    text="Remove Selected",
    command=remove_selected_auto_scheduler_item
)

btn_clear_auto_scheduler_items = ttk.Button(
    auto_scheduler_items_button_row,
    text="Clear",
    command=clear_auto_scheduler_items
)

ttk.Label(
    auto_scheduler_items_frame,
    textvariable=auto_selected_count_var,
    style="Muted.TLabel"
).grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))

auto_destination_row = ttk.Frame(auto_scheduler_card, style="CardBody.TFrame")
auto_destination_row.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(0, 6))

ttk.Label(
    auto_destination_row,
    text="Destination:",
    background=CARD,
    foreground=TEXT
).grid(row=0, column=0, sticky="w", padx=(0, 8))

ttk.Entry(
    auto_destination_row,
    textvariable=auto_backup_destination_var,
    width=24
).grid(row=0, column=1, sticky="w", padx=(0, 8))

btn_browse_auto_destination = ttk.Button(
    auto_destination_row,
    text="Browse",
    command=choose_auto_backup_destination
)
btn_browse_auto_destination.grid(row=0, column=2, sticky="w")
ttk.Label(auto_destination_row, textvariable=schedule_destination_caption_var,
          style="Muted.TLabel", wraplength=520, justify=LEFT).grid(
    row=1, column=0, columnspan=3, sticky="w", pady=(6, 0)
)

auto_options_frame = ttk.Frame(auto_scheduler_card, style="CardBody.TFrame")
auto_options_frame.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(0, 6))

auto_repeat_group = ttk.Frame(auto_options_frame, style="CardBody.TFrame")
auto_repeat_group.grid(row=0, column=0, sticky="w", pady=(0, 8))

ttk.Label(
    auto_repeat_group,
    text="Repeat:",
    background=CARD,
    foreground=TEXT
).pack(side=LEFT, padx=(0, 8))

auto_recurrence_dropdown = ttk.Combobox(
    auto_repeat_group,
    textvariable=auto_backup_recurrence_var,
    values=["Minutes", "Daily", "Weekly", "Bi-weekly", "Monthly"],
    width=17,
    state="readonly"
)
auto_recurrence_dropdown.pack(side=LEFT, padx=(0, 8))
auto_recurrence_dropdown.bind("<<ComboboxSelected>>", update_auto_recurrence_fields)

auto_interval_group = ttk.Frame(auto_repeat_group, style="CardBody.TFrame")
auto_interval_group.pack(side=LEFT)

ttk.Spinbox(
    auto_interval_group,
    from_=1,
    to=1440,
    textvariable=auto_backup_interval_var,
    width=5
).pack(side=LEFT, padx=(0, 6))

ttk.Label(
    auto_interval_group,
    text="min",
    background=CARD,
    foreground=TEXT
).pack(side=LEFT)

auto_time_group = ttk.Frame(auto_options_frame, style="CardBody.TFrame")
auto_time_group.grid(row=1, column=0, sticky="w", pady=(0, 8))

ttk.Label(
    auto_time_group,
    text="At:",
    background=CARD,
    foreground=TEXT
).pack(side=LEFT, padx=(0, 6))

ttk.Combobox(
    auto_time_group,
    textvariable=auto_hours_var,
    values=[f"{i:02d}" for i in range(1, 13)],
    width=4,
    state="readonly"
).pack(side=LEFT, padx=(0, 2))

ttk.Combobox(
    auto_time_group,
    textvariable=auto_minutes_var,
    values=[f"{i:02d}" for i in range(60)],
    width=4,
    state="readonly"
).pack(side=LEFT, padx=(0, 2))

ttk.Combobox(
    auto_time_group,
    textvariable=auto_ampm_var,
    values=["AM", "PM"],
    width=4,
    state="readonly"
).pack(side=LEFT)

auto_month_day_group = ttk.Frame(auto_options_frame, style="CardBody.TFrame")
auto_month_day_group.grid(row=2, column=0, sticky="w", pady=(0, 8))

ttk.Label(
    auto_month_day_group,
    text="Day:",
    background=CARD,
    foreground=TEXT
).pack(side=LEFT, padx=(0, 6))

ttk.Spinbox(
    auto_month_day_group,
    from_=1,
    to=31,
    textvariable=auto_month_day_var,
    width=5
).pack(side=LEFT)

auto_days_frame = ttk.Frame(auto_options_frame, style="CardBody.TFrame")
auto_days_frame.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(0, 8))
auto_days_frame.bind("<Configure>", update_scheduler_days_layout)

ttk.Label(
    auto_days_frame,
    text="Days:",
    background=CARD,
    foreground=TEXT
).grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 2))

auto_scheduler_day_checkbuttons = {}
for day, var in auto_selected_days.items():
    auto_scheduler_day_checkbuttons[day] = ttk.Checkbutton(auto_days_frame, text=day, variable=var)

auto_action_frame = ttk.Frame(auto_options_frame, style="CardBody.TFrame")
auto_action_frame.grid(row=2, column=0, columnspan=4, sticky="e", pady=(0, 8))

btn_add_office_auto = ttk.Button(
    auto_action_frame,
    text="Add Schedule",
    command=add_office_auto_backup_schedule,
    width=BTN_WIDTH
)

auto_schedule_toggle = ToggleSwitch(
    auto_action_frame,
    command=toggle_scheduler,
    is_on_callback=lambda: scheduler_running
)
auto_schedule_toggle.grid(row=0, column=1, sticky="e")
btn_auto_toggle_scheduler = auto_schedule_toggle

ttk.Label(
    auto_scheduler_card,
    text="Minute backups run continuously; Daily runs once as soon as the scheduler is enabled on a selected day; other calendar schedules use their selected time.",
    style="ReadableCaption.TLabel",
    wraplength=720,
    justify=LEFT
).grid(row=5, column=0, columnspan=4, sticky="w", pady=(4, 0))

scheduler_section_widgets.update({
    "manual": manual_scheduler_card,
    "auto": auto_scheduler_card,
    "scheduled": schedule_card
})
refresh_scheduler_sections()
root.after_idle(update_scheduler_item_buttons_layout)
root.after_idle(update_scheduler_days_layout)
root.after_idle(update_scheduler_control_layout)
root.after_idle(update_office_preset_caption)
root.after_idle(update_auto_recurrence_fields)
root.after_idle(update_scheduler_tab_layout)

main_buttons.extend([
    btn_add_scheduler_files,
    btn_add_scheduler_folder,
    btn_remove_scheduler_item,
    btn_clear_scheduler_items,
    btn_add_schedule_files,
    btn_add_schedule_folder,
    btn_remove_schedule_item,
    btn_clear_schedule_items,
    btn_add_auto_scheduler_files,
    btn_add_auto_scheduler_folder,
    btn_remove_auto_scheduler_item,
    btn_clear_auto_scheduler_items,
    btn_add_time,
    btn_remove_time,
    btn_browse_manual_destination,
    btn_browse_auto_destination,
    btn_auto_toggle_scheduler,
    btn_toggle_scheduler
])


# =========================================================
# Backup Tab: Actions
# =========================================================

btn_start_backup = ttk.Button(
    action_row,
    text="Start Backup",
    command=start_backup_async,
    style="Accent.TButton"
)
btn_start_backup.grid(row=0, column=1, rowspan=2, sticky="e")

main_buttons.extend([
    btn_start_backup
])

load_app_settings()
apply_cloud_tab_visibility()
refresh_schedule_destination_caption()
refresh_google_drive_status()
refresh_logs_tab()
update_startup_checkbox_style()
update_backup_summary()
root.after_idle(update_dashboard_card_layout)
root.after_idle(update_main_notebook_shell)
root.after_idle(update_cloud_schedule_layout)
root.after_idle(update_backup_tab_layout)
root.after_idle(update_cloud_items_layout)
root.after_idle(update_sql_card_layout)
root.after_idle(update_sql_database_button_state)
root.after_idle(update_sql_scheduler_controls)
root.after_idle(update_google_drive_layout)
root.after_idle(update_cloud_next_button_state)
root.after_idle(update_responsive_tab_layout)
root.protocol("WM_DELETE_WINDOW", on_app_close)

setup_tray_icon()
refresh_dashboard()
check_scheduled_backups()
check_cloud_scheduled_backups()
check_sql_scheduled_backups()
process_progress_queue()
process_ui_action_queue()
process_backup_result_queue()

root.mainloop()
