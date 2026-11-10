@echo off
chcp 65001 >nul
cd /d "%~dp0"
title CCS Bot (jangan ditutup)
if not exist config.bat goto nocfg
call config.bat
set "GOOGLE_APPLICATION_CREDENTIALS=%~dp0secrets\key.json"
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
:loop
if exist ".git" call :update
echo [%date% %time%] Menjalankan bot...
python main.py
echo [%date% %time%] Bot berhenti. Mulai ulang 10 detik lagi. Tutup jendela ini untuk menghentikan.
timeout /t 10 /nobreak >nul
goto loop

:update
for /f %%H in ('git rev-parse HEAD') do set "OLD=%%H"
git pull --ff-only
for /f %%H in ('git rev-parse HEAD') do set "NEW=%%H"
if "%OLD%"=="%NEW%" exit /b 0
echo Versi baru ditarik. Memasang paket dan menjalankan tes...
python -m pip install -r requirements.txt >nul 2>nul
python -m unittest test_core >nul 2>nul
if errorlevel 1 goto rollback
echo Update OK.
exit /b 0
:rollback
echo TES GAGAL pada versi baru. Kembali ke versi lama.
>bad_commit.txt echo %NEW%
git reset --hard %OLD%
exit /b 0

:nocfg
echo config.bat belum ada. Jalankan SETUP.bat dulu.
pause
