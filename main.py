APP_NAME = "Backup Compressor"
APP_VERSION = "2.0.3"
BG = "#313338"
CARD = "#2b2d31"
CARD_DARK = "#1e1f22"
TEXT = "#f2f3f5"
MUTED = "#b5bac1"
ACCENT = "#5865f2"
ACCENT_HOVER = "#4752c4"
BTN_WIDTH = 16
# =========================================================
# 📦 IMPORTS
# =========================================================
import os
import zipfile
import subprocess
import json
import threading
import sys
import py7zr
import pystray
import winshell
import ctypes
from PIL import Image as PILImage, ImageDraw    
from datetime import datetime
from tkinter import *
from tkinter import filedialog, messagebox
from tkinter import ttk
# =========================================================
# ⚙️ APP CONFIG / GLOBAL VARIABLES
# =========================================================
main_buttons = []
selected_items = []
scheduled_backup_times = []
scheduler_running = False
last_run_time = None
#settings_file = "app_settings.json"
active_profile_path = None
backup_running = False
tray_icon = None
app_should_exit = False 

app_data_folder = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), APP_NAME)
os.makedirs(app_data_folder, exist_ok=True)

settings_file = os.path.join(app_data_folder, "app_settings.json")

logs_folder = os.path.join(app_data_folder, "logs")
os.makedirs(logs_folder, exist_ok=True)

backup_log_file = os.path.join(logs_folder, "backup_log.txt")

# =========================================================
# 📁 FILE SELECTION & LIST MANAGEMENT
# =========================================================

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

def add_files():
    files = filedialog.askopenfilenames()
    selected_items.extend(files)
    update_list()

def add_folder():
    folder = filedialog.askdirectory()
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
    folder = filedialog.askdirectory()
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

def create_zip(output_path):
    total_files = count_backup_files()
    processed = 0
    if total_files == 0:
           raise ValueError("No files found to back up.")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for item in selected_items:
            if os.path.isfile(item):
                zipf.write(item, os.path.basename(item))
                processed += 1
                set_progress((processed / total_files) * 100, f"Backing up: {os.path.basename(item)}")
            
            elif os.path.isdir(item):
                for root_dir, dirs, files in os.walk(item):
                    for file in files:
                        full_path = os.path.join(root_dir, file)
                        arcname = os.path.relpath(full_path, os.path.dirname(item))
                        zipf.write(full_path, arcname)

                        processed += 1
                        set_progress((processed / total_files) * 100, f"Backing up: {file}")

    set_progress(100, "ZIP backup complete.")   

def create_7z(output_path):
    progress_bar.config(mode="indeterminate")
    progress_bar.start(10)
    status_var.set("Creating 7Z backup... please wait.")
    root.update_idletasks()

    with py7zr.SevenZipFile(output_path, "w") as archive:
        for item in selected_items:
            archive.writeall(item, os.path.basename(item))

    progress_bar.stop()
    progress_bar.config(mode="determinate")
    set_progress(100, "7Z backup complete.")

def create_rar(output_path):
    rar_exe = r"C:\Program Files\WinRAR\Rar.exe"

    if not os.path.exists(rar_exe):
        raise FileNotFoundError("WinRAR/Rar.exe was not found. Check the WinRAR install path.")

    progress_bar.config(mode="indeterminate")
    progress_bar.start(10)
    status_var.set("Creating RAR backup... please wait.")
    root.update_idletasks()

    command = [rar_exe, "a", output_path] + selected_items
    subprocess.run(command, check=True)

    progress_bar.stop()
    progress_bar.config(mode="determinate")
    set_progress(100, "RAR backup complete.")

def get_file_count_and_size():
    total_files = 0
    total_size = 0

    for item in selected_items:
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

def set_ui_busy(is_busy):
    global backup_running
    backup_running = is_busy

    state = DISABLED if is_busy else NORMAL

    for button in main_buttons:
        button.config(state=state)

    if is_busy:
        status_var.set("Backup running... please wait.")
    else:
        status_var.set("Ready")

def open_destination_folder():
    destination = destination_var.get()

    if not destination or not os.path.exists(destination):
        return

    try:
        os.startfile(destination)
    except Exception:
        pass

def save_app_settings():
    settings = {
        "last_profile": active_profile_path,
        "destination": destination_var.get(),
        "format": format_var.get(),
        "schedule_times": scheduled_backup_times
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

        scheduled_backup_times.clear()
        scheduled_backup_times.extend(settings.get("schedule_times", []))
        update_schedule_list()

        active_profile_path = settings.get("last_profile")

    except Exception:
        pass

def on_app_close():
    save_app_settings()

    if app_should_exit:
        root.destroy()
    else:
        hide_window()

def start_backup(show_messages=True):
    if backup_running:
        return False

    if not selected_items:
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

    locked_files = get_locked_files()

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
        set_progress(0, "Starting backup...")

        total_files, total_size = get_file_count_and_size()
        status_var.set(f"Backing up {total_files} files | {format_size(total_size)}")

        if format_choice == "zip":
            output = os.path.join(destination, get_backup_name("zip"))
            create_zip(output)

        elif format_choice == "7z":
            output = os.path.join(destination, get_backup_name("7z"))
            create_7z(output)

        elif format_choice == "rar":
            output = os.path.join(destination, get_backup_name("rar"))
            create_rar(output)

        else:
            raise ValueError("Unknown backup format selected.")

        write_backup_log(destination, output, format_choice)
        save_app_settings()

        set_progress(100, f"Backup complete: {os.path.basename(output)}")

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
        if show_messages:
            messagebox.showerror("Backup Failed", str(e))
        else:
            write_scheduler_status(f"Scheduled backup failed: {e}")

        return False

    finally:
        set_ui_busy(False)

        if scheduler_running:
            scheduler_status_var.set("Idle")
            status_label.config(image=icon_teal)
            update_tray_icon("#1abc9c")
        else:
            status_label.config(image=icon_red)
            update_tray_icon("#e74c3c")

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

def get_locked_files():
    locked_files = []

    for item in selected_items:
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

def write_backup_log(destination, output_file, format_choice):
    log_path = backup_log_file

    with open(log_path, "a", encoding="utf-8") as log:
        log.write("====================================\n")
        log.write(f"Backup Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"Format: {format_choice.upper()}\n")
        log.write(f"Output File: {output_file}\n")
        log.write("Items Backed Up:\n")

        for item in selected_items:
            log.write(f"- {item}\n")

        log.write("\n")

def save_profile():
    if not selected_items:
        messagebox.showwarning("No Items", "Add files or folders before saving a profile.")
        return
        

    profile_path = filedialog.asksaveasfilename(
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
        return

    log_window = Toplevel(root)
    log_window.title("Backup Log Viewer")
    log_window.geometry("700x450")
    log_window.configure(bg="#1e1e1e")

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

    style.configure("TRadiobutton", background="#2d2d2d", foreground="#ffffff", font=("Segoe UI", 10))
    style.configure("TEntry", fieldbackground="#3a3a3a", foreground="#ffffff")

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


def set_progress(value, message):
    progress_var.set(value)
    status_var.set(message)
    root.update_idletasks()

def count_backup_files():
    total = 0

    for item in selected_items:
        if os.path.isfile(item):
            total += 1
        elif os.path.isdir(item):
            for _, _, files in os.walk(item):
                total += len(files)

    return total

def run_backup_silent():
    start_backup(show_messages=False)

def write_scheduler_status(message):
    with open(backup_log_file, "a", encoding="utf-8") as log:
        log.write(f"[Scheduler] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - v{APP_VERSION} - {message}\n")

def check_scheduled_backups():  
    global last_run_time    
    if scheduler_running:
        today = datetime.now().strftime("%a")

        if not selected_days[today].get():
            scheduler_status_var.set("Idle")      # when waiting
            status_label.config(image=icon_teal)
            update_tray_icon("#1abc9c")
            root.after(60000, check_scheduled_backups)
            return

        current_time = datetime.now().strftime("%I:%M %p").lstrip("0")

        if current_time in scheduled_backup_times and current_time != last_run_time:
            last_run_time = current_time

            scheduler_status_var.set("Running Backup")
            status_label.config(image=icon_green)
            update_tray_icon("#2ecc71")
            write_scheduler_status(f"Scheduled backup started at {current_time}")

            backup_thread = threading.Thread(target=run_backup_silent)
            backup_thread.daemon = True
            backup_thread.start()

        else:
                if scheduler_status_var.get() != "Running Backup":
                    scheduler_status_var.set("Idle")      # when waiting
                    status_label.config(image=icon_teal)
                    update_tray_icon("#1abc9c")

    root.after(60000, check_scheduled_backups)

def update_schedule_list():
    schedule_listbox.delete(0, END)
    for backup_time in scheduled_backup_times:
        schedule_listbox.insert(END, backup_time)

def add_backup_time():
    raw_time = f"{hours_var.get()}:{minutes_var.get()}"

    try:
        dt = datetime.strptime(raw_time, "%H:%M")
    except ValueError:
        messagebox.showerror("Invalid Time", "Please select a valid time.")
        return

    backup_time = dt.strftime("%I:%M %p").lstrip("0")

    if backup_time not in scheduled_backup_times:
        scheduled_backup_times.append(backup_time)
        update_schedule_list()
        write_scheduler_status(f"Backup time added: {backup_time}")
    

def remove_selected_time():
    selected = schedule_listbox.curselection()

    if not selected:
        messagebox.showwarning("No Time Selected", "Select a backup time to remove.")
        return

    index = selected[0]
    time_value = schedule_listbox.get(index)

    schedule_listbox.delete(index)

    if time_value in scheduled_backup_times:
        scheduled_backup_times.remove(time_value)

    write_scheduler_status(f"Backup time removed: {time_value}")

def update_tray_icon(color):
    if tray_icon:
        tray_icon.icon = create_tray_image(color)

def start_scheduler():  # start
    global scheduler_running

    if not scheduled_backup_times:
        messagebox.showwarning("No Schedule", "Add at least one backup time first.")
        return

    scheduler_running = True

    scheduler_status_var.set("Running")   # when started
    status_label.config(image=icon_green)   
    update_tray_icon("#2ecc71")   # green idle
    
    write_scheduler_status("Scheduler started")

def create_tray_image(color="#2ecc71"):
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
    update_tray_icon("#e74c3c")  # red stopped

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
        create_tray_image("#e74c3c"),
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

def add_preset_time(time_str):
    if time_str not in scheduled_backup_times:
        scheduled_backup_times.append(time_str)
        update_schedule_list()



root = Tk()

selected_days = {
    "Mon": BooleanVar(value=True),
    "Tue": BooleanVar(value=True),
    "Wed": BooleanVar(value=True),
    "Thu": BooleanVar(value=True),
    "Fri": BooleanVar(value=True),
    "Sat": BooleanVar(value=True),
    "Sun": BooleanVar(value=True),
}

root.iconbitmap(os.path.join(os.path.dirname(__file__), "app_icon.ico"))
icon_red = create_status_icon("#e74c3c")   # not running
icon_green = create_status_icon("#2ecc71") # running
icon_teal = create_status_icon("#1abc9c")  # idle

root.title("Backup Compressor")

width = 1000
height = 800

screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()

x = int((screen_width / 2) - (width / 2))
y = int((screen_height / 2) - (height / 2))

root.geometry(f"{width}x{height}+{x}+{y}")
root.minsize(800, 600)


apply_modern_style()

destination_var = StringVar()
format_var = StringVar(value="zip")
schedule_time_var = StringVar()
scheduler_status_var = StringVar(value="Stopped")
progress_var = DoubleVar(value=0)
status_var = StringVar(value="Ready")
summary_var = StringVar(value="Selected: 0 files | Total size: 0 B")

# Main tab container
notebook = ttk.Notebook(root)

notebook.pack_propagate(False)
notebook.configure(style="TNotebook")
notebook.pack(fill=BOTH, expand=True, padx=15, pady=15)

backup_tab = ttk.Frame(notebook, padding=20)
scheduler_tab = ttk.Frame(notebook, padding=20)
logs_tab = ttk.Frame(notebook, padding=20)
settings_tab = ttk.Frame(notebook, padding=20)

notebook.add(backup_tab, text="Backup")
notebook.add(scheduler_tab, text="Scheduler")
# notebook.add(logs_tab, text="Logs")
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

log_scrollbar = Scrollbar(logs_card)
log_scrollbar.pack(side=RIGHT, fill=Y)

log_text.config(yscrollcommand=log_scrollbar.set)
log_scrollbar.config(command=log_text.yview)

notebook.add(settings_tab, text="Settings")
profile_card = ttk.Frame(settings_tab, style="Card.TFrame", padding=15)
profile_card.pack(fill=X, pady=10)

ttk.Label(
    profile_card,
    text="Backup Profiles",
    background="#2d2d2d",
    foreground="#ffffff",
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 10))

btn_save_profile = ttk.Button(profile_card, text="Save Profile", command=save_profile)
btn_save_profile.pack(side=LEFT, padx=(0, 8))

btn_load_profile = ttk.Button(profile_card, text="Load Profile", command=load_profile)
btn_load_profile.pack(side=LEFT)

main_buttons.extend([
    btn_save_profile,
    btn_load_profile
])


# File selection card
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





# Settings card
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
    text="Backup Settings",
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

# Scheduler card
scheduler_card = ttk.Frame(scheduler_tab, style="Card.TFrame", padding=15)
scheduler_card.pack(fill=X, pady=10)


ttk.Label(
    scheduler_card,
    text="Backup Scheduler",
    background="#2d2d2d",
    foreground="#ffffff",
    font=("Segoe UI", 12, "bold")
).grid(row=0, column=0, sticky="w", columnspan=4, pady=(0, 10))

ttk.Label(scheduler_card, text="Backup Time:", background="#2d2d2d", foreground="#ffffff").grid(row=1, column=0, sticky="w", padx=(0, 8))

hours_var = StringVar(value="12")
minutes_var = StringVar(value="00")

hours_dropdown = ttk.Combobox(
    scheduler_card,
    textvariable=hours_var,
    values=[f"{i:02d}" for i in range(24)],
    width=5,
    state="readonly"
)
hours_dropdown.grid(row=1, column=1, padx=(0, 5))

minutes_dropdown = ttk.Combobox(
    scheduler_card,
    textvariable=minutes_var,
    values=[f"{i:02d}" for i in range(60)],
    width=5,
    state="readonly"
)
minutes_dropdown.grid(row=1, column=2, padx=(0, 10))

ttk.Button(scheduler_card, text="Morning (09:00 AM)", width=BTN_WIDTH,
    command=lambda: add_preset_time("9:00 AM")).grid(row=2, column=0)

ttk.Button(scheduler_card, text="Evening (6:00 PM)", width=BTN_WIDTH,
    command=lambda: add_preset_time("6:00 PM")).grid(row=2, column=1)

btn_add_time = ttk.Button(scheduler_card, text="Add Time", command=add_backup_time, width=BTN_WIDTH)
btn_remove_time = ttk.Button(scheduler_card, text="Remove Selected", command=remove_selected_time, width=BTN_WIDTH)
btn_add_time.grid(row=3, column=0, padx=10, pady=5)
btn_remove_time.grid(row=3, column=1, padx=10, pady=5)

ttk.Label(
    scheduler_card,
    text="Use 24-hour format, example: 09:00 or 18:30",
    background="#2d2d2d",
    foreground="#bdbdbd"
).grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 6))

schedule_listbox = Listbox(
    scheduler_card,
    height=6,
    width=18,
    bg="#1f1f1f",
    fg="#ffffff",
    selectbackground="#0078d4",
    selectforeground="#ffffff",
    font=("Segoe UI", 10),
    relief=FLAT
)
schedule_listbox.grid(row=2, column=2, rowspan=2, sticky="nw", padx=(25, 0), pady=(0, 10))

scheduler_control_row = ttk.Frame(scheduler_card, style="Card.TFrame")
scheduler_control_row.grid(row=8, column=0, columnspan=3, sticky="w", pady=(10, 0))

btn_start_scheduler = ttk.Button(scheduler_control_row, text="Start", width=BTN_WIDTH, command=start_scheduler)
btn_stop_scheduler = ttk.Button(scheduler_control_row, text="Stop", width=BTN_WIDTH, command=stop_scheduler)

status_label = ttk.Label(
    scheduler_control_row,
    textvariable=scheduler_status_var,
    image=icon_red,
    compound="left",
    background="#2d2d2d",
    foreground="#ffffff"
)

btn_start_scheduler.pack(side=LEFT, padx=(0, 8))
btn_stop_scheduler.pack(side=LEFT, padx=(0, 8))
status_label.pack(side=LEFT)

main_buttons.extend([
    btn_add_time,
    btn_remove_time,
    btn_start_scheduler,
    btn_stop_scheduler
])

days_frame = ttk.Frame(scheduler_card, style="Card.TFrame")
days_frame.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 10))

for i, (day, var) in enumerate(selected_days.items()):
    ttk.Checkbutton(days_frame, text=day, variable=var).grid(row=0, column=i, padx=3)

scheduler_card.columnconfigure(0, weight=0)
scheduler_card.columnconfigure(1, weight=0)
scheduler_card.columnconfigure(2, weight=0)
scheduler_card.columnconfigure(3, weight=1)
for i in range(10):
    scheduler_card.rowconfigure(i, weight=0)

startup_row = ttk.Frame(scheduler_card, style="Card.TFrame")
startup_row.grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 0))

ttk.Button(
    startup_row,
    text="Enable Startup",
    width=BTN_WIDTH,
    command=enable_run_on_startup
).pack(side=LEFT, padx=(0, 8))

ttk.Button(
    startup_row,
    text="Disable Startup",
    width=BTN_WIDTH,
    command=disable_run_on_startup
).pack(side=LEFT)

# Progress card
progress_card = ttk.Frame(backup_tab, style="Card.TFrame", padding=15)
progress_card.pack(fill=X, pady=10)



ttk.Label(
    progress_card,
    text="Backup Progress",
    background="#2d2d2d",
    foreground="#ffffff",
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 10))

progress_bar = ttk.Progressbar(
    progress_card,
    variable=progress_var,
    maximum=100
)
progress_bar.pack(fill=X, pady=(0, 8))

ttk.Label(
    progress_card,
    textvariable=status_var,
    background="#2d2d2d",
    foreground="#bdbdbd"
).pack(anchor="w")

# Action buttons
action_row = ttk.Frame(backup_tab)
action_row.pack(fill=X, pady=15)
action_row.columnconfigure(0, weight=1)
action_row.columnconfigure(1, weight=1)

btn_view_log = ttk.Button(action_row, text="View Backup Log", command=view_backup_log)
btn_view_log.grid(row=0, column=0, sticky="w")

btn_start_backup = ttk.Button(
    action_row,
    text="Start Backup",
    command=start_backup,
    style="Accent.TButton"
)
btn_start_backup.grid(row=0, column=1, sticky="e")

main_buttons.extend([
    btn_view_log,
    btn_start_backup
])

load_app_settings()
update_backup_summary()
root.protocol("WM_DELETE_WINDOW", on_app_close)

setup_tray_icon()
check_scheduled_backups()

_card.pack(fill=X, pady=10)



ttk.Label(
    progress_card,
    text="Backup Progress",
    background="#2d2d2d",
    foreground="#ffffff",
    font=("Segoe UI", 12, "bold")
).pack(anchor="w", pady=(0, 10))

progress_bar = ttk.Progressbar(
    progress_card,
    variable=progress_var,
    maximum=100
)
progress_bar.pack(fill=X, pady=(0, 8))

ttk.Label(
    progress_card,
    textvariable=status_var,
    background="#2d2d2d",
    foreground="#bdbdbd"
).pack(anchor="w")

# Action buttons
action_row = ttk.Frame(backup_tab)
action_row.pack(fill=X, pady=15)
action_row.columnconfigure(0, weight=1)
action_row.columnconfigure(1, weight=1)

btn_view_log = ttk.Button(action_row, text="View Backup Log", command=view_backup_log)
btn_view_log.grid(row=0, column=0, sticky="w")

btn_start_backup = ttk.Button(
    action_row,
    text="Start Backup",
    command=start_backup,
    style="Accent.TButton"
)
btn_start_backup.grid(row=0, column=1, sticky="e")

main_buttons.extend([
    btn_view_log,
    btn_start_backup
])

load_app_settings()
update_backup_summary()
root.protocol("WM_DELETE_WINDOW", on_app_close)

setup_tray_icon()
check_scheduled_backups()

root.mainloop()