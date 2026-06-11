"""Covers fix #6: `folder` is a first-class FastAPI query parameter, so it
appears in the OpenAPI schema and is parsed/validated by the framework instead
of being read manually from `request.query_params`.
"""

UPLOAD_PATH = "/v1/graph/sites/{site_id}/drives/{drive_id}/files"


def test_folder_is_declared_query_param(client):
    schema = client.app.openapi()
    operation = schema["paths"][UPLOAD_PATH]["post"]
    params = {p["name"]: p["in"] for p in operation.get("parameters", [])}
    assert params.get("folder") == "query"


async def test_upload_passes_folder_to_service(client, fake_sp):
    received = {}

    async def fake_upload(*, site_id, drive_id, folder, filename, data):
        received.update(
            site_id=site_id, drive_id=drive_id, folder=folder, filename=filename
        )
        return {
            "id": "01ABC",
            "name": filename,
            "size": len(data),
            "webUrl": "https://example/doc",
            "parentReference": {"path": "/drive/root:/Sub"},
        }

    fake_sp.upload_file = fake_upload

    resp = client.post(
        "/v1/graph/sites/site1/drives/drive1/files",
        params={"folder": "Sub/Folder"},
        files={"file": ("doc.txt", b"hello", "text/plain")},
    )

    assert resp.status_code == 201
    assert received["folder"] == "Sub/Folder"
    assert received["filename"] == "doc.txt"
    body = resp.json()
    assert body["status"] == "uploaded"
    assert body["id"] == "01ABC"


async def test_upload_too_large_returns_413(client, fake_sp):
    uploaded = {"called": False}

    async def fake_upload(**kwargs):
        uploaded["called"] = True
        return {}

    fake_sp.upload_file = fake_upload

    resp = client.post(
        "/v1/graph/sites/site1/drives/drive1/files",
        files={"file": ("big.bin", b"x" * (4 * 1024 * 1024 + 1), "application/octet-stream")},
    )

    assert resp.status_code == 413
    assert uploaded["called"] is False
