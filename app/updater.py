import os
import subprocess
import shutil
import datetime
import zipfile
import sys

class GitUpdater:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.backup_dir = os.path.join(root_dir, 'backups')
        self.config_dir = os.path.join(root_dir, 'config')
        self.work_dir = os.path.join(root_dir, 'work')

    def run_git_cmd(self, args):
        """Runs a git command and returns output/error."""
        try:
            result = subprocess.run(
                ['git'] + args,
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"Git command failed: {e.cmd}")
            print(f"Stderr: {e.stderr}")
            raise Exception(f"Git error: {e.stderr.strip()}")
        except FileNotFoundError:
             raise Exception("Git not found. Please install Git.")

    def check_for_updates(self):
        """
        Fetches remote and checks if local is behind.
        Returns: (has_update: bool, message: str)
        """
        try:
            # 1. Fetch remote info
            self.run_git_cmd(['fetch', 'origin'])

            # 2. Get hash of HEAD and origin/main
            local_hash = self.run_git_cmd(['rev-parse', 'HEAD'])
            remote_hash = self.run_git_cmd(['rev-parse', 'origin/main'])

            if local_hash != remote_hash:
                # Check if we are behind
                # "git rev-list --count HEAD..origin/main" returns number of commits we are behind
                behind_count = self.run_git_cmd(['rev-list', '--count', 'HEAD..origin/main'])
                if int(behind_count) > 0:
                    return True, f"New version available ({behind_count} commits behind)."
                else:
                     return False, "Local version is ahead or divergent."

            return False, "Up to date."

        except Exception as e:
            return False, f"Update check failed: {str(e)}"

    def get_changelog(self):
        """Returns list of commits between HEAD and origin/main."""
        try:
            # Format: hash - date - message
            log_output = self.run_git_cmd([
                'log',
                'HEAD..origin/main',
                '--pretty=format:%h|%cd|%s',
                '--date=short'
            ])

            commits = []
            if log_output:
                for line in log_output.split('\n'):
                    parts = line.split('|')
                    if len(parts) >= 3:
                        commits.append({
                            'hash': parts[0],
                            'date': parts[1],
                            'message': parts[2]
                        })
            return commits
        except Exception as e:
            return [{"message": f"Could not retrieve changelog: {e}"}]

    def create_backup(self):
        """Zips config and work directories."""
        if not os.path.exists(self.backup_dir):
            os.makedirs(self.backup_dir)

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_name = f"backup_{timestamp}.zip"
        backup_path = os.path.join(self.backup_dir, backup_name)

        try:
            with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add Config
                if os.path.exists(self.config_dir):
                    for root, dirs, files in os.walk(self.config_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, self.root_dir)
                            zipf.write(file_path, arcname)

                # Add Work
                if os.path.exists(self.work_dir):
                    for root, dirs, files in os.walk(self.work_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, self.root_dir)
                            zipf.write(file_path, arcname)

            return True, backup_path
        except Exception as e:
            return False, str(e)

    def perform_update(self):
        """Pulls changes from origin/main."""
        try:
            # Assumes 'main' is the branch
            self.run_git_cmd(['pull', 'origin', 'main'])
            return True, "Update successful."
        except Exception as e:
            return False, f"Update failed: {str(e)}"
