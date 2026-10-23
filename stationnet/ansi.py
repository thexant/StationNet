"""ASCII-art rendering for the station masthead."""

from functools import lru_cache
import unicodedata

from pyfiglet import Figlet

HEADER_FONTS = (
    ("small", "Compact"),
    ("standard", "Classic terminal"),
    ("slant", "Slanted"),
    ("mini", "Minimal"),
    ("digital", "Digital display"),
    ("cybermedium", "Cyber"),
    ("doom", "Bold block"),
    ("shadow", "Shadow"),
    ("3-d", "3-D"),
    ("chunky", "Chunky"),
    ("univers", "Univers"),
    ("banner3-D", "Banner 3-D"),
    ("epic", "Epic"),
    ("larry3d", "Larry 3D"),
    ("starwars", "Star Wars"),
    ("cosmic", "Cosmic"),
    ("5lineoblique", "5 Line Oblique"),
    ("alligator", "Alligator"),
    ("avatar", "Avatar"),
    ("banner", "Banner"),
    ("doh", "Doh"),
    ("graffiti", "Graffiti"),
    ("ogre", "Ogre"),
    ("smkeyboard", "Small keyboard"),
    ("speed", "Speed"),
    ("sub-zero", "Sub-Zero"),
    ("weird", "Weird"),

)
HEADER_FONT_NAMES = frozenset(font for font, _label in HEADER_FONTS)


@lru_cache(maxsize=128)
def station_banner(name: str, font: str = "small", size: int = 100) -> str:
    """Render a station name in one of the supported masthead styles."""
    ascii_name = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .upper()
    )
    printable_name = "".join(
        character for character in ascii_name if character.isprintable()
    ).strip()

    # pyfiglet inserts its own newlines at this width. Scale that width in the
    # opposite direction of the CSS display size so a smaller masthead can use
    # the horizontal space it just gained instead of retaining old line wraps.
    safe_size = min(200, max(5, int(size)))
    renderer = Figlet(
        font=font if font in HEADER_FONT_NAMES else "small",
        width=round(72 * 100 / safe_size),
    )
    return renderer.renderText(printable_name or "STATIONNET").rstrip()
