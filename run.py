"""StationNet production entry point."""
import argparse
import signal
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path


def _port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _report(result):
    print(result.message)
    for rel in result.skipped:
        print(f"  not copied (move by hand): {Path(rel).as_posix()}")
    for rel in result.kept:
        print(f"  left in place: {rel}")
    for path in result.leftovers:
        print(f"  WARNING: incomplete earlier migration folder, safe to delete once checked: {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a StationNet station.")
    parser.add_argument("--station", help="station id under stations/ (default: STATIONNET_STATION or 'default')")
    parser.add_argument("--migrate-only", action="store_true",
                        help="migrate a legacy data/ folder into stations/default/ and exit")
    parser.add_argument("--dry-run", action="store_true", help="with --migrate-only, report without changing anything")
    args = parser.parse_args(argv)

    # Imported late so --help and migration work without loading the app.
    from stationnet import instance, station_migration
    try:
        station = instance.resolve(Path(__file__).resolve().parent, args.station)
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}")

    if not station.standalone and station.id == instance.DEFAULT_STATION:
        legacy = station.root / "data"
        if station_migration.needs_migration(legacy) and not args.dry_run and _port_in_use(station.port):
            raise SystemExit(f"ERROR: Port {station.port} is in use. Stop StationNet before migrating {legacy}.")
        result = station_migration.migrate_legacy(legacy, station.data_dir.parent, station.id, dry_run=args.dry_run)
        if result.status != "noop" or result.leftovers:
            _report(result)
        if result.status == "refused":
            raise SystemExit(1)
        station.config = instance.load_config(station.data_dir)
    if args.migrate_only:
        return

    if not station.standalone:
        station.data_dir.mkdir(parents=True, exist_ok=True)
        instance.write_config_if_missing(station.data_dir, instance.new_config(station.id))
        station.config = instance.load_config(station.data_dir)

    from stationnet.station_runtime import AlreadyRunning, StationRuntime
    runtime = StationRuntime(station.data_dir)
    try:
        runtime.acquire()
    except AlreadyRunning as exc:
        raise SystemExit(f"ERROR: {exc}")
    try:
        _serve(station, runtime)
    finally:
        runtime.release()
        runtime.clear_stop()


def _interrupted(signum, frame):
    raise KeyboardInterrupt


def _serve(station, runtime):
    """Run the server while publishing status and honoring stop requests."""
    import uvicorn
    from stationnet import create_app
    from stationnet.multiplayer_socket import create_asgi_app
    runtime.clear_stop()
    status = {"host": station.host, "port": station.port,
              "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    runtime.write_status(state="starting", **status)
    mode = "standalone" if station.standalone else f"station '{station.id}'"
    print(f"StationNet {mode}: data in {station.data_dir}", flush=True)
    print(f"StationNet listening on http://{station.host}:{station.port} (WebSocket enabled)", flush=True)
    clean = False
    try:
        server = uvicorn.Server(uvicorn.Config(
            create_asgi_app(create_app(station.app_config())), host=station.host, port=station.port,
            ws_max_size=16 * 1024, ws_max_queue=64, workers=1))
        done = threading.Event()

        def watch():
            announced = False
            while not done.wait(0.5):
                if not announced and server.started:
                    runtime.write_status(state="running", **status)
                    announced = True
                if runtime.stop_requested_at() is not None:
                    server.should_exit = True

        watcher = threading.Thread(target=watch, name="station-runtime", daemon=True)
        watcher.start()
        # uvicorn re-raises the signal that stopped it once shutdown is done;
        # make SIGTERM end up as the same clean interrupt as Ctrl+C.
        signal.signal(signal.SIGTERM, _interrupted)
        try:
            server.run()
            clean = server.started or runtime.stop_requested_at() is not None
        except KeyboardInterrupt:
            clean = True
        finally:
            done.set()
            # Its last status write must not land after the cleanup below.
            watcher.join()
    finally:
        # A status left behind with the lock free tells launchers it failed.
        if clean:
            runtime.clear_status()
        else:
            runtime.write_status(state="failed", **status)

if __name__ == "__main__":
    main()
