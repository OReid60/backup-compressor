Download Link Below
https://github.com/OReid60/backup-compressor/releases/download/2.5.1/Backup.Compressor.Setup.2.5.1.exe

Backup Compressor
Version: 2.5.1

========================================
OVERVIEW
========================================
Backup Compressor is a Windows-based backup utility that allows users to:
- Compress files and folders into ZIP, 7Z, or RAR formats
- Schedule automated backups
- Upload backups to Google Drive
- Perform SQL Server database backups
- Monitor backup activity via logs and dashboard

========================================
CORE FEATURES
========================================

[1] FILE & FOLDER BACKUP
- Add multiple files and folders
- Supports recursive folder backup
- Real-time file count and size summary

[2] MULTIPLE COMPRESSION FORMATS
- ZIP (built-in)
- 7Z (requires 7-Zip installed)
- RAR (requires WinRAR installed)

[3] BACKUP PROFILES
- Save backup configurations to JSON
- Load profiles instantly
- Stores:
  - Selected items
  - Destination
  - Format
  - Schedule times

[4] PROGRESS TRACKING
- Live compression progress bar
- Status updates during backup
- Background-safe UI updates via queue system

[5] LOCKED FILE DETECTION
- Prevents backup if files are in use
- Displays preview of locked files
- Uses Windows API (ctypes)

[6] SCHEDULER (LOCAL)
- Time-based scheduling
- Day-of-week selection
- Runs in background
- Tray icon status indicator

[7] CLOUD BACKUP (GOOGLE DRIVE)
- OAuth-based authentication
- Uploads backups automatically
- Organized folder structure:
  Format → Date → Time
- Supports scheduled cloud backups

[8] SQL SERVER BACKUP
- Discover SQL Servers
- Select multiple databases
- Scheduled SQL backup support
- Outputs .BAK files

[9] LOGGING SYSTEM
- Text log file
- JSON event logs
- Tracks:
  - Success
  - Failures
  - Scheduler events

[10] DASHBOARD
- Displays:
  - Last backup
  - Progress
  - Scheduler status
  - Cloud status
  - Storage usage
- Backup history viewer

[11] SYSTEM TRAY INTEGRATION
- Minimize to tray
- Notifications on completion
- Scheduler status indicator (color-coded)

[12] AUTO STARTUP
- Option to run on Windows startup
- Uses Windows startup shortcut

========================================
FOLDER STRUCTURE
========================================

App Data Location:
%APPDATA%\Backup Compressor\

Contains:
- app_settings.json
- logs\
  - backup_log.txt
  - backup_events.jsonl
- token.json (Google Drive)

========================================
DEPENDENCIES
========================================

Required:
- Python 3.x
- tkinter
- pystray
- pillow (PIL)

Optional:
- 7-Zip → for .7z support
- WinRAR → for .rar support
- SQLCMD → for SQL backups

Google Drive:
- credentials.json required

========================================
HOW TO USE
========================================

1. Add files/folders
2. Choose destination
3. Select format
4. Click "Start Backup"

Optional:
- Save profile
- Configure scheduler
- Enable cloud backup
- Enable SQL backup

========================================
NOTES
========================================

- Ensure 7-Zip or WinRAR is installed for those formats
- Google Drive requires first-time authentication
- SQL backups require SQL Server access permissions

========================================
VERSION
========================================
Current Version: 2.5.1
