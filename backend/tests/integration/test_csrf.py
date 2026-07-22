def _login_without_csrf_header(client, admin_user):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "TestPassword123!"},
    )
    assert response.status_code == 200
    return response


def test_mutation_without_csrf_header_is_rejected(client, admin_user) -> None:
    _login_without_csrf_header(client, admin_user)
    response = client.post("/api/v1/auth/keep-alive")
    assert response.status_code == 403


def test_mutation_with_wrong_csrf_header_is_rejected(client, admin_user) -> None:
    _login_without_csrf_header(client, admin_user)
    response = client.post(
        "/api/v1/auth/keep-alive", headers={"X-CSRF-Token": "token-incorrecto"}
    )
    assert response.status_code == 403


def test_mutation_with_correct_csrf_header_succeeds(client, admin_user) -> None:
    login_response = _login_without_csrf_header(client, admin_user)
    csrf_token = login_response.cookies.get("csrf_token")
    response = client.post("/api/v1/auth/keep-alive", headers={"X-CSRF-Token": csrf_token})
    assert response.status_code == 200


def test_get_requests_do_not_require_csrf_header(client, admin_user) -> None:
    _login_without_csrf_header(client, admin_user)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200


def test_logout_clears_csrf_cookie(client, admin_user) -> None:
    login_response = _login_without_csrf_header(client, admin_user)
    csrf_token = login_response.cookies.get("csrf_token")

    logout_response = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token})
    assert logout_response.status_code == 200
    assert logout_response.cookies.get("csrf_token") is None
