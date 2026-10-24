"""StationNet production entry point."""
import os
import sys
from pathlib import Path
import uvicorn
from stationnet import create_app
from stationnet.multiplayer_socket import create_asgi_app


def main():
    # Source launches are independent of the caller's working directory. Packaged
    # launchers set STATIONNET_DATA to a persistent directory outside the app.
    if not os.environ.get("STATIONNET_DATA") and not getattr(sys, "frozen", False):
        os.environ["STATIONNET_DATA"] = str(Path(__file__).resolve().parent / "data")
    host = os.environ.get("STATIONNET_HOST", "0.0.0.0")
    port = int(os.environ.get("STATIONNET_PORT", "8080"))
    print(f"StationNet listening on http://{host}:{port} (WebSocket enabled)")
    uvicorn.run(create_asgi_app(create_app()), host=host, port=port,
                ws_max_size=16 * 1024, ws_max_queue=64, workers=1)

if __name__ == "__main__":
    main()

