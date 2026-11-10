@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === SETUP CCS BOT (cukup sekali di tiap PC) ===
where python >nul 2>nul
if errorlevel 1 goto nopy
python -m pip install -r requirements.txt
if errorlevel 1 goto err
if not exist "seatalk-oapi\seatalk-oapi-sdk-py\setup.py" goto nosdk
python -m pip install "%~dp0seatalk-oapi\seatalk-oapi-sdk-py"
if errorlevel 1 goto err
if not exist "secrets\key.json" goto nokey
if exist config.bat goto done
set /p APPID=Masukkan SEATALK_APP_ID: 
for /f "delims=" %%S in ('powershell -NoProfile -Command "$s=Read-Host 'Masukkan SEATALK_APP_SECRET (tidak terlihat saat diketik)' -AsSecureString; [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))"') do set "SECRET=%%S"
>config.bat echo set "SEATALK_APP_ID=%APPID%"
>>config.bat echo set "SEATALK_APP_SECRET=%SECRET%"
echo Kredensial disimpan di config.bat (hanya di PC ini, jangan dibagikan).
:done
python -m unittest test_core >nul 2>nul
if errorlevel 1 (echo Peringatan: tes logika gagal) else (echo Tes logika OK)
echo.
echo SETUP SELESAI. Jalankan bot dengan klik dua kali JALANKAN_BOT.bat
pause
exit /b 0
:nopy
echo Python belum terpasang. Pasang dari python.org dan centang "Add python.exe to PATH", lalu jalankan SETUP.bat lagi.
pause
exit /b 1
:nosdk
echo Folder seatalk-oapi\seatalk-oapi-sdk-py tidak ditemukan. Salin folder SDK ke sini lalu ulangi.
pause
exit /b 1
:nokey
echo Berkas secrets\key.json tidak ditemukan. Salin key Google ke folder secrets lalu ulangi.
pause
exit /b 1
:err
echo Instalasi gagal. Periksa pesan di atas.
pause
exit /b 1
