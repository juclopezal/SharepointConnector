from pydantic import BaseModel


class SiteInfo(BaseModel):
    id: str
    name: str
    displayName: str
    webUrl: str
    description: str | None = None


class SitesResponse(BaseModel):
    sites: list[SiteInfo]
    total: int


class ListInfo(BaseModel):
    id: str
    name: str
    displayName: str
    webUrl: str
    list_template: str | None = None


class ListsResponse(BaseModel):
    site_id: str
    lists: list[ListInfo]
    total: int


class DriveInfo(BaseModel):
    id: str
    name: str
    driveType: str
    webUrl: str


class DrivesResponse(BaseModel):
    site_id: str
    drives: list[DriveInfo]
    total: int


class DriveItemInfo(BaseModel):
    id: str
    name: str
    webUrl: str
    size: int | None = None
    is_folder: bool
    created_at: str | None = None
    modified_at: str | None = None


class FolderContentsResponse(BaseModel):
    drive_id: str
    parent_id: str | None
    items: list[DriveItemInfo]
    total: int
