"""Klien nyata: SeaTalk Open API (HTTP) dan Google Sheets. Rahasia hanya dari environment."""
from __future__ import annotations

import logging
import os
import queue
import threading
import time

import requests

log = logging.getLogger("ccs")
API = "https://openapi.seatalk.io"


class SeaTalkApi:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id, self.app_secret = app_id, app_secret
        self._token, self._exp = "", 0.0
        self._lock = threading.Lock()
        self.http = requests.Session()

    def _get_token(self, force=False) -> str:
        with self._lock:
            if force or not self._token or time.time() > self._exp - 300:
                r = self.http.post(f"{API}/auth/app_access_token",
                                   json={"app_id": self.app_id, "app_secret": self.app_secret}, timeout=10)
                d = r.json()
                if d.get("code") != 0:
                    raise RuntimeError(f"token gagal, code={d.get('code')}")
                self._token = d["app_access_token"]
                self._exp = float(d.get("expire") or time.time() + 7200)
            return self._token

    def _post(self, path, body, retry_auth=True):
        r = self.http.post(f"{API}{path}", json=body, timeout=10,
                           headers={"Authorization": f"Bearer {self._get_token()}"})
        d = r.json()
        if d.get("code") == 100 and retry_auth:  # token kedaluwarsa
            self._get_token(force=True)
            return self._post(path, body, retry_auth=False)
        return d

    def send_card(self, group_id: str, thread_id: str, text_md: str) -> bool:
        body = {"group_id": group_id, "message": {
            "tag": "interactive_message",
            "interactive_message": {"elements": [
                {"element_type": "description", "description": {"format": 1, "text": text_md}}]},
        }}
        if thread_id:
            body["message"]["thread_id"] = thread_id
        for attempt in (1, 2):  # percobaan ulang hanya untuk galat jaringan (belum terkirim)
            try:
                d = self._post("/messaging/v2/group_chat", body)
            except requests.exceptions.ConnectionError:
                log.warning("koneksi gagal kirim kartu (percobaan %d)", attempt)
                continue
            except Exception:
                log.exception("kirim kartu gagal")  # timeout baca: mungkin sudah terkirim, jangan ulang
                return False
            if d.get("code") == 0:
                return True
            log.warning("kirim kartu ditolak code=%s", d.get("code"))
            return False
        return False


class Sheet:
    """Google Sheets via service account. Penulisan dilakukan di antrean latar belakang
    sehingga kartu tidak menunggu sheet."""

    def __init__(self, spreadsheet_id: str, creds_path: str):
        import gspread
        self.gc = gspread.service_account(filename=creds_path)
        self.sh = self.gc.open_by_key(spreadsheet_id)
        self.log_ws = self.sh.worksheet(os.environ.get("ASSIST_LOG_TAB", "ASSIST_LOG"))
        self.pilih = self.sh.worksheet("PILIH TL")
        self.list_ws = self.sh.worksheet("LIST TL & JOBDESK")
        self.case_ws = self.sh.worksheet("CASE_LOG")
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._writer, daemon=True, name="sheet-writer").start()

    def _writer(self):
        while True:
            ws, row = self.q.get()
            for attempt in range(5):
                try:
                    ws.append_row(row, value_input_option="RAW",
                                           insert_data_option="INSERT_ROWS",
                                           table_range="A1")
                    break
                except Exception:
                    log.exception("tulis ASSIST_LOG gagal (percobaan %d)", attempt + 1)
                    time.sleep(2 ** attempt)
            else:
                log.error("BARIS HILANG, catat manual: %s", row)

    def append_row(self, row):
        self.q.put((self.log_ws, [str(c) for c in row]))

    def append_case(self, row):
        self.q.put((self.case_ws, [str(c) for c in row]))

    def read_candidates(self):
        rows = self.pilih.get("B5:E9")
        out = []
        for r in rows:
            r = r + [""] * (4 - len(r))
            b, e = str(r[0]).strip(), str(r[3]).strip().upper()
            if b.isdigit() and e == "OK":
                out.append(b)
        return out

    def read_tl_ids(self):
        return [str(r[0]).strip() for r in self.list_ws.get("D2:D13") if r and str(r[0]).strip()]

    def read_limit_minutes(self) -> float:
        try:
            return float(self.pilih.get("B15")[0][0])
        except Exception:
            return 5.0

    def load_log(self):
        return self.log_ws.get("A2:H5000")


def env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"Environment variable {name} belum diisi")
    return v
