import os
import sys
import zipfile
import shutil
import glob

def restore_latest_backup():
    print("--- Restore Backup Utility ---")

    root_dir = os.getcwd()
    backup_dir = os.path.join(root_dir, 'backups')

    if not os.path.exists(backup_dir):
        print(f"Error: Backup directory not found at {backup_dir}")
        return

    zips = glob.glob(os.path.join(backup_dir, "*.zip"))
    if not zips:
        print("Error: No backup zip files found.")
        return

    # Sort by time (newest first)
    zips.sort(key=os.path.getmtime, reverse=True)

    print("Available Backups:")
    for i, z in enumerate(zips[:5]):
        print(f"[{i+1}] {os.path.basename(z)}")

    choice = input("\nSelect backup to restore (1-5) or 'q' to quit: ").strip()
    if choice.lower() == 'q':
        return

    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(zips):
            print("Invalid selection.")
            return

        target_zip = zips[idx]
        print(f"\nRestoring from {os.path.basename(target_zip)}...")

        # 1. Clean existing config/work to avoid zombie files
        config_dir = os.path.join(root_dir, 'config')
        work_dir = os.path.join(root_dir, 'work')

        if os.path.exists(config_dir):
            try:
                shutil.rmtree(config_dir)
                print(f" -> Cleared {config_dir}")
            except Exception as e:
                print(f"Warning: Could not clear config dir: {e}")

        if os.path.exists(work_dir):
            try:
                shutil.rmtree(work_dir)
                print(f" -> Cleared {work_dir}")
            except Exception as e:
                print(f"Warning: Could not clear work dir: {e}")

        # 2. Extract
        with zipfile.ZipFile(target_zip, 'r') as zf:
            zf.extractall(root_dir)

        print("\nSUCCESS: Backup restored.")
        print("Please restart the application.")

    except ValueError:
        print("Invalid input.")
    except Exception as e:
        print(f"\nCRITICAL ERROR during restore: {e}")

if __name__ == "__main__":
    restore_latest_backup()
