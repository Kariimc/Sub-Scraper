import pickle
import threading
from pathlib import Path
from typing import Any, Optional

_TOKEN_PATH = Path.home() / ".sub_scraper" / "gdrive_token.pkl"
_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class GDriveUploader:
    def __init__(self, credentials_path: str, folder_id: str = "") -> None:
        self.credentials_path = credentials_path
        self.folder_id = folder_id
        self._service: Optional[Any] = None
        # Guards the one-time OAuth flow. Without this, parallel download
        # workers each launch run_local_server + a browser at once, which
        # crashes the process (segfault) on the first batch.
        self._auth_lock = threading.Lock()
        # The googleapiclient service (httplib2 transport) is NOT thread-safe.
        # Parallel workers sharing one connection corrupt it at the C level
        # and segfault, so every upload is serialized through this lock.
        self._upload_lock = threading.Lock()

    def _get_service(self) -> Any:
        if self._service:
            return self._service

        with self._auth_lock:
            # Re-check inside the lock: another worker may have just finished
            # authenticating while we were waiting.
            if self._service:
                return self._service

            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build

            creds: Optional[Credentials] = None
            if _TOKEN_PATH.exists():
                with open(_TOKEN_PATH, "rb") as f:
                    creds = pickle.load(f)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, _SCOPES)
                    creds = flow.run_local_server(port=0)
                _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(_TOKEN_PATH, "wb") as f:
                    pickle.dump(creds, f)

            self._service = build("drive", "v3", credentials=creds)
            return self._service

    def upload(self, file_path: str) -> str:
        from googleapiclient.http import MediaFileUpload

        service = self._get_service()
        path = Path(file_path)
        metadata: dict = {"name": path.name}
        if self.folder_id:
            metadata["parents"] = [self.folder_id]

        media = MediaFileUpload(str(path), resumable=True)
        with self._upload_lock:
            result = service.files().create(
                body=metadata, media_body=media, fields="id"
            ).execute()
        return result.get("id", "")
