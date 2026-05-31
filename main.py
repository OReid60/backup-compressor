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
APP_VERSION = "2.6.3"
ALWAYS_RUN_AS_ADMIN = True
SINGLE_INSTANCE_MUTEX_NAME = r"Local\BackupCompressorSingleInstance"
ERROR_ALREADY_EXISTS = 183

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
SCHEDULER_POLL_INTERVAL_MS = 15000
GOOGLE_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
OFFICE_FILE_EXTENSIONS = (
    ".doc", ".docx", ".docm",
    ".xls", ".xlsx", ".xlsm", ".xlsb",
    ".ppt", ".pptx", ".pptm",
    ".pdf", ".txt", ".rtf", ".csv",
    ".png", ".jpeg", ".pdn",
    ".one", ".pst", ".ost"
)
OFFICE_BACKUP_FOLDER_NAMES = ("Desktop", "Documents", "Downloads")

# Main color and sizing constants are kept together so UI sections share one
# visual language without repeating magic values.

# =========================================================
# Runtime State
# =========================================================
main_buttons = []
selected_items = []
scheduler_selected_items = []
cloud_selected_items = []
scheduled_backup_times = []
scheduler_running = False
last_run_time = None
schedule_last_run_times = {}
scheduler_section_order = ["manual", "auto"]
scheduler_section_widgets = {}
cloud_section_order = ["cloud_items", "google_drive", "sql", "cloud_schedule"]
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
    if os.name != "nt":
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

def choose_manual_backup_destination():
    folder = filedialog.askdirectory(parent=root)
    if folder:
        manual_backup_destination_var.set(folder)

def choose_auto_backup_destination():
    folder = filedialog.askdirectory(parent=root)
    if folder:
        auto_backup_destination_var.set(folder)

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
        messagebox.showwarning("No Item Selected", "Select a scheduler item to remove.")
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
    if "scheduler_items_listbox" not in globals():
        return

    scheduler_items_listbox.delete(0, END)

    for item in scheduler_selected_items:
        scheduler_items_listbox.insert(END, item)

    file_count, folder_count = get_selected_item_counts(scheduler_selected_items)
    scheduler_selected_count_var.set(
        f"{file_count} file(s), {folder_count} folder(s) selected"
    )

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
        "last_profile": active_profile_path,
        "destination": destination_var.get(),
        "manual_backup_destination": manual_backup_destination_var.get(),
        "auto_backup_destination": auto_backup_destination_var.get(),
        "format": format_var.get(),
        "scheduler_selected_items": scheduler_selected_items,
        "schedule_times": scheduled_backup_times,
        "cloud_schedule_times": cloud_scheduled_backup_times,
        "cloud_selected_items": cloud_selected_items,
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

        settings_were_reset = reset_old_settings_after_update(settings)

        destination_var.set(settings.get("destination", ""))
        manual_backup_destination_var.set(
            settings.get("manual_backup_destination", settings.get("destination", ""))
        )
        auto_backup_destination_var.set(
            settings.get("auto_backup_destination", settings.get("destination", ""))
        )
        format_var.set(settings.get("format", "zip"))
        sql_server_var.set(settings.get("sql_server", r".\SQLEXPRESS"))
        sql_database_var.set(settings.get("sql_database", "BackupCompressorTest"))
        sql_include_scheduler_var.set(settings.get("sql_include_scheduler", False))
        google_drive_folder_var.set(settings.get("google_drive_folder", "My Drive"))

        scheduled_backup_times.clear()
        scheduled_backup_times.extend(settings.get("schedule_times", []))
        update_schedule_list()

        scheduler_selected_items.clear()
        scheduler_selected_items.extend(settings.get("scheduler_selected_items", []))
        update_scheduler_item_list()

        cloud_scheduled_backup_times.clear()
        cloud_scheduled_backup_times.extend(settings.get("cloud_schedule_times", []))
        update_cloud_schedule_list()

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

    backup_cloud_settings_before_update(settings, saved_version)
    cloud_schedule_times = settings.get("cloud_schedule_times", [])
    cloud_selected_items = settings.get("cloud_selected_items", [])
    google_drive_folder = settings.get("google_drive_folder", "My Drive")
    sql_include_scheduler = settings.get("sql_include_scheduler", False)

    settings["app_version"] = APP_VERSION
    settings["last_profile"] = None
    settings["destination"] = ""
    settings["manual_backup_destination"] = ""
    settings["auto_backup_destination"] = ""
    settings["scheduler_selected_items"] = []
    settings["schedule_times"] = []
    settings["cloud_schedule_times"] = cloud_schedule_times
    settings["cloud_selected_items"] = cloud_selected_items
    settings["google_drive_folder"] = google_drive_folder
    settings["sql_include_scheduler"] = sql_include_scheduler

    return True

def backup_cloud_settings_before_update(settings, saved_version):
    cloud_backup = {
        "backup_created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "from_app_version": saved_version or "unknown",
        "to_app_version": APP_VERSION,
        "cloud_schedule_times": settings.get("cloud_schedule_times", []),
        "cloud_selected_items": settings.get("cloud_selected_items", []),
        "google_drive_folder": settings.get("google_drive_folder", "My Drive"),
        "sql_include_scheduler": settings.get("sql_include_scheduler", False)
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(
        app_data_folder,
        f"cloud_settings_backup_{saved_version or 'unknown'}_to_{APP_VERSION}_{timestamp}.json"
    )

    with open(backup_file, "w", encoding="utf-8") as file:
        json.dump(cloud_backup, file, indent=4)

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
            notify_files_in_use_backup_continuing(locked_files)
            write_backup_event(
                "local",
                "info",
                f"Backup continuing with {len(locked_files)} file(s) in use.",
                destination=destination,
                backup_format=format_choice.upper()
            )

        output = perform_backup(items, destination, format_choice)

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

def refresh_dashboard(schedule_next=True):
    dashboard_status_var.set(status_var.get())
    dashboard_progress_var.set(progress_var.get())
    dashboard_schedule_var.set(scheduler_status_var.get())
    dashboard_cloud_var.set(cloud_status_var.get())
    dashboard_google_var.set(google_drive_status_var.get())
    dashboard_storage_var.set(get_backup_folder_summary())

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

    if not os.path.exists(destination):
        os.makedirs(destination)

    locked_files = get_locked_files(items)

    if locked_files:
        notify_files_in_use_backup_continuing(locked_files)
        write_scheduler_status(
            f"Scheduled backup continuing with {len(locked_files)} file(s) in use."
        )

    try:
        set_ui_busy(True)
        output = perform_backup(items, destination, format_var.get())
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
            "local",
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
    destination = get_schedule_destination(schedule)

    before_files = set()

    if destination and os.path.exists(destination):
        before_files = set(os.listdir(destination))

    file_backup_success = run_scheduled_file_backup(schedule)

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
    queue_ui_action(refresh_logs_tab)

# =========================================================
# Scheduler and Tray Helpers
# =========================================================

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

                if today not in schedule_days:
                    should_run = False
                elif schedule_mode == "interval":
                    try:
                        interval_minutes = int(schedule.get("interval_minutes", 10))
                    except (TypeError, ValueError):
                        interval_minutes = 10

                    last_run = schedule_last_run_times.get(schedule_key)
                    should_run = not last_run or (now - last_run).total_seconds() >= interval_minutes * 60
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

                if isinstance(schedule, dict) and schedule.get("mode") == "interval":
                    schedule_last_run_times[schedule_key] = now
                elif isinstance(schedule, dict):
                    schedule_last_run_times[schedule_key] = now.strftime("%Y-%m-%d %I:%M %p")

                scheduler_status_var.set("Running Backup")
                status_label.config(image=icon_blue)
                update_tray_icon(LOCAL_SCHEDULE_COLOR)

                schedule_name = schedule.get("name", "Local Backup") if isinstance(schedule, dict) else "Local Backup"
                write_scheduler_status(f"Scheduled backup started: {schedule_name}")

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
            category = "Auto" if schedule.get("mode") == "interval" else "Manual"
            source = "Office files" if schedule.get("source") == "office_files" else "Selected items"
            description = schedule.get("description", "")
            destination = str(schedule.get("destination") or "").strip()
            destination_name = os.path.basename(os.path.normpath(destination)) if destination else "Default location"
            days = ", ".join(schedule.get("days", []))

            if schedule.get("mode") == "interval":
                timing = f"Every {schedule.get('interval_minutes', 10)} min"
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
        messagebox.showwarning("No Items", "Add files or folders in the Scheduler tab before adding a manual schedule.")
        return

    if not destination:
        messagebox.showwarning("No Manual Destination", "Choose a manual backup location first.")
        return

    new_schedule = {
        "name": schedule_name_var.get().strip() or ("Office Files Backup" if uses_office_files else "Scheduler Backup"),
        "mode": "time",
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
        f"Backup schedule added: {new_schedule['name']} at {backup_time}"
    )

def add_office_auto_backup_schedule():
    selected_schedule_days = [day for day, var in selected_days.items() if var.get()]

    if not selected_schedule_days:
        messagebox.showwarning("No Days Selected", "Select at least one backup day.")
        return

    try:
        interval_minutes = int(auto_backup_interval_var.get())
    except ValueError:
        messagebox.showwarning("Invalid Interval", "Enter a whole number of minutes.")
        return

    if interval_minutes < 1:
        messagebox.showwarning("Invalid Interval", "Interval must be at least 1 minute.")
        return

    destination = auto_backup_destination_var.get().strip()

    if not destination:
        messagebox.showwarning("No Auto Destination", "Choose an auto backup location first.")
        return

    new_schedule = {
        "name": schedule_name_var.get().strip() or "Office Auto Backup",
        "mode": "interval",
        "source": "office_files",
        "interval_minutes": interval_minutes,
        "destination": destination,
        "description": "Common office files from Desktop, Documents, Downloads, and OneDrive",
        "days": selected_schedule_days
    }

    scheduled_backup_times.append(new_schedule)

    update_schedule_list()
    save_app_settings()

    write_scheduler_status(
        f"Office auto backup schedule added: every {interval_minutes} minute(s)"
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
    check_scheduled_backups(schedule_next=False)

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
    root.minsize(900, 640)

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
        save_app_settings()
        selection_window.destroy()

    def select_folder():
        folder = filedialog.askdirectory(parent=selection_window)

        if folder:
            cloud_selected_items.append(folder)

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

    cloud_scheduler_running = True
    cloud_status_var.set("Running")
    cloud_schedule_enabled_var.set(True)
    refresh_schedule_tray_icon()

    messagebox.showinfo(
        "Cloud Backup Scheduler Started",
        "Cloud backup scheduler is now running."
    )
    check_cloud_scheduled_backups(schedule_next=False)

def stop_cloud_scheduler():
    global cloud_scheduler_running

    cloud_scheduler_running = False
    cloud_status_var.set("Stopped")
    cloud_schedule_enabled_var.set(False)
    refresh_schedule_tray_icon()

def update_cloud_selection_count():
    if "cloud_items_listbox" in globals():
        cloud_items_listbox.delete(0, END)

        for item in cloud_selected_items:
            cloud_items_listbox.insert(END, item)

    cloud_selected_count_var.set(
        f"{len(cloud_selected_items)} cloud item(s) selected"
    )

def update_office_preset_caption():
    if office_schedule_var.get():
        office_preset_caption_var.set(
            "Office preset saves Word, Excel, PowerPoint, PDF, text/CSV/RTF, OneNote, and Outlook files from Desktop, Documents, Downloads, and OneDrive."
        )
    else:
        office_preset_caption_var.set("")

def refresh_scheduler_sections():
    if "scheduler_paned" not in globals():
        return

    for pane in scheduler_paned.panes():
        scheduler_paned.forget(pane)

    min_sizes = {
        "manual": 360,
        "auto": 300
    }

    for section_key in scheduler_section_order:
        section = scheduler_section_widgets.get(section_key)

        if section:
            scheduler_paned.add(section, minsize=min_sizes.get(section_key, 100))

    update_scheduler_scroll_region()

def update_scheduler_scroll_region(event=None):
    if "scheduler_canvas" not in globals():
        return

    scheduler_canvas.update_idletasks()
    scroll_region = scheduler_canvas.bbox("all")
    scheduler_canvas.configure(scrollregion=scroll_region)

    canvas_width = scheduler_canvas.winfo_width()
    canvas_height = scheduler_canvas.winfo_height()
    content_width = max(canvas_width, scheduler_content.winfo_reqwidth(), 940)
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
    header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
    header.columnconfigure(0, weight=1)

    ttk.Label(
        header,
        text=title,
        background=CARD,
        foreground=TEXT,
        font=("Segoe UI", 12, "bold")
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
        header.pack(fill=X, pady=(0, 10))
    else:
        header.grid(row=0, column=0, columnspan=columnspan, sticky="ew", pady=(0, 10))

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
    if "dashboard_top_row" not in globals():
        return

    width = dashboard_top_row.winfo_width()

    for card in (dashboard_status_card, dashboard_schedule_card, dashboard_cloud_card):
        card.grid_forget()

    if width < 900:
        for index, card in enumerate((dashboard_status_card, dashboard_schedule_card, dashboard_cloud_card)):
            card.grid(row=index, column=0, sticky="ew", pady=(0, 8))

        dashboard_top_row.columnconfigure(0, weight=1)
        dashboard_top_row.columnconfigure(1, weight=0)
        dashboard_top_row.columnconfigure(2, weight=0)
    else:
        dashboard_status_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        dashboard_schedule_card.grid(row=0, column=1, sticky="nsew", padx=8)
        dashboard_cloud_card.grid(row=0, column=2, sticky="nsew", padx=(8, 0))

        for column in range(3):
            dashboard_top_row.columnconfigure(column, weight=1)

def update_cloud_schedule_layout(event=None):
    if "cloud_schedule_card" not in globals() or "cloud_schedule_listbox" not in globals():
        return

    width = cloud_schedule_card.winfo_width()

    if width < 950:
        cloud_schedule_listbox.grid(
            row=6,
            column=0,
            columnspan=4,
            sticky="ew",
            padx=(0, 0),
            pady=(12, 0)
        )
    else:
        cloud_schedule_listbox.grid(
            row=1,
            column=4,
            rowspan=5,
            sticky="nw",
            padx=(20, 0),
            pady=(0, 0)
        )

def arrange_responsive_button_grid(frame, buttons, columns):
    columns = max(1, columns)

    for child in frame.winfo_children():
        child.pack_forget()
        child.grid_forget()

    for index, button in enumerate(buttons):
        row = index // columns
        column = index % columns
        padx = (0, 8) if column < columns - 1 else (0, 0)
        pady = (0, 8) if row < (len(buttons) - 1) // columns else (0, 0)
        button.grid(row=row, column=column, sticky="ew", padx=padx, pady=pady)

    for column in range(columns):
        frame.columnconfigure(column, weight=1, minsize=120)

def update_backup_tab_layout(event=None):
    if "button_row" not in globals() or "btn_clear_list" not in globals():
        return

    width = button_row.winfo_width()
    arrange_responsive_button_grid(
        button_row,
        [btn_add_files, btn_add_folder, btn_clear_list],
        1 if width < 430 else 3
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
        [btn_find_servers, btn_test_sql, btn_load_databases],
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
            cloud_status_var.set("Idle")
            refresh_schedule_tray_icon()

    if schedule_next:
        root.after(SCHEDULER_POLL_INTERVAL_MS, check_cloud_scheduled_backups)

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
manual_backup_destination_var = StringVar()
auto_backup_destination_var = StringVar()
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
office_schedule_var = BooleanVar(value=False)
office_preset_caption_var = StringVar(value="")
auto_backup_interval_var = StringVar(value="10")
scheduler_selected_count_var = StringVar(value="0 file(s), 0 folder(s) selected")

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
dashboard_top_row.bind("<Configure>", update_dashboard_card_layout)

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
    height=12,
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
dashboard_history_button_row.pack(fill=X, pady=(10, 0))

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
configure_auto_hide_scrollbar(
    log_text,
    log_scrollbar,
    VERTICAL,
    "pack",
    {"side": RIGHT, "fill": Y}
)

logs_button_row = ttk.Frame(logs_tab)
logs_button_row.pack(fill=X, pady=(10, 0))

btn_view_log = ttk.Button(logs_button_row, text="Refresh Backup Logs", command=refresh_logs_tab)
btn_view_log.pack(side=LEFT)

main_buttons.append(btn_view_log)

# =========================================================
# Cloud Backup Tab: File Selection
# =========================================================

cloud_vertical_split = PanedWindow(
    settings_tab,
    orient=VERTICAL,
    bg=BG,
    bd=0,
    sashwidth=8,
    sashrelief=FLAT,
    showhandle=False
)
cloud_vertical_split.pack(fill=BOTH, expand=True)

cloud_top_split = PanedWindow(
    cloud_vertical_split,
    orient=HORIZONTAL,
    bg=BG,
    bd=0,
    sashwidth=8,
    sashrelief=FLAT,
    showhandle=False
)

cloud_bottom_split = PanedWindow(
    cloud_vertical_split,
    orient=HORIZONTAL,
    bg=BG,
    bd=0,
    sashwidth=8,
    sashrelief=FLAT,
    showhandle=False
)

cloud_vertical_split.add(cloud_top_split, minsize=220)
cloud_vertical_split.add(cloud_bottom_split, minsize=260)

cloud_files_card = ttk.Frame(settings_tab, style="Card.TFrame", padding=15)

create_cloud_section_header(
    cloud_files_card,
    "cloud_items",
    "Selected Cloud Backup Items",
    manager="pack"
)

cloud_items_button_row = ttk.Frame(cloud_files_card, style="Card.TFrame")
cloud_items_button_row.pack(fill=X, pady=(0, 10))
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
cloud_items_list_frame.pack(fill=BOTH, expand=True)

cloud_items_listbox = Listbox(
    cloud_items_list_frame,
    bg="#1f1f1f",
    fg="#ffffff",
    selectbackground="#0078d4",
    selectforeground="#ffffff",
    font=("Segoe UI", 10),
    relief=FLAT,
    height=6,
    selectmode=EXTENDED
)
cloud_items_listbox.pack(side=LEFT, fill=BOTH, expand=True)

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
cloud_selected_count_label.pack(anchor="w", pady=(8, 0))

main_buttons.extend([
    btn_select_cloud_items,
    btn_remove_cloud_items,
    btn_clear_cloud_items
])

# =========================================================
# Cloud Backup Tab: Google Drive Location
# =========================================================

google_drive_card = ttk.Frame(settings_tab, style="Card.TFrame", padding=15)
google_drive_card.columnconfigure(1, weight=1)

create_cloud_section_header(
    google_drive_card,
    "google_drive",
    "Google Drive Backup Location",
    columnspan=3
)

google_drive_folder_label = ttk.Label(
    google_drive_card,
    text="Cloud Folder:",
    background=CARD,
    foreground=TEXT
)
google_drive_folder_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)

google_drive_folder_entry = ttk.Entry(
    google_drive_card,
    textvariable=google_drive_folder_var
)
google_drive_folder_entry.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=5)

google_drive_status_label = ttk.Label(
    google_drive_card,
    textvariable=google_drive_status_var,
    background=CARD,
    foreground="#57f287",
    font=("Segoe UI", 9, "bold")
)
google_drive_status_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

google_drive_button_row = ttk.Frame(google_drive_card, style="Card.TFrame")
google_drive_button_row.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 0))
google_drive_button_row.bind("<Configure>", update_google_drive_layout)

btn_connect_google = ttk.Button(
    google_drive_button_row,
    text="Google Drive",
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

sql_card = ttk.Frame(settings_tab, style="Card.TFrame", padding=15)
sql_card.columnconfigure(1, weight=1)

create_cloud_section_header(
    sql_card,
    "sql",
    "SQL Backup Settings",
    columnspan=3
)

ttk.Label(sql_card, text="Server:", background=CARD, foreground=TEXT).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)

sql_server_entry = ttk.Entry(sql_card, textvariable=sql_server_var, width=30)
sql_server_entry.grid(row=1, column=1, sticky="ew", pady=5)

sql_button_row = ttk.Frame(sql_card, style="Card.TFrame")
sql_button_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 5))
sql_button_row.bind("<Configure>", update_sql_card_layout)

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

ttk.Label(
    sql_card,
    textvariable=sql_selected_count_var,
    background=CARD,
    foreground="#57f287",
    font=("Segoe UI", 9)
).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 2))

ttk.Label(
    sql_card,
    text="If finding SQL Servers takes too long, type the server name above and press Enter. Always test the connection.",
    background=CARD,
    foreground="#ffb86c",
    font=("Segoe UI", 9, "bold"),
    wraplength=760,
    justify=LEFT
).grid(row=4, column=0, columnspan=3, sticky="w", pady=(0, 8))

main_buttons.extend([
    btn_find_servers,
    btn_test_sql,
    btn_load_databases
])

# =========================================================
# Cloud Backup Tab: Cloud Schedule
# =========================================================

cloud_schedule_card = ttk.Frame(settings_tab, style="Card.TFrame", padding=15)
cloud_schedule_card.columnconfigure(0, weight=0)
cloud_schedule_card.columnconfigure(1, weight=0)
cloud_schedule_card.columnconfigure(2, weight=0)
cloud_schedule_card.columnconfigure(3, weight=0)
cloud_schedule_card.columnconfigure(4, weight=1)
cloud_schedule_card.bind("<Configure>", update_cloud_schedule_layout)

create_cloud_section_header(
    cloud_schedule_card,
    "cloud_schedule",
    "Cloud Backup Scheduler",
    columnspan=5
)

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

main_buttons.extend([
    btn_add_cloud_time,
    btn_remove_cloud_time,
    btn_start_cloud_scheduler,
    btn_stop_cloud_scheduler
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

cloud_action_row = ttk.Frame(settings_tab)
cloud_action_row.pack(fill=X, pady=15)
cloud_action_row.columnconfigure(0, weight=1)

btn_start_cloud_schedule = ttk.Button(
    cloud_action_row,
    text="Start Local + Cloud Schedule",
    width=BTN_WIDTH + 10,
    command=start_cloud_backup_schedule,
    style="Accent.TButton"
)
btn_start_cloud_schedule.grid(row=0, column=0, sticky="e")

main_buttons.append(btn_start_cloud_schedule)


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
button_row.bind("<Configure>", update_backup_tab_layout)

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
scheduler_canvas.bind("<Configure>", update_scheduler_scroll_region)
scheduler_canvas.bind("<Enter>", bind_scheduler_mousewheel)
scheduler_canvas.bind("<Leave>", unbind_scheduler_mousewheel)

scheduler_paned = PanedWindow(
    scheduler_content,
    orient=HORIZONTAL,
    bg=CARD,
    bd=0,
    height=430,
    width=1040,
    sashwidth=6,
    sashrelief=FLAT,
    showhandle=False
)
scheduler_paned.pack(fill=X, pady=(0, 10))

manual_scheduler_card = ttk.Frame(scheduler_paned, style="Card.TFrame", padding=15)
manual_scheduler_card.columnconfigure(0, weight=1)

create_scheduler_section_header(manual_scheduler_card, "manual", "Manual Backups")

hours_var = StringVar(value="12")
minutes_var = StringVar(value="00")
ampm_var = StringVar(value="AM")

schedule_details_frame = ttk.Frame(manual_scheduler_card, style="Card.TFrame")
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
).pack(side=LEFT, padx=(0, 14))

ttk.Checkbutton(
    schedule_details_frame,
    text="Office file preset",
    variable=office_schedule_var,
    command=update_office_preset_caption
).pack(side=LEFT)

ttk.Label(
    manual_scheduler_card,
    textvariable=office_preset_caption_var,
    background=CARD,
    foreground=MUTED,
    wraplength=430,
    justify=LEFT
).grid(row=2, column=0, columnspan=4, sticky="w", pady=(0, 8))

scheduler_items_frame = ttk.Frame(manual_scheduler_card, style="Card.TFrame")
scheduler_items_frame.grid(row=3, column=0, columnspan=4, sticky="nsew", pady=(0, 8))
scheduler_items_frame.columnconfigure(0, weight=1)
scheduler_items_frame.rowconfigure(1, weight=1)
manual_scheduler_card.rowconfigure(3, weight=1)

ttk.Label(
    scheduler_items_frame,
    text="Scheduler Backup Items",
    background=CARD,
    foreground=TEXT,
    font=("Segoe UI", 10, "bold")
).grid(row=0, column=0, sticky="w", pady=(0, 6))

scheduler_items_listbox = Listbox(
    scheduler_items_frame,
    height=4,
    width=60,
    bg="#1f1f1f",
    fg="#ffffff",
    selectbackground="#0078d4",
    selectforeground="#ffffff",
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

scheduler_items_button_row = ttk.Frame(scheduler_items_frame, style="Card.TFrame")
scheduler_items_button_row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

btn_add_scheduler_files = ttk.Button(
    scheduler_items_button_row,
    text="Add Files",
    command=add_scheduler_files
)
btn_add_scheduler_files.pack(side=LEFT, padx=(0, 6))

btn_add_scheduler_folder = ttk.Button(
    scheduler_items_button_row,
    text="Add Folder",
    command=add_scheduler_folder
)
btn_add_scheduler_folder.pack(side=LEFT, padx=(0, 6))

btn_remove_scheduler_item = ttk.Button(
    scheduler_items_button_row,
    text="Remove Selected",
    command=remove_selected_scheduler_item
)
btn_remove_scheduler_item.pack(side=LEFT, padx=(0, 6))

btn_clear_scheduler_items = ttk.Button(
    scheduler_items_button_row,
    text="Clear",
    command=clear_scheduler_items
)
btn_clear_scheduler_items.pack(side=LEFT)

ttk.Label(
    scheduler_items_frame,
    textvariable=scheduler_selected_count_var,
    background=CARD,
    foreground=MUTED,
    font=("Segoe UI", 9)
).grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))

manual_destination_row = ttk.Frame(manual_scheduler_card, style="Card.TFrame")
manual_destination_row.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(0, 8))
manual_destination_row.columnconfigure(1, weight=1)

ttk.Label(
    manual_destination_row,
    text="Destination:",
    background=CARD,
    foreground=TEXT
).grid(row=0, column=0, sticky="w", padx=(0, 8))

ttk.Entry(
    manual_destination_row,
    textvariable=manual_backup_destination_var,
    width=36
).grid(row=0, column=1, sticky="ew", padx=(0, 8))

btn_browse_manual_destination = ttk.Button(
    manual_destination_row,
    text="Browse",
    command=choose_manual_backup_destination
)
btn_browse_manual_destination.grid(row=0, column=2, sticky="w")

time_row = ttk.Frame(manual_scheduler_card, style="Card.TFrame")
time_row.grid(row=5, column=0, columnspan=4, sticky="w", pady=(0, 8))

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

days_frame = ttk.Frame(manual_scheduler_card, style="Card.TFrame")
days_frame.grid(row=6, column=0, columnspan=4, sticky="w", pady=(0, 8))

ttk.Label(
    days_frame,
    text="Days:",
    background=CARD,
    foreground=TEXT
).grid(row=0, column=0, padx=(0, 8))

for i, (day, var) in enumerate(selected_days.items(), start=1):
    ttk.Checkbutton(days_frame, text=day, variable=var).grid(row=0, column=i, padx=3)

manual_button_row = ttk.Frame(manual_scheduler_card, style="Card.TFrame")
manual_button_row.grid(row=7, column=0, columnspan=4, sticky="w", pady=(2, 0))
manual_button_row.columnconfigure(0, minsize=132)
manual_button_row.columnconfigure(1, minsize=132)

btn_add_time = ttk.Button(
    manual_button_row,
    text="Add Manual",
    command=add_backup_time,
    width=BTN_WIDTH
)
btn_add_time.grid(row=0, column=0, sticky="ew", padx=(0, 8))

btn_remove_time = ttk.Button(
    manual_button_row,
    text="Remove Selected",
    command=remove_selected_time,
    width=BTN_WIDTH
)
btn_remove_time.grid(row=0, column=1, sticky="ew")

schedule_card = ttk.Frame(scheduler_content, style="Card.TFrame", padding=15)
schedule_card.columnconfigure(0, weight=1)
schedule_card.pack(fill=BOTH, expand=True, pady=(0, 10))

create_scheduler_section_header(schedule_card, "scheduled", "Scheduled Backups")

schedule_list_frame = ttk.Frame(schedule_card, style="Card.TFrame")
schedule_list_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
schedule_card.rowconfigure(1, weight=1)
schedule_list_frame.columnconfigure(0, weight=1)
schedule_list_frame.rowconfigure(0, weight=1)

schedule_listbox = Listbox(
    schedule_list_frame,
    height=8,
    width=105,
    bg="#1f1f1f",
    fg="#ffffff",
    selectbackground="#0078d4",
    selectforeground="#ffffff",
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

scheduler_control_row = ttk.Frame(schedule_card, style="Card.TFrame")
scheduler_control_row.grid(row=2, column=0, sticky="w")
scheduler_control_row.columnconfigure(0, minsize=132)
scheduler_control_row.columnconfigure(1, minsize=132)

btn_start_scheduler = ttk.Button(
    scheduler_control_row,
    text="Start Schedule",
    width=BTN_WIDTH,
    command=start_scheduler,
    style="CompactAccent.TButton"
)

btn_stop_scheduler = ttk.Button(
    scheduler_control_row,
    text="Stop Schedule",
    width=BTN_WIDTH,
    command=stop_scheduler
)

status_label = ttk.Label(
    scheduler_control_row,
    textvariable=scheduler_status_var,
    image=icon_red,
    compound="left",
    background=CARD,
    foreground=TEXT
)

btn_start_scheduler.grid(row=0, column=0, sticky="ew", padx=(0, 8))
btn_stop_scheduler.grid(row=0, column=1, sticky="ew")
status_label.grid(row=0, column=2, sticky="w", padx=(10, 0))

auto_scheduler_card = ttk.Frame(scheduler_paned, style="Card.TFrame", padding=15)
auto_scheduler_card.columnconfigure(0, weight=1)

create_scheduler_section_header(auto_scheduler_card, "auto", "Auto Backups")

auto_destination_row = ttk.Frame(auto_scheduler_card, style="Card.TFrame")
auto_destination_row.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 8))
auto_destination_row.columnconfigure(1, weight=1)

ttk.Label(
    auto_destination_row,
    text="Destination:",
    background=CARD,
    foreground=TEXT
).grid(row=0, column=0, sticky="w", padx=(0, 8))

ttk.Entry(
    auto_destination_row,
    textvariable=auto_backup_destination_var,
    width=36
).grid(row=0, column=1, sticky="ew", padx=(0, 8))

btn_browse_auto_destination = ttk.Button(
    auto_destination_row,
    text="Browse",
    command=choose_auto_backup_destination
)
btn_browse_auto_destination.grid(row=0, column=2, sticky="w")

auto_options_frame = ttk.Frame(auto_scheduler_card, style="Card.TFrame")
auto_options_frame.grid(row=2, column=0, columnspan=4, sticky="w", pady=(0, 8))

ttk.Label(
    auto_options_frame,
    text="Office file interval:",
    background=CARD,
    foreground=TEXT
).pack(side=LEFT, padx=(0, 6))

ttk.Spinbox(
    auto_options_frame,
    from_=1,
    to=1440,
    textvariable=auto_backup_interval_var,
    width=5
).pack(side=LEFT, padx=(0, 6))

ttk.Label(
    auto_options_frame,
    text="min",
    background=CARD,
    foreground=TEXT
).pack(side=LEFT, padx=(0, 10))

btn_add_office_auto = ttk.Button(
    auto_options_frame,
    text="Add Auto",
    command=add_office_auto_backup_schedule,
    width=BTN_WIDTH
)
btn_add_office_auto.pack(side=LEFT)

scheduler_section_widgets.update({
    "manual": manual_scheduler_card,
    "auto": auto_scheduler_card
})
refresh_scheduler_sections()

main_buttons.extend([
    btn_add_scheduler_files,
    btn_add_scheduler_folder,
    btn_remove_scheduler_item,
    btn_clear_scheduler_items,
    btn_add_time,
    btn_remove_time,
    btn_browse_manual_destination,
    btn_browse_auto_destination,
    btn_add_office_auto,
    btn_start_scheduler,
    btn_stop_scheduler
])


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
root.after_idle(update_dashboard_card_layout)
root.after_idle(update_cloud_schedule_layout)
root.after_idle(update_backup_tab_layout)
root.after_idle(update_cloud_items_layout)
root.after_idle(update_sql_card_layout)
root.after_idle(update_google_drive_layout)
root.protocol("WM_DELETE_WINDOW", on_app_close)

setup_tray_icon()
refresh_dashboard()
check_scheduled_backups()
check_cloud_scheduled_backups()
process_progress_queue()
process_ui_action_queue()
process_backup_result_queue()

root.mainloop()
