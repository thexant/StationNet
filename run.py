"""StationNet production entry point."""
import os
from waitress import serve
from stationnet import create_app


def _thread_count():
    """Return a sensible worker-pool size for the app's polling workload."""
    try:
        threads = int(os.environ.get("STATIONNET_THREADS", "16"))
    except ValueError as error:
        raise ValueError("STATIONNET_THREADS must be a positive whole number.") from error
    if threads < 1:
        raise ValueError("STATIONNET_THREADS must be a positive whole number.")
    return threads


def main():
    host = os.environ.get("STATIONNET_HOST", "0.0.0.0")
    port = int(os.environ.get("STATIONNET_PORT", "8080"))
    threads = _thread_count()
    print(f"StationNet listening on http://{host}:{port} ({threads} worker threads)")
    serve(create_app(), host=host, port=port, threads=threads)

if __name__ == "__main__":
    main()

