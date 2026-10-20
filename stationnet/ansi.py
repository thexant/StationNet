"""ASCII-art rendering for the station masthead."""

from functools import lru_cache
import unicodedata

from pyfiglet import Figlet


@lru_cache(maxsize=128)
def station_banner(name: str) -> str:
    """Render a station name in the compact style used by the original logo."""
    ascii_name = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .upper()
    )
    printable_name = "".join(
        character for character in ascii_name if character.isprintable()
    ).strip()

    # A bounded width wraps long names into readable four-line blocks instead
    # of forcing the masthead to shrink until it is illegible.
    renderer = Figlet(font="small", width=72)
    return renderer.renderText(printable_name or "STATIONNET").rstrip()
