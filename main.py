"""Titik masuk: jalankan dengan `python main.py`. Rahasia dari environment saja."""
import logging
import os
import subprocess
import threading
import time

from core import Bot
from io_clients import Sheet, SeaTalkApi, env

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ccs")

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1hqaCHLZYGX-oN4yWIcK7UbXMpXW7I0913409k3Jq-YQ")
TICK_SECONDS = 15
UPDATE_CHECK_SECONDS = 300


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=60).stdout.strip()


def update_watcher(sheet):
    """Bila folder ini hasil git clone: cek GitHub tiap 5 menit; ada commit baru -> keluar,
    launcher (JALANKAN_BOT.bat) yang menarik kode baru lalu menjalankan ulang."""
    if not os.path.isdir(".git"):
        return
    while True:
        time.sleep(UPDATE_CHECK_SECONDS)
        try:
            _git("fetch", "--quiet")
            local, remote = _git("rev-parse", "HEAD"), _git("rev-parse", "@{u}")
            bad = open("bad_commit.txt").read().strip() if os.path.exists("bad_commit.txt") else ""
            if remote and remote != local and remote != bad:
                log.info("versi baru di GitHub (%s), restart untuk update", remote[:8])
                for _ in range(20):  # tunggu antrean tulis sheet kosong
                    if sheet.q.empty():
                        break
                    time.sleep(1)
                time.sleep(2)
                os._exit(0)
        except Exception:
            log.exception("cek update gagal")


def main():
    from seatalk_oapi_sdk import Client, EventDispatcher  # dari halaman SDK portal SeaTalk

    app_id, app_secret = env("SEATALK_APP_ID"), env("SEATALK_APP_SECRET")
    sheet = Sheet(SPREADSHEET_ID, env("GOOGLE_APPLICATION_CREDENTIALS"))
    api = SeaTalkApi(app_id, app_secret)
    bot = Bot(sheet, api, own_ids=[os.environ.get("BOT_SEATALK_ID", "")])
    bot.load_state()

    def ticker():
        while True:
            time.sleep(TICK_SECONDS)
            try:
                bot.escalate_tick()
            except Exception:
                log.exception("tick error")

    threading.Thread(target=ticker, daemon=True, name="escalation").start()
    threading.Thread(target=update_watcher, args=(sheet,), daemon=True, name="updater").start()

    dispatcher = (
        EventDispatcher()
        .on_new_mentioned_message_received_from_group_chat(lambda ev: bot.on_event(ev, "mention"))
        .on_new_message_received_from_thread(lambda ev: bot.on_event(ev, "thread"))
    )
    client = Client(app_id=app_id, app_secret=app_secret, dispatcher=dispatcher)
    try:
        client.run()
    finally:
        client.close()


if __name__ == "__main__":
    main()
