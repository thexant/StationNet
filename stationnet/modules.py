"""Station service switches shared by navigation and request handling."""

# These service tabs did not previously have their own switch.
ADDITIONAL_MODULES = (
    ("station", "STATION"),
    ("board", "BULLETIN BOARD"),
    ("announcements", "ANNOUNCEMENTS"),
    ("chat", "CHAT"),
    ("mail", "MAIL"),
    ("radio", "RADIO"),
    ("archive", "FILES"),
    ("users", "USERS"),
    ("applications", "PROGRAMS"),
    ("moderation", "MODERATION"),
)

ALL_MODULES = (
    ("station", "STATION"),
    ("board", "BULLETIN BOARD"),
    ("imageboard", "IMAGEBOARD"),
    ("announcements", "ANNOUNCEMENTS"),
    ("events", "EVENTS"),
    ("kiwix", "WIKI"),
    ("videos", "VIDEOS"),
    ("library", "LIBRARY"),
    ("arcade", "ARCADE"),
    ("chat", "CHAT"),
    ("mail", "MAIL"),
    ("radio", "RADIO"),
    ("archive", "FILES"),
    ("users", "USERS"),
    ("office", "OFFICE"),
    ("applications", "PROGRAMS"),
    ("mud", "MUD"),
    ("user_feeds", "USER FEEDS"),
    ("moderation", "MODERATION"),
)

BLUEPRINT_MODULES = {
    "board": "board", "announcements": "announcements",
    "chat": "chat", "mail": "mail", "radio": "radio",
    "broadcast": "radio", "programs": "radio", "archive": "archive",
    "users": "users", "applications": "applications",
    "application_admin": "applications", "moderation": "moderation",
    "reports": "moderation", "imageboard": "imageboard",
    "events": "events", "feeds": "user_feeds", "office": "office",
    "kiwix": "kiwix", "videos": "videos", "library": "library",
    "arcade": "arcade", "mud": "mud",
}


def enabled(station, module):
    return bool(station and station[f"{module}_enabled"])


def request_module(request):
    if request.endpoint == "station.terminal":
        return "station"
    return BLUEPRINT_MODULES.get(request.blueprint)
