"""Schemas de los endpoints orientados a usuario (resolución por URL)."""

from typing import Any

from pydantic import BaseModel, Field


class ListItemByUrlRequest(BaseModel):
    sharepoint_url: str = Field(
        ...,
        description=(
            "URL de la lista tal como aparece en el navegador, p. ej. "
            "https://host.sharepoint.com/sitio/Lists/MiLista/AllItems.aspx"
        ),
    )
    data: dict[str, Any] = Field(
        ...,
        description=(
            "Campos del nuevo ítem. Las claves deben ser los nombres internos "
            "(internal name) de las columnas de la lista."
        ),
    )


class ListItemByUrlResponse(BaseModel):
    status: str = "created"
    id: str
    webUrl: str | None = None
    site_id: str
    list_id: str


class FilterBy(BaseModel):
    field: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_]+$",
        description=(
            "Nombre interno (internal name) de la columna que identifica de forma "
            "única el registro, p. ej. '_x006c_dq4'. Solo letras, dígitos y '_'."
        ),
    )
    value: str = Field(
        ...,
        description="Valor exacto que debe tener `field` en el registro a actualizar.",
    )


class ListItemUpdateByUrlRequest(BaseModel):
    sharepoint_url: str = Field(
        ...,
        description=(
            "URL de la lista tal como aparece en el navegador, p. ej. "
            "https://host.sharepoint.com/sitio/Lists/MiLista/AllItems.aspx"
        ),
    )
    filter_by: FilterBy = Field(
        ...,
        description=(
            "Campo único y valor con los que se localiza el registro a actualizar. "
            "Debe identificar un único ítem (si coincide más de uno se devuelve 409)."
        ),
    )
    data: dict[str, Any] = Field(
        ...,
        description=(
            "Campos a actualizar. Las claves deben ser los nombres internos "
            "(internal name) de las columnas de la lista."
        ),
    )


class ListItemUpdateByUrlResponse(BaseModel):
    status: str = "updated"
    id: str
    webUrl: str | None = None
    site_id: str
    list_id: str


class UploadByUrlResponse(BaseModel):
    status: str = "uploaded"
    id: str
    name: str
    size: int | None = None
    webUrl: str
    site_id: str
    drive_id: str
    folder: str
