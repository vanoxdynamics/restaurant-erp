async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


async def test_ready(client):
    r = await client.get("/health/ready")
    assert r.status_code == 200, r.text
    assert r.json()["database"] == "ok" and r.json()["redis"] == "ok"


async def test_unauthenticated_requests_are_rejected(client):
    for path in ("/api/v1/orders", "/api/v1/catalog/items", "/api/v1/kots", "/api/v1/staff"):
        r = await client.get(path)
        assert r.status_code == 401, path
        assert r.json()["error"]["code"] == "unauthorized"


async def test_request_id_is_echoed(client):
    r = await client.get("/health", headers={"X-Request-ID": "abc123"})
    assert r.headers["x-request-id"] == "abc123"
