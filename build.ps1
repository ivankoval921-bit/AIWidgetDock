# Сборка AIWidgetDock.exe: движок из C:\DesktopClaudeWidget\widget.py (пути -> %APPDATA%\AIWidgetDock), один exe
$ErrorActionPreference = 'Stop'
$root = 'D:\AIWidgetDock'; $src = Join-Path $root 'src'
New-Item -ItemType Directory -Force $src, (Join-Path $root 'assets'), (Join-Path $root 'dist') | Out-Null
Copy-Item 'D:\ClaudeProjects_new\PC Organazer\_aiwd_src\dock.py' $src -Force
$w = [IO.File]::ReadAllText('C:\DesktopClaudeWidget\widget.py', [Text.Encoding]::UTF8)
$app = "os.path.join(os.environ.get('APPDATA',''), 'AIWidgetDock', 'engine'"
$w = $w.Replace('r"C:\DesktopClaudeWidget\error.txt"', "$app, 'error.txt')")
$w = $w.Replace('r"C:\DesktopClaudeWidget\widget_debug.log"', "$app, 'engine_debug.log')")
$w = $w.Replace('BASE_DIR = os.path.dirname(os.path.abspath(__file__))', "BASE_DIR = $app)`nos.makedirs(BASE_DIR, exist_ok=True)")
if ($w -match 'C:\\DesktopClaudeWidget') { 'WARN: hard path left:'; ([regex]::Matches($w, '.{0,60}C:\\DesktopClaudeWidget.{0,40}') | Select-Object -First 5 | ForEach-Object { $_.Value }) }
[IO.File]::WriteAllText((Join-Path $src 'engine.py'), $w, (New-Object Text.UTF8Encoding($false)))
Copy-Item 'C:\DesktopClaudeWidget\cowork_context.py' $src -Force
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
