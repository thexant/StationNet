"""Validation and rules shared by authored properties, storage points, the builder, and world packages.

A property is a set of rooms a character can hold: one shared set ("unique")
or a template every holder gets a private copy of ("instanced"). A storage
point keeps items for one character ("personal"), for a property's owners
("property"), or for anyone who may use it ("communal"). mud.py owns the
commands, rent, and access checks.
"""

from datetime import datetime, timedelta, timezone

from . import mud_conditions

KINDS = ("unique", "instanced")
ACQUISITIONS = ("purchase", "claim", "grant")
SCOPES = ("personal", "property", "communal")
ITEM_TYPES = ("misc", "equipment", "container", "consumable", "key")
# Authored defaults; exports leave out fields that still have them.
PROPERTY_DEFAULTS = {"kind": "unique", "acquisition": "purchase", "price": 0, "refund_percent": 0, "rent": 0,
                     "rent_period_seconds": 604800, "grace_seconds": 86400, "prepay_periods": 4,
                     "max_owners": 1, "agent_npc_definition_id": None, "conditions_json": [], "available": 1}
STORAGE_DEFAULTS = {"description": "", "scope": "personal", "capacity": 20, "item_types": "", "conditions_json": []}
PROPERTY_FIELDS = ("name", "description", *PROPERTY_DEFAULTS)
STORAGE_FIELDS = ("name", *STORAGE_DEFAULTS)


def _text(value, label, low, high):
    if not isinstance(value, str) or not low <= len(value.strip()) <= high:
        raise ValueError(f"{label} must be {low} to {high} characters.")
    return " ".join(value.split()) if label.endswith("name") else value.strip()


def _number(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be a whole number from {low} to {high}.")
    return value


def _choice(value, label, options):
    if value not in options:
        raise ValueError(f"{label} must be one of {', '.join(options)}.")
    return value


def item_types(value):
    """A storage point's allowed item types as stored text: blank for any, else a sorted comma list."""
    if value in (None, "", []):
        return ""
    parts = value.split(",") if isinstance(value, str) else value
    if not isinstance(parts, list) or any(not isinstance(part, str) for part in parts):
        raise ValueError("Storage item types must be a list of item types.")
    chosen = {part.strip() for part in parts if part.strip()}
    if chosen - set(ITEM_TYPES):
        raise ValueError(f"Storage item types must be from {', '.join(ITEM_TYPES)}.")
    return ",".join(kind for kind in ITEM_TYPES if kind in chosen)


def property(row):
    """A normalized property row (without rooms or IDs) or ValueError."""
    get = lambda key: row.get(key, PROPERTY_DEFAULTS.get(key))  # noqa: E731 - one lookup rule for every field
    rent = _number(get("rent"), "Rent", 0, 1_000_000)
    available = get("available")
    if type(available) is bool:
        available = int(available)
    if available not in (0, 1):
        raise ValueError("Property available must be true or false.")
    result = {
        "name": _text(row.get("name"), "Property name", 2, 80),
        "description": _text(row.get("description"), "Property description", 1, 2000),
        "kind": _choice(get("kind"), "Property kind", KINDS),
        "acquisition": _choice(get("acquisition"), "Property acquisition", ACQUISITIONS),
        "price": _number(get("price"), "Price", 0, 1_000_000),
        "refund_percent": _number(get("refund_percent"), "Refund percent", 0, 100),
        "rent": rent,
        "rent_period_seconds": _number(get("rent_period_seconds"), "Rent period", 3600, 31_536_000),
        "grace_seconds": _number(get("grace_seconds"), "Grace period", 0, 2_592_000),
        "prepay_periods": _number(get("prepay_periods"), "Prepaid periods", 1, 52),
        "max_owners": _number(get("max_owners"), "Owner limit", 1, 20),
        "agent_npc_definition_id": get("agent_npc_definition_id") or None,
        "conditions_json": mud_conditions.validate_conditions(get("conditions_json")),
        "available": available,
    }
    if result["acquisition"] != "purchase" and result["price"]:
        raise ValueError("Only purchased properties have a price.")
    return result


def storage_point(row):
    """A normalized storage point row (without its room or ID) or ValueError."""
    get = lambda key: row.get(key, STORAGE_DEFAULTS.get(key))  # noqa: E731
    description = get("description")
    if not isinstance(description, str) or len(description.strip()) > 1000:
        raise ValueError("Storage description must be at most 1000 characters.")
    return {
        "name": _text(row.get("name"), "Storage name", 2, 80),
        "description": description.strip(),
        "scope": _choice(get("scope"), "Storage scope", SCOPES),
        "capacity": _number(get("capacity"), "Storage capacity", 1, 500),
        "item_types": item_types(get("item_types")),
        "conditions_json": mud_conditions.validate_conditions(get("conditions_json")),
    }


def rent_share(rent, owner_count, primary):
    """One owner's part of a period's rent: split evenly, the primary owner pays any remainder."""
    owner_count = max(1, owner_count)
    return rent // owner_count + (rent % owner_count if primary else 0)


def parse_time(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc) if value else None


def stamp(moment):
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def extended(paid_until, period_seconds, periods, now):
    """paid_until after paying periods more: counted from now if it had already lapsed."""
    start = max(parse_time(paid_until) or now, now)
    return start + timedelta(seconds=period_seconds * periods)


def prepaid_periods(paid_until, period_seconds, now):
    """Whole periods already paid beyond now (a part-period counts as one)."""
    until = parse_time(paid_until)
    if until is None or until <= now:
        return 0
    return -(-int((until - now).total_seconds()) // period_seconds)
