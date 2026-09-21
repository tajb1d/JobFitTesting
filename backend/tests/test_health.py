def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_needs_no_auth(client):
    # No Authorization header, and the DB stand-in would fail if touched.
    assert client.get("/health").status_code == 200
