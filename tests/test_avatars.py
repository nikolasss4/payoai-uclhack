"""Profile pictures for people and organisations: who can change them, what's accepted, and how they're served."""

import struct
import zlib

from fastapi.testclient import TestClient

from payoai.api.app import _sign, app

client = TestClient(app)


def png() -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + b"\xd4\xf5\x3c" * 4 for _ in range(4))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def auth(team, role, with_team=True):
    h = {"Authorization": f"Bearer {_sign(team['users'][role])}"}
    return {**h, "X-Team-Id": team["id"]} if with_team else h


def upload(path, headers, data=None, name="me.png"):
    return client.put(path, headers=headers, files={"file": (name, data if data is not None else png(), "image/png")})


def test_anyone_can_set_and_remove_their_own_picture(team):
    r = upload("/api/me/avatar", auth(team, "VIEWER", with_team=False))
    assert r.status_code == 200
    url = r.json()["avatar_url"]
    assert url.startswith(f"/api/avatars/user/{team['users']['VIEWER']}?")
    me = client.get("/api/me", headers=auth(team, "VIEWER", with_team=False)).json()
    assert me["user"]["avatar_url"] == url
    image = client.get(url)
    assert image.status_code == 200 and image.headers["content-type"] == "image/png" and image.content == png()
    assert image.headers["x-content-type-options"] == "nosniff"
    assert client.delete("/api/me/avatar", headers=auth(team, "VIEWER", with_team=False)).json() == {"avatar_url": None}
    assert client.get(url).status_code == 404


def test_only_an_approver_changes_the_organisation_picture_and_it_is_audited(team):
    for role in ("VIEWER", "OPERATOR"):
        assert upload("/api/team/avatar", auth(team, role)).status_code == 403
    r = upload("/api/team/avatar", auth(team, "APPROVER"))
    assert r.status_code == 200
    me = client.get("/api/me", headers=auth(team, "OPERATOR", with_team=False)).json()
    assert next(t for t in me["teams"] if t["id"] == team["id"])["avatar_url"] == r.json()["avatar_url"]
    entries = client.get("/api/audit?limit=5", headers=auth(team, "VIEWER")).json()["entries"]
    assert entries[0]["what"] == "Organisation picture changed"


def test_audit_entries_carry_the_persons_picture(team):
    url = upload("/api/me/avatar", auth(team, "APPROVER", with_team=False)).json()["avatar_url"]
    upload("/api/team/avatar", auth(team, "APPROVER"))
    entry = client.get("/api/audit?limit=1&traces=1", headers=auth(team, "VIEWER")).json()["entries"][0]
    assert entry["photo"] == url


def test_files_that_are_not_pictures_are_rejected(team):
    h = auth(team, "APPROVER", with_team=False)
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    assert upload("/api/me/avatar", h, svg, "x.png").status_code == 422
    assert upload("/api/me/avatar", h, b"").status_code == 422
    assert upload("/api/me/avatar", h, png() + b"\x00" * (2 * 1024 * 1024)).status_code == 422
    assert upload("/api/me/avatar", {}).status_code == 401


def test_picture_urls_cannot_be_forged(team):
    url = upload("/api/team/avatar", auth(team, "APPROVER")).json()["avatar_url"]
    path, query = url.split("?")
    sig = query.split("s=")[1]
    forged = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    assert client.get(f"{path}?{query.replace(sig, forged)}").status_code == 404
    assert client.get(path.replace("/team/", "/user/") + "?" + query).status_code == 404
    assert client.get(path).status_code == 404
