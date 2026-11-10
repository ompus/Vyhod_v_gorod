import pytest
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_auth_and_conversations_flow(client):
    username = "test_user_flow"
    password = "password123"

    # Register
    reg_res = client.post("/api/auth/register", json={"username": username, "password": password})
    assert reg_res.status_code in [200, 400]  # 400 if already exists

    # Login
    login_res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    assert token is not None

    headers = {"Authorization": f"Bearer {token}"}

    # Get Me
    me_res = client.get("/api/auth/me", headers=headers)
    assert me_res.status_code == 200
    assert me_res.json()["username"] == username

    # Create conversation
    conv_res = client.post("/api/conversations", json={"title": "Тестовый чат"}, headers=headers)
    assert conv_res.status_code == 200
    conv_id = conv_res.json()["id"]

    # List conversations
    list_res = client.get("/api/conversations", headers=headers)
    assert list_res.status_code == 200
    assert any(c["id"] == conv_id for c in list_res.json())

    # Delete conversation
    del_res = client.delete(f"/api/conversations/{conv_id}", headers=headers)
    assert del_res.status_code == 200


def test_semantic_places_endpoint(client):
    res = client.get("/api/places/semantic?q=пельмени&limit=3")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "name" in data[0]


def test_chat_sse_stream(client):
    username = "stream_user"
    password = "stream_pass_123"
    client.post("/api/auth/register", json={"username": username, "password": password})
    login_res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]

    response = client.get(f"/api/chat/stream?message=пельмени&token={token}")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    text = response.text
    assert "event: session" in text
    assert "event: done" in text


def test_conversational_free_dialogue_and_history_recall(client):
    username = "dialogue_user"
    password = "dialogue_pass_123"
    client.post("/api/auth/register", json={"username": username, "password": password})
    login_res = client.post("/api/auth/login", json={"username": username, "password": password})
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create a conversation
    conv_res = client.post("/api/conversations", json={"title": "Диалог"}, headers=headers)
    conv_id = conv_res.json()["id"]

    # 2. Greeting
    r1 = client.get(f"/api/chat/stream?message=Привет!&conversation_id={conv_id}&token={token}")
    assert r1.status_code == 200
    assert "event: done" in r1.text

    # 3. Ask about conversation history: "О чём мы общались сейчас?"
    r2 = client.get(f"/api/chat/stream?message=О чём мы общались сейчас?&conversation_id={conv_id}&token={token}")
    assert r2.status_code == 200
    assert "event: done" in r2.text

