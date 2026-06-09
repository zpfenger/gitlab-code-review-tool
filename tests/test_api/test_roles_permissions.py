from fastapi.testclient import TestClient

from app.main import app


def test_builtin_role_definitions_only_return_assignable_roles():
    with TestClient(app) as client:
        response = client.get("/api/roles/definitions/builtin")

    assert response.status_code == 200
    names = [item["name"] for item in response.json()]
    assert names == ["system_admin", "project_admin"]
