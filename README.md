# CCS Assistant (Python, tanpa LLM) - app 41006

Alur sama dengan bot Smart: manusia non-TL menyapa bot -> kartu "Mohon dibantu cek ya, @TL" + baris ASSIGNED;
TL membalas di thread -> baris ACK_LOGGED; tanpa respons dalam `PILIH TL!B15` menit -> eskalasi ke TL berikutnya
(maks 5 TL/thread, kedaluwarsa 40 menit). Pengecekan eskalasi tiap 15 detik.

## Yang perlu Anda siapkan (rahasia jangan dikirim ke chat)
1. Portal SeaTalk app 41006: aktifkan **Bot**, beri izin *Send Message to Group Chat*, tambahkan bot ke grup.
2. Unduh **SDK Python** dari halaman SDK di portal, lalu `pip install` berkasnya (modul `seatalk_oapi_sdk`).
3. `pip install -r requirements.txt`
4. Google: buat *service account* (Sheets API aktif), unduh JSON-nya, lalu **Share** spreadsheet JOBDESK TL ke email service account (Editor).
5. Isi environment variable:
   - `SEATALK_APP_ID`, `SEATALK_APP_SECRET` (dari Basic Info app 41006)
   - `GOOGLE_APPLICATION_CREDENTIALS` = path berkas JSON service account
   - opsional `BOT_SEATALK_ID` (ID bot sendiri), `SPREADSHEET_ID`
6. `python main.py` (biarkan menyala), lalu di portal: Event Callback -> **WebSocket** -> **Re-verify**,
   dan langganan event *New Mentioned Message From Group Chat* + *New Message Received from Thread*.
7. Matikan/bedakan dulu bot Smart (skill/jadwal) agar tidak ada kartu ganda di grup yang sama.

Tes logika: `python -m unittest -v test_core` (13 tes, tanpa jaringan).

## Catatan
- Komunikasi dengan SeaTalk/Google belum bisa saya uji dari sini (butuh kredensial Anda); logika inti sudah diuji dengan data tiruan.
- Nama method SDK diambil dari dokumentasi; bila SDK yang diunduh berbeda, hanya `main.py` yang perlu disesuaikan.
- Kartu dikirim dulu, baris sheet ditulis di latar belakang (dengan retry) agar cepat.
