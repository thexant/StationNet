"""ASCII-art rendering for the station masthead."""

from functools import lru_cache
import unicodedata

from pyfiglet import Figlet, FigletFont


def available_header_fonts() -> tuple[str, ...]:
    """Return every FIGlet font shipped by the installed pyfiglet package."""
    try:
        return tuple(sorted(set(FigletFont.getFonts()), key=str.casefold))
    except (OSError, AttributeError):
        # A usable fallback keeps existing installations operable if a damaged
        # pyfiglet font directory is encountered.
        return ("small",)


def is_available_header_font(font: str) -> bool:
    return font in available_header_fonts()


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
        font=font if is_available_header_font(font) else "small",
        width=round(72 * 100 / safe_size),
    )
    return renderer.renderText(printable_name or "STATIONNET").rstrip()
