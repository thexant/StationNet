"""Station service switches shared by navigation and request handling."""

# These service tabs did not previously have their own switch.
ADDITIONAL_MODULES = (
    ("board", "BULLETIN BOARD"),
    ("announcements", "ANNOUNCEMENTS"),
    ("chat", "CHAT"),
    ("mail", "MAIL"),
    ("radio", "RADIO"),
    ("archive", "FILES"),
    ("users", "USERS"),
    ("applications", "PROGRAMS"),
)

ALL_MODULES = (
    ("board", "BULLETIN BOARD"),
    ("imageboard", "IMAGEBOARD"),
    ("announcements", "ANNOUNCEMENTS"),
    ("events", "EVENTS"),
    ("kiwix", "WIKI"),
    ("videos", "VIDEOS"),
    ("library", "LIBRARY"),
    ("arcade", "ARCADE"),
    ("tv", "TV"),
    ("chat", "CHAT"),
    ("mail", "MAIL"),
    ("radio", "RADIO"),
    ("archive", "FILES"),
    ("users", "USERS"),
    ("office", "OFFICE"),
    ("applications", "PROGRAMS"),
    ("mud", "MUD"),
    ("user_feeds", "USER FEEDS"),
)

BLUEPRINT_MODULES = {
    "board": "board", "announcements": "announcements",
    "chat": "chat", "mail": "mail", "radio": "radio",
    "broadcast": "radio", "programs": "radio", "archive": "archive",
    "users": "users", "applications": "applications",
    "application_admin": "applications", "imageboard": "imageboard",
    "events": "events", "feeds": "user_feeds", "office": "office",
    "kiwix": "kiwix", "videos": "videos", "library": "library",
    "arcade": "arcade", "mud": "mud", "tv": "tv",
}


def enabled(station, module):
    return bool(station and station[f"{module}_enabled"])


def request_module(request):
    return BLUEPRINT_MODULES.get(request.blueprint)
