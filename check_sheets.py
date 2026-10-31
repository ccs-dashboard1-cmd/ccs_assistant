"""Tes baca-saja koneksi Google Sheets (tidak menulis apa pun)."""
import os
from io_clients import Sheet

sid = os.environ.get("SPREADSHEET_ID", "1hqaCHLZYGX-oN4yWIcK7UbXMpXW7I0913409k3Jq-YQ")
s = Sheet(sid, os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
print("Kandidat TL (PILIH TL, E=OK):", s.read_candidates())
print("ID TL (LIST TL & JOBDESK D2:D13):", s.read_tl_ids())
print("Batas menit (B15):", s.read_limit_minutes())
print("Jumlah baris ASSIST_LOG:", len(s.load_log()))
print("OK: Google Sheets terhubung")
