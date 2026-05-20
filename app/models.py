from typing import Any, Optional
from pydantic import BaseModel


class UploadPayload(BaseModel):
    token: Optional[str] = None   # ignored — kept for backward compat with PA callers
    folder: str
    filename: str
    data: str                     # base64-encoded file content
    drive_name: Optional[str] = None  # overrides DEFAULT_DRIVE_NAME


class ListPayload(BaseModel):
    token: Optional[str] = None   # ignored — kept for backward compat
    list_name: Optional[str] = None   # falls back to DEFAULT_LIST_NAME
    fields: dict[str, Any]        # any key/value types accepted by the SP list
