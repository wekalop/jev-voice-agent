"""Tests for the hosted setup: the password gate, one-caller limit, private conversations, and safe packaging."""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("RESULTS_DIR", str(tmp_path))
    monkeypatch.delenv("SPACE_ID", raising=False)
    monkeypatch.delenv("TURN_KEY_ID", raising=False)
    monkeypatch.delenv("TURNIX_API_TOKEN", raising=False)
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    module = importlib.import_module("live.server")
    module.current_call.update(busy=False, token=None)
    from live import access

    access._failures.clear()
    return module


def client(module):
    return TestClient(module.app, follow_redirects=False)


def test_open_on_your_own_machine_with_no_password(server):
    assert client(server).get("/").status_code == 200


def test_password_gate_blocks_page_and_api(server, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "correct-horse")
    web = client(server)
    assert web.get("/").status_code == 303
    assert web.get("/guide").status_code == 303
    assert web.get("/api/live").status_code == 401
    assert web.get("/api/ice").status_code == 401
    assert web.post("/api/offer", json={"sdp": "x", "type": "offer"}).status_code == 401


def test_wrong_password_fails_and_right_password_works(server, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "correct-horse")
    web = client(server)
    assert web.post("/login", data={"password": "nope"}).status_code == 401
    assert web.get("/").status_code == 303
    assert web.post("/login", data={"password": "correct-horse"}).status_code == 303
    assert web.get("/").status_code == 200


def test_forged_cookie_is_rejected(server, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "correct-horse")
    web = client(server)
    web.cookies.set("nabda_access", "forged")
    assert web.get("/").status_code == 303


def test_repeated_wrong_passwords_lock_out_even_the_right_one(server, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "correct-horse")
    web = client(server)
    for _ in range(10):
        web.post("/login", data={"password": "nope"})
    assert web.post("/login", data={"password": "correct-horse"}).status_code == 429


def test_hosting_refuses_to_start_without_a_password(server, monkeypatch):
    from live import access

    monkeypatch.setenv("SPACE_ID", "someone/space")
    with pytest.raises(RuntimeError):
        access.check_startup()


def test_second_caller_gets_a_busy_answer(server):
    server.current_call.update(busy=True, token="abc")
    assert client(server).post("/api/offer", json={"sdp": "x", "type": "offer"}).status_code == 409


def test_conversation_is_only_visible_to_the_caller(server):
    server.current_call.update(busy=True, token="abc")
    server.monitor.start_call("Hi")
    server.monitor.begin_turn("My number is secret", False, "steady")
    web = client(server)
    for headers in ({}, {"x-call-token": "wrong"}):
        body = web.get("/api/live", headers=headers).json()
        assert body["turns"] == [] and body["active"] is True
    assert web.get("/api/live", headers={"x-call-token": "abc"}).json()["turns"][0]["caller"] == "My number is secret"
    server.monitor.end_call()


def test_relay_settings_drop_blocked_ports_and_pick_one_server_relay():
    from live import ice

    cleaned = ice.without_blocked_ports([
        {"urls": ["stun:stun.cloudflare.com:3478", "stun:stun.cloudflare.com:53"]},
        {"urls": ["turn:turn.cloudflare.com:53?transport=udp", "turns:turn.cloudflare.com:443?transport=tcp"], "username": "u", "credential": "c"},
    ])
    assert cleaned[0]["urls"] == ["stun:stun.cloudflare.com:3478"]
    assert cleaned[1]["urls"] == ["turns:turn.cloudflare.com:443?transport=tcp"]
    servers = ice.server_side(cleaned)
    assert servers[1].urls == "turns:turn.cloudflare.com:443?transport=tcp" and servers[1].username == "u"
    picked = ice.server_side([{"urls": ["turn:a:3478?transport=udp", "turn:a:80?transport=tcp", "turns:a:443?transport=tcp"], "username": "u", "credential": "c"}])
    assert picked[0].urls == "turns:a:443?transport=tcp"


def test_either_relay_setup_counts_as_configured(monkeypatch):
    from live import ice

    for name in ("TURNIX_API_TOKEN", "TURN_KEY_ID", "TURN_KEY_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert not ice.configured()
    monkeypatch.setenv("TURNIX_API_TOKEN", "x")
    assert ice.configured()
    monkeypatch.delenv("TURNIX_API_TOKEN")
    monkeypatch.setenv("TURN_KEY_ID", "x")
    assert not ice.configured()
    monkeypatch.setenv("TURN_KEY_API_TOKEN", "x")
    assert ice.configured()


def test_space_package_has_no_secrets_or_private_files(monkeypatch, tmp_path):
    from deploy import build_space

    monkeypatch.setattr(build_space, "OUTPUT", tmp_path / "space")
    folder = build_space.build()
    names = {path.name for path in folder.rglob("*")}
    assert {"Dockerfile", "README.md", "requirements.txt", "support_policy.md", "turn.py"} <= names
    assert not names & {".env", ".venv", "turns_live.jsonl", "decisions.jsonl", "pytest"}
    assert "sdk: docker" in (folder / "README.md").read_text(encoding="utf-8")


def test_package_build_stops_if_a_secret_would_ship(monkeypatch, tmp_path):
    from deploy import build_space

    monkeypatch.setattr(build_space, "OUTPUT", tmp_path / "space")
    monkeypatch.setattr(build_space, "secret_values", lambda: ["Nabda is a mobile"])
    with pytest.raises(SystemExit):
        build_space.build()
    assert not (tmp_path / "space").exists()
