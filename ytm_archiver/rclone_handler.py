import os
import subprocess
import logging
import time
from typing import Optional, Tuple

class RcloneHandler:
    def __init__(self, remote_name: str, remote_path: str = ""):
        """
        Initialize RcloneHandler with remote configuration.
        
        Args:
            remote_name: Name of the rclone remote (must be pre-configured in rclone)
            remote_path: Base path on the remote storage (e.g., 'Mega:/YouTubeBackups/ChannelName')
        """
        self.remote_name = remote_name
        self.remote_path = remote_path.rstrip('/')
        self.logger = logging.getLogger(__name__)
        self._verify_rclone_installation()

    def _verify_rclone_installation(self) -> None:
        """Verify that rclone is installed and accessible."""
        try:
            result = subprocess.run(
                ['rclone', 'version'],
                capture_output=True,
                text=True,
                check=True
            )
            self.logger.debug(f"Rclone version: {result.stdout.split('\n')[0] if result.stdout else 'Unknown'}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            self.logger.error("Rclone is not installed or not in PATH. Please install rclone and ensure it's accessible.")
            raise RuntimeError("Rclone is not installed or not in PATH") from e

    def _run_rclone_command(self, command: list, retries: int = 3) -> Tuple[bool, str]:
        """
        Execute an rclone command with retry logic.
        
        Args:
            command: List of command arguments to pass to rclone
            retries: Number of retry attempts
            
        Returns:
            Tuple of (success, message)
        """
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                self.logger.debug(f"Executing: rclone {' '.join(command)}")
                result = subprocess.run(
                    ['rclone'] + command,
                    capture_output=True,
                    text=True,
                    check=True
                )
                return True, result.stdout.strip()
            except subprocess.CalledProcessError as e:
                last_error = e
                error_msg = f"Rclone command failed (attempt {attempt}/{retries}): {e.stderr.strip()}"
                self.logger.warning(error_msg)
                if attempt < retries:
                    # Exponential backoff
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)
        
        error_msg = f"Rclone command failed after {retries} attempts: {last_error.stderr.strip() if last_error else 'Unknown error'}"
        self.logger.error(error_msg)
        return False, error_msg

    def _ensure_remote_path_exists(self) -> bool:
        """Ensure the remote path exists, create it if necessary."""
        if not self.remote_path:
            return True
            
        # Check if path exists
        check_cmd = ['lsd', f"{self.remote_name}:"]
        success, output = self._run_rclone_command(check_cmd)
        if not success:
            return False
            
        # Extract the base directory from remote_path
        base_path = self.remote_path.split(':', 1)[-1].lstrip('/')
        
        # Create the directory structure if it doesn't exist
        mkdir_cmd = ['mkdir', '--parents', f"{self.remote_name}:{base_path}"]
        success, _ = self._run_rclone_command(mkdir_cmd)
        return success

    def upload_file(self, local_path: str, remote_subpath: str = "") -> bool:
        """
        Upload a file to the remote storage using rclone.
        
        Args:
            local_path: Path to the local file to upload
            remote_subpath: Optional subpath within the remote path
            
        Returns:
            bool: True if upload was successful, False otherwise
        """
        if not os.path.isfile(local_path):
            self.logger.error(f"Local file not found: {local_path}")
            return False
            
        # Ensure the remote path exists
        if not self._ensure_remote_path_exists():
            self.logger.error(f"Failed to ensure remote path exists: {self.remote_path}")
            return False
            
        # Build the remote destination path
        remote_dest = f"{self.remote_name}:"
        if self.remote_path:
            remote_dest += self.remote_path.lstrip(':')
        if remote_subpath:
            remote_dest += '/' + remote_subpath.lstrip('/')
            
        # Add the filename to the remote path
        remote_dest = remote_dest.rstrip('/') + '/' + os.path.basename(local_path)
        
        self.logger.info(f"Uploading {local_path} to {remote_dest}")
        
        # Use rclone copy to upload the file
        upload_cmd = [
            'copy',
            '--progress',
            '--transfers', '1',
            '--retries', '3',
            '--low-level-retries', '3',
            '--stats', '5s',
            local_path,
            os.path.dirname(remote_dest)
        ]
        
        success, message = self._run_rclone_command(upload_cmd)
        if success:
            self.logger.info(f"Successfully uploaded {local_path} to {remote_dest}")
        else:
            self.logger.error(f"Failed to upload {local_path}: {message}")
            
        return success

    def check_connection(self) -> bool:
        """Check if the rclone remote is accessible."""
        self.logger.info(f"Checking connection to rclone remote: {self.remote_name}")
        success, _ = self._run_rclone_command(['lsd', f"{self.remote_name}:"])
        if success:
            self.logger.info("Successfully connected to rclone remote")
        else:
            self.logger.error("Failed to connect to rclone remote")
        return success
