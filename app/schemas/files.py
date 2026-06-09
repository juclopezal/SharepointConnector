from pydantic import BaseModel


class UploadFileResponse(BaseModel):
    status: str = "uploaded"
    id: str
    name: str
    size: int | None = None
    webUrl: str
    drive_path: str | None = None


class FileMetadataResponse(BaseModel):
    id: str
    name: str
    size: int | None = None
    webUrl: str
    mime_type: str | None = None
    created_at: str | None = None
    modified_at: str | None = None
    download_url: str | None = None
