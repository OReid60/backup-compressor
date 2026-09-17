# Backup Compressor 3.1.1

A Windows backup utility for compressing selected files and folders into ZIP, 7Z, or RAR archives.

## Features

- Backup files and folders
- ZIP, 7Z, and RAR support
- Backup profiles
- Scheduler
- System tray support
- AppData settings and logs
- Windows installer support
- Dark interface with optional Cloud Backup navigation under Settings
- Backup logs under Settings > Logs
- Scheduled backups grouped into YYYY-MM folders with automatic year rollover

## Download

Go to the Releases section and download the latest installer.
The latest installer is also included in installer_output/Backup Compressor Setup 3.1.1.exe.

## Development

Install Python dependencies from requirements.txt. Google Drive integration requires
your own desktop OAuth credentials.json in the project folder; credentials and
local settings are not tracked. Run python main.py --test-mode for isolated
settings and logs. Manual backups and uploads in testing mode are real operations.

Build the application with PyInstaller using Backup Compressor.spec, then compile
installer.iss with Inno Setup 6. The build uses app_icon.ico.

## Notes

RAR support requires WinRAR/Rar.exe to be installed separately.

## Disclaimer

Always verify your backups before deleting original files.
