from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_notes(client: TestClient) -> None:
    response = client.get("/notes")
    assert response.status_code == 200
    assert len(response.json()) == 3
