@echo off
cd /d "%~dp0"
schtasks /create /tn "CCS Bot" /tr "\"%~dp0JALANKAN_BOT.bat\"" /sc onlogon /f
if errorlevel 1 (echo Gagal membuat tugas otomatis) else (echo Bot akan otomatis jalan saat Windows login. Hapus dengan: schtasks /delete /tn "CCS Bot" /f)
pause
