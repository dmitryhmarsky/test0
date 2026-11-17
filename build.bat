@echo off
REM Сборка InstantReplay.exe (нужен Python 3.10+)
python -m pip install -r requirements.txt || exit /b 1
python -m PyInstaller --noconfirm --onefile --noconsole --name InstantReplay instant_replay.py || exit /b 1
echo.
echo Готово: dist\InstantReplay.exe
echo Положите ffmpeg.exe рядом с InstantReplay.exe (https://www.gyan.dev/ffmpeg/builds/ - сборка "full" или "essentials", версия 6.0+)
