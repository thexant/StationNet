"""Shared firearm and ammunition definition rules for worlds and dungeons."""


def validate_item(item_type, slot, properties):
    """Return a player-facing error, or None for an ordinary or valid armed item."""
    firearm = properties.get("firearm")
    ammo_type = properties.get("ammo_type")
    capacity = properties.get("magazine_size")
    rounds = properties.get("rounds")
    if firearm is not None and firearm is not True:
        return "firearm must be true"
    if firearm is True:
        if item_type != "equipment" or slot not in ("main_hand", "off_hand"):
            return "firearms must be hand equipment"
        if not isinstance(ammo_type, str) or not ammo_type.strip() or len(ammo_type) > 64:
            return "firearms need an ammo_type of 1–64 characters"
        if type(capacity) is not int or not 1 <= capacity <= 100:
            return "magazine_size must be between 1 and 100"
        if rounds is not None:
            return "firearms cannot define loose rounds"
    elif ammo_type is not None or capacity is not None or rounds is not None:
        if item_type != "misc" or capacity is not None:
            return "ammunition must be a miscellaneous item with no magazine_size"
        if not isinstance(ammo_type, str) or not ammo_type.strip() or len(ammo_type) > 64:
            return "ammunition needs an ammo_type of 1–64 characters"
        if type(rounds) is not int or not 1 <= rounds <= 1000:
            return "ammunition rounds must be between 1 and 1000"
    return None


def is_firearm(properties):
    return properties.get("firearm") is True and validate_item(
        "equipment", "main_hand", properties) is None
