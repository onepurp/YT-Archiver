from mega import Mega
import logging
import os
import time

class MegaHandler:
    def __init__(self, email: str, password: str, target_folder: str):
        self.email = email
        self.password = password
        self.target_folder = target_folder
        self.logger = logging.getLogger(__name__)
        self.mega = None
        self.folder_node = None
        self._login_and_prepare_folder()

    def _login_and_prepare_folder(self):
        retry = 0
        while retry < 5:
            try:
                self.mega = Mega().login(self.email, self.password)
                self.folder_node = self._get_or_create_folder(self.target_folder)
                return
            except Exception as e:
                retry += 1
                self.logger.warning(f"Mega.nz login/folder creation failed, retry {retry}: {e}")
                time.sleep(2 ** retry)
        raise Exception("Failed to login to Mega.nz after multiple attempts.")

    def _get_or_create_folder(self, path: str):
        # Support nested folder creation
        parent = None
        for part in filter(None, path.strip('/').split('/')):
            folder = self.mega.find(part, parent=parent)
            if not folder:
                folder = self.mega.create_folder(part, parent=parent)
            parent = folder[0] if isinstance(folder, list) else folder
        return parent

    def upload_file(self, filepath: str):
        retry = 0
        while retry < 5:
            try:
                self.logger.info(f"Uploading {filepath} to Mega.nz...")
                self.mega.upload(filepath, self.folder_node)
                self.logger.info(f"Uploaded {filepath} successfully.")
                return True
            except Exception as e:
                retry += 1
                self.logger.warning(f"Upload failed (attempt {retry}): {e}")
                time.sleep(2 ** retry)
        self.logger.error(f"Failed to upload {filepath} after multiple attempts.")
        return False
