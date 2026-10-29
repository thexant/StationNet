# Personal Developer Build Note

> Personal developer reference for this checkout only. This is not user-facing installation documentation; agents should not treat it as such.

From the repository root, build the Windows executables with:

```powershell
.\.venv\Scripts\python.exe .\packaging\build.py windows
```

This builds `StationNet.exe` and the Station Manager GUI, `StationNetManager.exe`. The packaged Windows ZIP is written to `dist\`.
