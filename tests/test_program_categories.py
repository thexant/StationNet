import sqlite3

from stationnet.applications import Application, CATEGORIES, _validate_metadata
from stationnet.db import _migrate_application_categories


def test_program_categories_accept_new_package_types():
    assert CATEGORIES == ("Games", "Utilities", "Creative", "Educational", "Productivity", "Other")
    for category in CATEGORIES:
        _validate_metadata(Application("sample", "Sample", "Description", "Author", "1", category))


def test_legacy_apps_keep_their_installation_when_categories_migrate():
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.execute(
        "CREATE TABLE installed_applications ("
        "slug TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,"
        "author TEXT NOT NULL,version TEXT NOT NULL,"
        "category TEXT NOT NULL CHECK(category IN ('Games','Apps')) ,"
        "entrypoint TEXT NOT NULL,permissions_json TEXT NOT NULL DEFAULT '[]',"
        "capabilities_json TEXT NOT NULL DEFAULT '{}',"
        "installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    database.execute(
        "INSERT INTO installed_applications(slug,name,description,author,version,category,entrypoint) "
        "VALUES('legacy','Legacy','Old app','Tester','1','Apps','main.js')"
    )
    _migrate_application_categories(database)
    assert tuple(database.execute(
        "SELECT category,entrypoint FROM installed_applications WHERE slug='legacy'"
    ).fetchone()) == ("Other", "main.js")
    database.execute(
        "INSERT INTO installed_applications(slug,name,description,author,version,category,entrypoint) "
        "VALUES('new','New','New app','Tester','1','Educational','main.js')"
    )
    _migrate_application_categories(database)
