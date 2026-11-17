@echo off
chcp 65001 >nul
REM Сборка InstantReplay.exe (нужен Python 3.10+ с галочкой "tcl/tk" при установке)
python -m pip install -r requirements.txt pyinstaller || exit /b 1
python -m PyInstaller --noconfirm --onefile --windowed --name InstantReplay --icon icon.ico ^
  --collect-data customtkinter --hidden-import pyaudiowpatch instant_replay.py || exit /b 1
if exist ffmpeg.exe copy /y ffmpeg.exe dist\ >nul
echo.
echo Готово: dist\InstantReplay.exe
echo Рядом с exe должен лежать ffmpeg.exe (6.0+): https://www.gyan.dev/ffmpeg/builds/
