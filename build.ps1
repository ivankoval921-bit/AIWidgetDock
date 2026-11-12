# Сборка AIWidgetDock.exe (один файл) из D:\AIWidgetDock\src. Исходники НЕ перезаписываются.
# Движок src\engine.py сделан из C:\DesktopClaudeWidget\widget.py (пути -> %APPDATA%\AIWidgetDock); при необходимости обновляется отдельно.
$ErrorActionPreference = 'Stop'
$root = 'D:\AIWidgetDock'; $src = Join-Path $root 'src'
$py = 'C:\Program Files\Python311\python.exe'
& $py -m py_compile (Join-Path $src 'dock.py') (Join-Path $src 'engine.py')
$ErrorActionPreference = "Continue"
Push-Location $root
& $py -m PyInstaller --noconfirm --onefile --windowed --name AIWidgetDock --icon "$root\assets\app.ico" `
  --add-data "$src\engine.py;." --add-data "$src\cowork_context.py;." --add-data "$root\assets;assets" `
  --hidden-import win32crypt --hidden-import Crypto.Cipher.AES --hidden-import websocket --hidden-import sqlite3 --hidden-import winsound `
  --distpath "$root\dist" --workpath "$root\build" --specpath "$root\build" "$src\dock.py" 2>&1 | Select-Object -Last 4
Pop-Location
Get-Item "$root\dist\AIWidgetDock.exe" | Select-Object Name, Length, LastWriteTime
