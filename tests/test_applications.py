from stationnet.applications import APPS, GAMES, catalog, find


def test_catalog_is_centralized_and_contains_both_categories():
    applications = catalog()

    assert applications
    assert {application.category for application in applications} == {GAMES, APPS}
    assert all(
        all(
            (
                application.slug,
                application.name,
                application.description,
                application.author,
                application.version,
            )
        )
        for application in applications
    )
    assert all(find(application.slug) is application for application in applications)


def test_directory_requires_login_and_is_linked_from_navigation(initialized):
    initialized.post("/logout", data={"csrf_token": _csrf(initialized)})
    response = initialized.get("/games-apps")
    assert response.status_code == 302
    assert "/login?next=/games-apps" in response.headers["Location"]

    response = initialized.post(
        "/login",
        data={
            "csrf_token": _csrf(initialized, "/login"),
            "username": "sysop",
            "password": "correct horse battery staple",
        },
        follow_redirects=True,
    )
    assert b'href="/games-apps"' in response.data
    assert b"GAMES AND APPS" in response.data


def test_directory_lists_metadata_and_display_uses_station_shell(initialized):
    response = initialized.get("/games-apps")
    assert response.status_code == 200
    assert b"Orbit Command [Placeholder]" in response.data
    assert b"Crew Roster [Placeholder]" in response.data
    assert b"StationNet Core" in response.data
    assert b"0.0.1" in response.data

    display = initialized.get("/games-apps/orbit-command")
    assert display.status_code == 200
    assert b"station-shell" in display.data
    assert b"STATION APPLICATION DISPLAY" in display.data
    assert b"FUNCTIONALITY HAS NOT BEEN IMPLEMENTED YET" in display.data
    assert b"RETURN TO GAMES AND APPS" in display.data
    assert b'aria-current="page"' in display.data


def test_unknown_application_returns_not_found(initialized):
    assert initialized.get("/games-apps/not-registered").status_code == 404


def test_number_guess_uses_isolated_runtime_and_public_api(initialized):
    directory = initialized.get("/games-apps")
    assert b"Number Guess" in directory.data
    assert b"Signal Runner" not in directory.data

    display = initialized.get("/games-apps/number-guess")
    assert display.status_code == 200
    assert b'sandbox="allow-scripts"' in display.data
    assert b"application_host.js" in display.data

    runtime = initialized.get("/games-apps/number-guess/runtime")
    assert runtime.status_code == 200
    assert b"stationnet-application-source" in runtime.data
    assert "connect-src 'none'" in runtime.headers["Content-Security-Policy"]
    assert "worker-src blob:" in runtime.headers["Content-Security-Policy"]
    assert runtime.headers["X-Frame-Options"] == "SAMEORIGIN"

    source = initialized.get("/games-apps/number-guess/source.js")
    assert source.status_code == 200
    assert source.mimetype == "application/javascript"
    assert b"ui.numberInput" in source.data


def test_placeholder_has_no_runtime(initialized):
    assert initialized.get("/games-apps/orbit-command/runtime").status_code == 404
    assert initialized.get("/games-apps/orbit-command/source.js").status_code == 404


def _csrf(client, path="/terminal"):
    client.get(path)
    with client.session_transaction() as session:
        return session["csrf_token"]
