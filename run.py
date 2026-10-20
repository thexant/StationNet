"""StationNet production entry point."""
import os
from waitress import serve
from stationnet import create_app

def main():
    host = os.environ.get("STATIONNET_HOST", "0.0.0.0")
    port = int(os.environ.get("STATIONNET_PORT", "8080"))
    print(f"StationNet listening on http://{host}:{port}")
    serve(create_app(), host=host, port=port, threads=8)

if __name__ == "__main__":
    main()

