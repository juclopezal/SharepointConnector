from typing import Any

from pydantic import BaseModel


class CreateListItemRequest(BaseModel):
    fields: dict[str, Any]


class ListItemResponse(BaseModel):
    id: str
    fields: dict[str, Any] | None = None
    webUrl: str | None = None
    created_at: str | None = None
    modified_at: str | None = None


class CreateListItemResponse(BaseModel):
    status: str = "created"
    id: str
    webUrl: str | None = None


class ListItemsResponse(BaseModel):
    site_id: str
    list_id: str
    items: list[ListItemResponse]
    total: int
