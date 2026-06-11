"""Lectura validada de archivos subidos vía multipart/form-data."""

from fastapi import UploadFile

from app.core.exceptions import GraphAPIError

# Límite de la subida simple de Graph (PUT .../content): 4 MB.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024


async def read_upload(file: UploadFile) -> bytes:
    """Lee el archivo subido aplicando el límite de tamaño.

    Lee como máximo ``MAX_UPLOAD_BYTES + 1`` bytes, de modo que un archivo
    demasiado grande se rechaza con 413 sin bufferizarlo entero en memoria.
    """
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise GraphAPIError(
            413,
            "El archivo supera el tamaño máximo de 4 MB "
            "(límite de la subida simple de Microsoft Graph)",
        )
    return data
