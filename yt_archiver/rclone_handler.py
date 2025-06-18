"""
Rclone handler module for uploading files to cloud storage using rclone.

This module provides a high-level interface for interacting with rclone to upload files
to various cloud storage providers. It supports parallel uploads, progress tracking,
and robust error handling with retries.
"""

import os
import re
import json
import time
import random
import logging
import subprocess
import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    NamedTuple,
    Optional,
    Tuple,
    Union,
    cast,
    overload,
)
from concurrent.futures import ThreadPoolExecutor, as_completed, Future

# Type aliases
PathLike = Union[str, os.PathLike]
RcloneCommand = List[str]  # Type for rclone command arguments
RcloneResult = Tuple[bool, str]  # (success, message) tuple for command results

class RcloneError(Exception):
    """Base exception for rclone-related errors."""
    pass

class RcloneConfigError(RcloneError):
    """Raised when there's an issue with rclone configuration."""
    pass

class RcloneCommandError(RcloneError):
    """Raised when an rclone command fails."""
    def __init__(self, command: RcloneCommand, returncode: int, stderr: str):
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"Rclone command failed with code {returncode}: {shlex.join(command)}\n{stderr}"
        )

class UploadStatus(Enum):
    """Status of an upload operation."""
    PENDING = auto()
    DOWNLOADING = auto()
    UPLOADING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()

class RcloneStats(NamedTuple):
    """Statistics for an rclone operation."""
    bytes_transferred: int = 0
    bytes_total: int = 0
    transfer_speed: float = 0.0  # bytes per second
    transfer_percent: float = 0.0
    eta: Optional[float] = None  # seconds
    errors: int = 0
    checks: int = 0
    renames: int = 0
    retry_errors: int = 0
    transfer_time: float = 0.0  # seconds

# Default configuration constants
DEFAULT_RETRIES: int = 3
DEFAULT_TRANSFERS: int = 4
DEFAULT_CHECKERS: int = 8
DEFAULT_STATS: str = "5s"
MAX_UPLOAD_ATTEMPTS: int = 5
MAX_WORKERS: int = 8  # Maximum number of parallel uploads
MIN_RETRY_DELAY: float = 1.0  # Minimum delay between retries in seconds
MAX_RETRY_DELAY: float = 60.0  # Maximum delay between retries in seconds

# Rclone command constants
RCLONE_CMD: str = "rclone"
RCLONE_COPY: str = "copy"
RCLONE_MOVE: str = "move"
RCLONE_MKDIR: str = "mkdir"
RCLONE_LS: str = "ls"
RCLONE_LSJSON: str = "lsjson"
RCLONE_RMDIR: str = "rmdir"
RCLONE_DELETE: str = "delete"
RCLONE_SIZE: str = "size"

@dataclass
class UploadProgress:
    """Tracks upload progress for a file.
    
    Attributes:
        filename: Name of the file being uploaded.
        size: Total size of the file in bytes.
        transferred: Number of bytes transferred so far.
        speed: Current transfer speed in bytes per second.
        percent: Transfer completion percentage (0-100).
        eta: Estimated time remaining in seconds, or None if not available.
        done: Whether the upload has completed.
        error: Error message if the upload failed, None otherwise.
        status: Current status of the upload.
    """
    filename: str
    size: int = 0
    transferred: int = 0
    speed: float = 0.0  # bytes per second
    percent: float = 0.0
    eta: Optional[float] = None
    done: bool = False
    error: Optional[str] = None
    status: UploadStatus = UploadStatus.PENDING
    
    def update(self, 
              transferred: Optional[int] = None,
              size: Optional[int] = None,
              speed: Optional[float] = None,
              percent: Optional[float] = None,
              eta: Optional[float] = None,
              done: Optional[bool] = None,
              error: Optional[str] = None,
              status: Optional[UploadStatus] = None) -> None:
        """Update the progress with new values.
        
        Args:
            transferred: Number of bytes transferred.
            size: Total size of the file.
            speed: Current transfer speed in bytes/second.
            percent: Transfer completion percentage.
            eta: Estimated time remaining in seconds.
            done: Whether the transfer is complete.
            error: Error message if the transfer failed.
            status: Current status of the upload.
        """
        if transferred is not None:
            self.transferred = transferred
        if size is not None:
            self.size = size
        if speed is not None:
            self.speed = speed
        if percent is not None:
            self.percent = percent
        if eta is not None:
            self.eta = eta
        if done is not None:
            self.done = done
        if error is not None:
            self.error = error
            self.status = UploadStatus.FAILED
        if status is not None:
            self.status = status
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the progress to a dictionary.
        
        Returns:
            Dictionary containing the progress information.
        """
        return {
            'filename': self.filename,
            'size': self.size,
            'transferred': self.transferred,
            'speed': self.speed,
            'percent': self.percent,
            'eta': self.eta,
            'done': self.done,
            'error': self.error,
            'status': self.status.name
        }

@dataclass
class UploadResult:
    """Result of a file upload operation.
    
    Attributes:
        success: Whether the upload was successful.
        local_path: Path to the local file that was uploaded.
        remote_path: Destination path on the remote storage.
        message: Status message about the upload.
        error: Exception if the upload failed, None otherwise.
        bytes_uploaded: Number of bytes uploaded.
        speed: Average upload speed in bytes per second.
        duration: Total duration of the upload in seconds.
        retries: Number of retry attempts made.
        checksum_matched: Whether the remote file's checksum matches the local file.
    """
    success: bool
    local_path: str
    remote_path: str
    message: str = ""
    error: Optional[Exception] = None
    bytes_uploaded: int = 0
    speed: float = 0.0  # bytes per second
    duration: float = 0.0  # seconds
    retries: int = 0
    checksum_matched: Optional[bool] = None
    
    @classmethod
    def from_exception(
        cls, 
        local_path: str, 
        remote_path: str, 
        error: Exception,
        retries: int = 0
    ) -> 'UploadResult':
        """Create a failed UploadResult from an exception.
        
        Args:
            local_path: Path to the local file that failed to upload.
            remote_path: Intended destination path on the remote storage.
            error: Exception that caused the failure.
            retries: Number of retry attempts made.
            
        Returns:
            A new UploadResult instance with success=False.
        """
        return cls(
            success=False,
            local_path=local_path,
            remote_path=remote_path,
            message=f"Upload failed: {str(error)}",
            error=error,
            retries=retries
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the result to a dictionary.
        
        Returns:
            Dictionary containing the result information.
        """
        return {
            'success': self.success,
            'local_path': self.local_path,
            'remote_path': self.remote_path,
            'message': self.message,
            'error': str(self.error) if self.error else None,
            'bytes_uploaded': self.bytes_uploaded,
            'speed': self.speed,
            'duration': self.duration,
            'retries': self.retries,
            'checksum_matched': self.checksum_matched
        }

class RcloneHandler:
    """Handler for rclone operations with support for parallel uploads and retries.
    
    This class provides a high-level interface to rclone with features like:
    - Parallel file uploads with configurable concurrency
    - Automatic retries with exponential backoff
    - Progress tracking and callbacks
    - Remote directory management
    - File existence and integrity checks
    """
    
    def __init__(
        self, 
        remote_name: str, 
        remote_path: str = "", 
        max_workers: int = DEFAULT_TRANSFERS,
        retries: int = DEFAULT_RETRIES,
        checkers: int = DEFAULT_CHECKERS,
        stats_interval: str = DEFAULT_STATS
    ) -> None:
        """Initialize RcloneHandler with remote configuration.
        
        Args:
            remote_name: Name of the rclone remote (must be pre-configured in rclone).
            remote_path: Base path on the remote storage (e.g., 'YouTubeBackups/ChannelName').
            max_workers: Maximum number of parallel transfers (default: 4).
            retries: Number of retry attempts for failed operations.
            checkers: Number of checkers to use in rclone (affects local disk usage).
            stats_interval: How often to print stats (e.g., '5s', '1m').
            
        Raises:
            RcloneConfigError: If rclone is not installed or not in PATH.
        """
        self.remote_name = remote_name.strip()
        self.remote_path = remote_path.strip().rstrip('/')
        self.max_workers = max(1, min(max_workers, MAX_WORKERS))
        self.retries = max(0, retries)
        self.checkers = max(1, checkers)
        self.stats_interval = stats_interval
        
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._stop_event = False
        self._verify_rclone_installation()
        
        # Initialize thread pool for parallel uploads
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix='rclone_uploader_'
        )
        
        self.logger.info(
            "Initialized RcloneHandler with remote '%s:%s' and %d workers",
            self.remote_name,
            self.remote_path or '.',
            self.max_workers
        )
        
    def __del__(self) -> None:
        """Clean up resources.
        
        Note:
            This is called when the object is garbage collected.
            It ensures that the thread pool is properly shut down.
        """
        try:
            self.shutdown()
        except Exception as e:
            self.logger.warning("Error during RcloneHandler cleanup: %s", e, exc_info=True)
    
    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the uploader and optionally wait for pending uploads to complete.
        
        Args:
            wait: If True, wait for all pending uploads to complete before returning.
                  If False, attempt to cancel any pending futures.
        """
        self._stop_event = True
        if hasattr(self, '_executor') and self._executor:
            self.logger.debug("Shutting down thread pool (wait=%s)", wait)
            self._executor.shutdown(wait=wait, cancel_futures=not wait)
            self.logger.info("Thread pool shutdown complete")

    def _verify_rclone_installation(self) -> None:
        """Verify that rclone is installed and accessible.
        
        This method checks if rclone is installed and can be executed.
        
        Raises:
            RcloneConfigError: If rclone is not installed or not in PATH,
                            or if the rclone version check fails.
        """
        try:
            cmd = [RCLONE_CMD, "version"]
            self.logger.debug("Checking rclone installation: %s", " ".join(cmd))
            
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            
            # Extract version from output (e.g., "rclone v1.58.1")
            version_match = re.search(r'rclone\s+v(\d+\.\d+\.\d+)', result.stdout)
            if version_match:
                self.logger.info("Found rclone version: %s", version_match.group(1))
            else:
                self.logger.warning("Could not determine rclone version from output")
                
        except subprocess.CalledProcessError as e:
            error_msg = f"rclone command failed with code {e.returncode}: {e.stderr}"
            self.logger.error(error_msg)
            raise RcloneConfigError(error_msg) from e
        except FileNotFoundError as e:
            error_msg = "rclone is not installed or not in PATH"
            self.logger.error(error_msg)
            raise RcloneConfigError(error_msg) from e
        except Exception as e:
            error_msg = f"Unexpected error checking rclone installation: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise RcloneConfigError(error_msg) from e
            
    def _run_rclone_command(
        self, 
        command: RcloneCommand, 
        retries: int = 0,
        capture_output: bool = True,
        check: bool = True,
        **kwargs: Any
    ) -> Tuple[bool, str]:
        """Execute an rclone command with retry logic and error handling.
        
        This is a low-level method to run rclone commands with automatic retries
        and consistent error handling.
        
        Args:
            command: List of command arguments to pass to rclone (without the 'rclone' prefix).
            retries: Number of retry attempts for transient failures.
            capture_output: Whether to capture and return command output.
            check: If True, raise RcloneCommandError on non-zero exit code.
            **kwargs: Additional arguments to pass to subprocess.run().
            
        Returns:
            A tuple of (success, output) where:
                - success: Boolean indicating if the command succeeded
                - output: Command output as string if capture_output=True, else empty string
                
        Raises:
            RcloneCommandError: If check=True and the command fails after all retries.
            ValueError: If the command is empty or invalid.
        """
        if not command or not isinstance(command, list):
            raise ValueError("Command must be a non-empty list of arguments")
            
        # Ensure we're using the correct rclone command
        if command[0] == RCLONE_CMD:
            cmd = command.copy()
        else:
            cmd = [RCLONE_CMD] + command
            
        self.logger.debug("Executing rclone command: %s", " ".join(cmd))
        
        last_error: Optional[Exception] = None
        attempt = 0
        
        while attempt <= retries:
            if self._stop_event:
                self.logger.debug("Command execution cancelled by shutdown")
                return False, "Operation cancelled"
                
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE if capture_output else None,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=check,
                    **kwargs
                )
                
                # If we get here, command succeeded
                output = result.stdout.strip() if capture_output and result.stdout else ""
                if output:
                    self.logger.debug("Command output: %s", output)
                return True, output
                
            except subprocess.CalledProcessError as e:
                last_error = RcloneCommandError(
                    command=cmd,
                    returncode=e.returncode,
                    stderr=e.stderr.strip() if e.stderr else ""
                )
                
                # Log the error with appropriate level
                log_level = logging.WARNING if attempt < retries else logging.ERROR
                self.logger.log(
                    log_level,
                    "Command failed (attempt %d/%d): %s",
                    attempt + 1,
                    retries + 1,
                    str(last_error)
                )
                
                # Don't retry on certain errors
                if e.returncode in (1, 2):  # Syntax or usage error
                    self.logger.error("Not retrying due to command error")
                    break
                    
                # Calculate exponential backoff with jitter
                if attempt < retries:
                    base_delay = min(MIN_RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                    jitter = random.uniform(0, base_delay * 0.1)  # Add up to 10% jitter
                    wait_time = base_delay + jitter
                    
                    self.logger.debug("Retrying in %.1f seconds...", wait_time)
                    time.sleep(wait_time)
                
                attempt += 1
                
            except Exception as e:
                last_error = e
                self.logger.error(
                    "Unexpected error executing command: %s",
                    str(e),
                    exc_info=True
                )
                break
        
        # If we get here, all retries failed or we hit a non-retryable error
        if check:
            raise RcloneCommandError(
                command=cmd,
                returncode=getattr(last_error, 'returncode', -1),
                stderr=str(last_error) if last_error else "Unknown error"
            )
            
        return False, str(last_error) if last_error else "Unknown error"

    def _ensure_remote_path_exists(self, path: str = "") -> bool:
        """Ensure the remote path exists, create it if necessary.
        
        This method checks if the specified remote path exists and creates it
        if it doesn't. It handles nested paths and is idempotent.
        
        Args:
            path: Optional subpath to ensure exists within the configured remote path.
                 Should not include the remote name or base path.
                 
        Returns:
            bool: True if the path exists or was created successfully, False otherwise.
            
        Example:
            If remote_name='mega' and remote_path='backups/youtube', then:
            - _ensure_remote_path_exists() ensures 'mega:backups/youtube' exists
            - _ensure_remote_path_exists('channel1') ensures 'mega:backups/youtube/channel1' exists
        """
        try:
            # Build the full remote path
            remote_parts = []
            if self.remote_path:
                remote_parts.append(self.remote_path.strip('/'))
            if path:
                remote_parts.append(path.strip('/'))
                
            remote_path = f"{self.remote_name}:"
            if remote_parts:
                remote_path += '/'.join(remote_parts)
            
            self.logger.debug("Ensuring remote path exists: %s", remote_path)
            
            # Use mkdir -p to create path if it doesn't exist
            mkdir_cmd = [RCLONE_MKDIR, '--parents', remote_path]
            success, _ = self._run_rclone_command(
                mkdir_cmd,
                retries=2,  # Don't retry too many times for directory creation
                check=False
            )
            
            if not success:
                self.logger.warning("Failed to create remote path: %s", remote_path)
                return False
                
            self.logger.debug("Verified/created remote path: %s", remote_path)
            return True
            
        except Exception as e:
            self.logger.error(
                "Error ensuring remote path '%s' exists: %s",
                path,
                str(e),
                exc_info=True
            )
            return False
            
    def _get_remote_file_info(self, remote_path: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a remote file or directory.
        
        This method uses 'rclone lsjson' to retrieve metadata about a remote file
        or directory, including size, modification time, and other attributes.
        
        Args:
            remote_path: Path to the remote file or directory, including the remote name.
                       Example: 'mega:backups/file.txt' or 'mega:backups/dir'
                       
        Returns:
            A dictionary containing file/directory metadata, or None if the path
            doesn't exist or an error occurred.
            
        Example:
            {
                'Path': 'file.txt',
                'Name': 'file.txt',
                'Size': 12345,
                'MimeType': 'text/plain',
                'ModTime': '2023-01-01T12:00:00Z',
                'IsDir': False,
                'Tier': 'hot',
                'ID': 'file123',
                ...
            }
        """
        if not remote_path or not isinstance(remote_path, str):
            self.logger.error("Invalid remote path: %r", remote_path)
            return None
            
        try:
            # Ensure the path is properly formatted
            if ':' not in remote_path:
                remote_path = f"{self.remote_name}:{self.remote_path}/{remote_path}"
                
            self.logger.debug("Getting remote file info: %s", remote_path)
            
            # Use lsjson to get detailed file info
            ls_cmd = [RCLONE_LSJSON, '--files-only', '--no-modtime', '--no-mimetype', remote_path]
            success, output = self._run_rclone_command(
                ls_cmd,
                retries=2,  # Don't retry too many times for file checks
                check=False
            )
            
            if not success or not output:
                self.logger.debug("Remote path not found or empty: %s", remote_path)
                return None
                
            try:
                result = json.loads(output)
                # If we got a list, return the first item (for single file)
                if isinstance(result, list):
                    return result[0] if result else None
                return result
                
            except (json.JSONDecodeError, IndexError) as e:
                self.logger.error(
                    "Failed to parse rclone lsjson output: %s - Output: %s",
                    str(e),
                    output[:200]  # Log first 200 chars to avoid huge logs
                )
                return None
                
        except Exception as e:
            self.logger.error(
                "Error getting remote file info for '%s': %s",
                remote_path,
                str(e),
                exc_info=True
            )
            return None
            
    def upload_file(
        self, 
        local_path: Union[str, os.PathLike], 
        remote_subpath: str = "",
        progress_callback: Optional[Callable[[str, UploadProgress], None]] = None,
        overwrite: bool = True,
        verify: bool = True,
        retries: Optional[int] = None
    ) -> UploadResult:
        """Upload a single file to the remote storage with progress tracking.
        
        This method handles the complete upload process including:
        - Input validation and path resolution
        - Progress tracking and callbacks
        - Retry logic with exponential backoff
        - Optional file verification after upload
        - Clean error handling and reporting
        
        Args:
            local_path: Path to the local file to upload. Can be a string or PathLike object.
            remote_subpath: Optional subdirectory within the remote path to upload to.
                          Will be created if it doesn't exist.
            progress_callback: Optional callback function that receives UploadProgress updates.
            overwrite: If False, skip upload if file already exists with same size.
            verify: If True, verify the uploaded file size matches the local file.
                   Note: Full checksum verification requires additional API calls.
            retries: Number of retry attempts for transient failures. If None, uses instance default.
                   
        Returns:
            UploadResult object containing the result of the operation.
            
        Raises:
            FileNotFoundError: If the local file doesn't exist or is not accessible.
            PermissionError: If there are permission issues accessing the local file.
            RcloneError: For rclone-specific errors during the upload process.
            
        Example:
            >>> handler = RcloneHandler("mega", "backups")
            >>> result = handler.upload_file("video.mp4", "videos/2023")
            >>> if result.success:
            ...     print(f"Uploaded {result.local_path} to {result.remote_path}")
        """
        # Convert PathLike to string if needed
        local_path_str = os.fspath(local_path)
        retries = self.retries if retries is None else max(0, retries)
        
        # Input validation
        if not os.path.isfile(local_path_str):
            error_msg = f"Local file not found or not a file: {local_path_str}"
            self.logger.error(error_msg)
            raise FileNotFoundError(error_msg)
            
        if not os.access(local_path_str, os.R_OK):
            error_msg = f"No read permission for file: {local_path_str}"
            self.logger.error(error_msg)
            raise PermissionError(error_msg)
            
        try:
            file_size = os.path.getsize(local_path_str)
            file_name = os.path.basename(local_path_str)
            
            # Build remote destination path
            remote_parts = []
            if self.remote_path:
                remote_parts.append(self.remote_path.strip('/'))
            if remote_subpath:
                remote_parts.append(remote_subpath.strip('/'))
                
            remote_dir = f"{self.remote_name}:" + '/'.join(remote_parts)
            remote_file = f"{remote_dir}/{file_name}"
            
            # Ensure remote directory exists
            if not self._ensure_remote_path_exists(remote_subpath):
                error_msg = f"Failed to create remote directory: {remote_dir}"
                self.logger.error(error_msg)
                return UploadResult(
                    success=False,
                    local_path=local_path_str,
                    remote_path=remote_file,
                    message=error_msg,
                    error=RuntimeError(error_msg)
                )
            
            # Check if file exists and skip if overwrite is False
            if not overwrite:
                remote_info = self._get_remote_file_info(remote_file)
                if remote_info and remote_info.get('Size') == file_size:
                    self.logger.info(
                        "Skipping existing file (size matches): %s", 
                        remote_file
                    )
                    return UploadResult(
                        success=True,
                        local_path=local_path_str,
                        remote_path=remote_file,
                        message="File already exists with matching size",
                        bytes_uploaded=file_size,
                        checksum_matched=None  # We only checked size, not checksum
                    )
            
            # Set up progress tracking
            progress = UploadProgress(
                filename=file_name,
                size=file_size,
                status=UploadStatus.UPLOADING
            )
            
            if progress_callback:
                progress_callback(file_name, progress)
            
            self.logger.info(
                "Uploading %s (%.2f MB) to %s",
                file_name,
                file_size / (1024 * 1024),
                remote_file
            )
            
            # Build rclone command
            upload_cmd = [
                RCLONE_COPY,
                '--progress',
                '--stats', '1s',
                '--stats-one-line',
                '--transfers', '1',
                '--checkers', str(min(4, self.max_workers * 2)),
                '--retries', str(retries),
                '--low-level-retries', '3',
                '--contimeout', '60s',
                '--timeout', '5m',
                '--buffer-size', '32M',
                '--check-first',
                '--no-update-modtime',
                '--use-json-log',
            ]
            
            # Add source and destination
            upload_cmd.extend([local_path_str, remote_dir])
            
            # Execute the upload with retries
            last_error = None
            attempt = 0
            max_attempts = max(1, retries + 1)  # At least one attempt
            
            while attempt < max_attempts and not self._stop_event:
                try:
                    # Update progress for retry
                    if attempt > 0:
                        progress.update(
                            status=UploadStatus.UPLOADING,
                            message=f"Retry attempt {attempt + 1}/{max_attempts}"
                        )
                        if progress_callback:
                            progress_callback(file_name, progress)
                    
                    # Execute the upload
                    start_time = time.monotonic()
                    success, output = self._run_rclone_command(
                        upload_cmd,
                        retries=0,  # We handle retries in this method
                        capture_output=True,
                        check=False
                    )
                    
                    duration = time.monotonic() - start_time
                    
                    if success:
                        # Verify the upload if requested
                        checksum_matched = None
                        if verify:
                            try:
                                remote_info = self._get_remote_file_info(remote_file)
                                if remote_info:
                                    checksum_matched = (remote_info.get('Size') == file_size)
                                    if not checksum_matched:
                                        self.logger.warning(
                                            "Size mismatch after upload: local=%d, remote=%d",
                                            file_size,
                                            remote_info.get('Size', 0)
                                        )
                            except Exception as e:
                                self.logger.warning("Failed to verify upload: %s", str(e))
                        
                        # Calculate speed
                        speed = file_size / duration if duration > 0 else 0
                        
                        # Update progress
                        progress.update(
                            transferred=file_size,
                            percent=100.0,
                            speed=speed,
                            done=True,
                            status=UploadStatus.COMPLETED,
                            message="Upload completed successfully"
                        )
                        
                        self.logger.info(
                            "Successfully uploaded %s (%.2f MB at %.2f MB/s)",
                            file_name,
                            file_size / (1024 * 1024),
                            speed / (1024 * 1024) if speed > 0 else 0
                        )
                        
                        return UploadResult(
                            success=True,
                            local_path=local_path_str,
                            remote_path=remote_file,
                            message="Upload completed successfully",
                            bytes_uploaded=file_size,
                            speed=speed,
                            duration=duration,
                            checksum_matched=checksum_matched,
                            retries=attempt
                        )
                    
                    # If we get here, the upload failed
                    last_error = RcloneError(f"Upload failed: {output}")
                    
                except Exception as e:
                    last_error = RcloneError(f"Error during upload: {str(e)}") if not isinstance(e, RcloneError) else e
                    self.logger.error(
                        "Error during upload attempt %d/%d: %s",
                        attempt + 1,
                        max_attempts,
                        str(last_error),
                        exc_info=not isinstance(last_error, RcloneError)
                    )
                
                # Prepare for next attempt or final failure
                attempt += 1
                if attempt < max_attempts and not self._stop_event:
                    # Exponential backoff with jitter
                    delay = min(MIN_RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                    jitter = random.uniform(0, delay * 0.1)  # Add up to 10% jitter
                    sleep_time = delay + jitter
                    
                    self.logger.info(
                        "Retrying upload in %.1f seconds (attempt %d/%d)",
                        sleep_time,
                        attempt + 1,
                        max_attempts
                    )
                    
                    # Update progress
                    progress.update(
                        status=UploadStatus.PENDING,
                        message=f"Retrying in {sleep_time:.1f}s (attempt {attempt + 1}/{max_attempts})"
                    )
                    if progress_callback:
                        progress_callback(file_name, progress)
                    
                    time.sleep(sleep_time)
            
            # If we get here, all attempts failed or we were stopped
            if self._stop_event:
                error_msg = "Upload cancelled by shutdown"
                progress.update(
                    status=UploadStatus.CANCELLED,
                    done=True,
                    error=error_msg,
                    message=error_msg
                )
                last_error = RcloneError(error_msg)
            
            # Final progress update for failure
            progress.update(
                status=UploadStatus.FAILED,
                done=True,
                error=str(last_error) if last_error else "Unknown error",
                message=f"Upload failed after {attempt} attempt(s)"
            )
            if progress_callback:
                progress_callback(file_name, progress)
            
            return UploadResult.from_exception(
                local_path_str,
                remote_file,
                last_error or RcloneError("Unknown error"),
                retries=attempt - 1
            )
            
        except Exception as e:
            error = RcloneError(f"Unexpected error during upload: {str(e)}") if not isinstance(e, RcloneError) else e
            self.logger.error(
                "Unexpected error uploading %s: %s",
                local_path_str,
                str(error),
                exc_info=not isinstance(error, RcloneError)
            )
            
            # Update progress with error if we can
            if 'progress' in locals():
                progress.update(
                    status=UploadStatus.FAILED,
                    done=True,
                    error=str(error),
                    message="Upload failed due to unexpected error"
                )
                if progress_callback:
                    progress_callback(file_name, progress)
            
            return UploadResult.from_exception(
                local_path_str,
                remote_file if 'remote_file' in locals() else "",
                error
            )
    
    def upload_files(
        self, 
        file_paths: List[Union[str, os.PathLike]], 
        remote_subpath: str = "",
        progress_callback: Optional[Callable[[str, UploadProgress], None]] = None,
        **upload_kwargs: Any
    ) -> List[UploadResult]:
        """Upload multiple files in parallel to the remote storage.
        
        This method handles parallel uploads with proper error handling and progress reporting.
        
        Args:
            file_paths: List of local file paths to upload.
            remote_subpath: Optional subdirectory within the remote path to upload to.
            progress_callback: Optional callback function that receives progress updates.
            **upload_kwargs: Additional keyword arguments to pass to upload_file().
            
        Returns:
            List of UploadResult objects, one per file.
        """
        if not file_paths:
            return []
            
        results = []
        
        # Use ThreadPoolExecutor for parallel uploads
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Create a future for each file upload
            future_to_path = {
                executor.submit(self.upload_file, path, remote_subpath): path
                for path in file_paths
            }
            
            # Process completed uploads as they finish
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    result = future.result()
                    results.append(result)
                    
                    # Call progress callback if provided
                    if progress_callback and hasattr(result, 'to_dict'):
                        progress_callback(path, result)
                        
                except Exception as e:
                    error_msg = f"Error uploading {path}: {str(e)}"
                    self.logger.error(error_msg, exc_info=True)
                    results.append(UploadResult(
                        success=False,
                        local_path=path,
                        remote_path="",
                        message=error_msg,
                        error=e
                    ))
        
        return results

    def check_connection(self) -> bool:
        """Check if the rclone remote is accessible."""
        self.logger.info(f"Checking connection to rclone remote: {self.remote_name}")
        success, _ = self._run_rclone_command(['lsd', f"{self.remote_name}:"])
        if success:
            self.logger.info("Successfully connected to rclone remote")
        else:
            self.logger.error("Failed to connect to rclone remote")
        return success
