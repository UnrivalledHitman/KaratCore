from fastapi.testclient import TestClient

from app.main import app

client: TestClient = TestClient(app)


def test_health_check_returns_ok() -> None:
    response = client.get("/health")  # type: ignore[attr-defined]
    assert response.status_code == 200  # type: ignore[union-attr]
    assert response.json() == {"status": "ok"}  # type: ignore[union-attr]
