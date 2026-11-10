"""Logika inti CCS Assistant (tanpa LLM, tanpa dependensi jaringan).

Semua I/O (SeaTalk, Google Sheet, jam) disuntikkan sehingga bisa diuji.
"""
from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

log = logging.getLogger("ccs")

WIB = timezone(timedelta(hours=7))
CARD_TEXT = "Mohon dibantu cek ya"
NO_TL_TEXT = "Belum ada TL yang tersedia untuk di-tag saat ini. Mohon ditunggu."
MAX_MENTIONS_PER_THREAD = 10  # batas total kartu per thread (putaran ulang berhenti di sini)
EXPIRE_MINUTES = 40


def fmt_wib(dt: datetime) -> str:
    return dt.astimezone(WIB).strftime("%Y-%m-%dT%H:%M:%S+07:00")


def parse_wib(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def get(obj, *names, default=""):
    """Ambil atribut/key dari objek SDK atau dict; kembalikan default bila kosong."""
    for n in names:
        if obj is None:
            return default
        if isinstance(obj, dict):
            obj = obj.get(n)
        else:
            obj = getattr(obj, n, None)
    return default if obj is None else obj


CASE_FIELDS = (("l3_reason_code", r"L3\s*Reason\s*Code"), ("session_id", r"Session\s*ID"),
               ("case_id", r"Case\s*ID"), ("related_case", r"Related\s*Case"), ("hc", r"HC"),
               ("issue", r"Issue\s*/?\s*Summary"), ("action", r"Action\s*Taken"))
CORE_KEYS = ("l3_reason_code", "session_id", "case_id", "related_case", "hc")  # menentukan parse_status
# Label lain: hanya sebagai PEMBATAS (isinya tidak disimpan)
_STOP = (r"Data\s*Complaint", r"Username\s*Shopee", r"Nomor\s*Pesanan[^:\n]*", r"Thank\s*You")
MAX_FIELD_CHARS = 1500


def parse_case(text: str) -> dict:
    """Ambil field template Agent. Nilai bisa lebih dari satu baris (diambil sampai label berikutnya).
    Label Data Complaint/Username/Nomor Pesanan-Resi hanya jadi pembatas dan TIDAK disimpan.
    parse_status (OK/PARTIAL/NO_TEMPLATE) hanya dihitung dari 5 field inti."""
    out = {k: "" for k, _ in CASE_FIELDS}
    pats = [(k, re.compile(rf"^[ \t]*{p}[ \t]*:[ \t]*(.*)$", re.I)) for k, p in CASE_FIELDS]
    stops = [re.compile(rf"^[ \t]*{p}[ \t]*(:.*)?$", re.I) for p in _STOP]
    cur, buf = None, []

    def flush():
        if cur and not out[cur]:
            out[cur] = " ".join(x for x in buf if x).strip()[:MAX_FIELD_CHARS]

    for line in (text or "").replace("\r", "").split("\n"):
        hit = next(((k, m.group(1).strip()) for k, rx in pats for m in [rx.match(line)] if m), None)
        if hit:
            flush()
            cur, buf = hit[0], [hit[1]]
        elif any(rx.match(line) for rx in stops):
            flush()
            cur, buf = None, []
        elif cur:
            buf.append(line.strip())
    flush()
    if not out["hc"]:
        m = re.search(r"(?i)\bHCP\s*\d+\b", text or "")
        if m:
            out["hc"] = m.group(0).replace(" ", "").upper()
    filled = sum(1 for k in CORE_KEYS if out[k])
    out["parse_status"] = "NO_TEMPLATE" if filled == 0 else ("OK" if filled == len(CORE_KEYS) else "PARTIAL")
    return out


@dataclass
class Incoming:
    event_id: str
    group_id: str
    message_id: str
    thread_id: str  # kosong bila pesan bukan bagian thread
    sender_id: str
    sender_type: int  # 1 user, 2 bot, 3 system; 0 = tidak diketahui
    sent_at: datetime
    kind: str  # "mention" atau "thread"
    text: str = ""  # teks pesan (untuk CASE_LOG)
    sender_email: str = ""  # email pengirim (untuk CASE_LOG)

    @property
    def thread_key(self) -> str:
        # Pesan induk (belum thread): thread dimulai dari message_id-nya.
        return self.thread_id or self.message_id


def normalize(ev, kind: str, now: datetime) -> Incoming:
    e = get(ev, "event", default=None)
    m = get(e, "message", default=None)
    sent = get(m, "message_sent_time", default=0)
    try:
        sent_dt = datetime.fromtimestamp(int(sent), WIB) if sent else now
    except (TypeError, ValueError, OverflowError):
        sent_dt = now
    return Incoming(
        event_id=str(get(ev, "event_id")),
        group_id=str(get(e, "group_id")),
        message_id=str(get(m, "message_id")),
        thread_id=str(get(m, "thread_id")),
        sender_id=str(get(m, "sender", "seatalk_id")),
        sender_type=int(get(m, "sender", "sender_type", default=0) or 0),
        sent_at=sent_dt,
        kind=kind,
        text=str(get(m, "text", "plain_text")),
        sender_email=str(get(m, "sender", "email")).strip(),
    )


@dataclass
class ThreadState:
    group_id: str
    thread_id: str
    assigned: list = field(default_factory=list)  # [(waktu, tl_id)] berurutan
    acked: bool = False

    @property
    def first_assigned(self) -> datetime | None:
        return self.assigned[0][0] if self.assigned else None

    @property
    def last_assigned(self) -> datetime | None:
        return self.assigned[-1][0] if self.assigned else None

    @property
    def tl_ids(self) -> set:
        return {t for _, t in self.assigned}


class Bot:
    """sheet: objek dengan read_candidates(), read_tl_ids(), read_limit_minutes(),
    append_row(list[str]), load_log() -> list[list[str]].
    api: objek dengan send_card(group_id, thread_id, text_md) -> bool.
    """

    def __init__(self, sheet, api, clock=None, own_ids=(), tl_cache_seconds=30):
        self.sheet = sheet
        self.api = api
        self.clock = clock or (lambda: datetime.now(WIB))
        self.own_ids = {str(i) for i in own_ids if i}
        self.threads: dict[tuple, ThreadState] = {}
        self.seen_events: OrderedDict = OrderedDict()
        self.lock = threading.RLock()
        self._tl_cache = (None, None)
        self._tl_ttl = tl_cache_seconds
        self.logged_cases: OrderedDict = OrderedDict()  # thread yang sudah dicatat ke CASE_LOG (anti dobel)

    # ---------- state ----------
    def load_state(self):
        rows = self.sheet.load_log()
        with self.lock:
            self.threads.clear()
            for r in rows:
                r = list(r) + [""] * (8 - len(r))
                at, status, _, tl, _, _, thr, grp = r[:8]
                if not thr or not grp:
                    continue
                st = self.threads.setdefault((thr, grp), ThreadState(grp, thr))
                if status == "ASSIGNED":
                    t = parse_wib(at)
                    if t:
                        st.assigned.append((t, tl))
                elif status == "ACK_LOGGED":
                    st.acked = True
            for st in self.threads.values():
                st.assigned.sort(key=lambda x: x[0])
        log.info("state dimuat: %d thread", len(self.threads))

    def _dupe(self, event_id: str) -> bool:
        if not event_id:
            return False
        with self.lock:
            if event_id in self.seen_events:
                return True
            self.seen_events[event_id] = 1
            while len(self.seen_events) > 5000:
                self.seen_events.popitem(last=False)
        return False

    def _tl_ids(self) -> set:
        now = self.clock()
        ids, ts = self._tl_cache
        if ids is not None and (now - ts).total_seconds() < self._tl_ttl:
            return ids
        ids = {str(x) for x in self.sheet.read_tl_ids()}
        self._tl_cache = (ids, now)
        return ids

    def _next_in_rotation(self, st: "ThreadState", cands: list):
        """Putar bergilir: pilih kandidat yang paling sedikit sudah di-mention di thread ini;
        seri diurutkan menurut urutan daftar. Semua sudah terpakai -> mulai putaran baru dari atas.
        `cands` dibaca dari Sheet DI LUAR lock (pembacaan Sheet bisa lambat)."""
        if not cands:
            return None, False
        counts = {tl: 0 for tl in cands}
        for _, tl in st.assigned:
            if tl in counts:
                counts[tl] += 1
        best = min(cands, key=lambda tl: (counts[tl], cands.index(tl)))
        return best, counts[best] >= 1  # True = pengingat (TL ini sudah pernah di-mention)

    # ---------- handler event ----------
    def on_event(self, ev, kind: str):
        try:
            inc = normalize(ev, kind, self.clock())
            log.info("event %s: group=%s thread=%s sender=%s type=%s", kind, inc.group_id,
                     inc.thread_key, inc.sender_id, inc.sender_type)
            self.handle(inc)
        except Exception:  # jangan sampai handler menjatuhkan koneksi
            log.exception("gagal memproses event")

    def handle(self, inc: Incoming):
        if self._dupe(inc.event_id):
            return
        if inc.sender_type in (2, 3) or inc.sender_id in self.own_ids:
            return
        if not inc.group_id or not inc.sender_id:
            return
        key = (inc.thread_key, inc.group_id)
        if inc.sender_id in self._tl_ids():
            self._handle_tl(inc, key)
        elif inc.kind == "mention":
            self._handle_human(inc, key)
        # pesan thread dari non-TL tanpa mention: diabaikan

    def _handle_human(self, inc: Incoming, key):
        with self.lock:
            st = self.threads.get(key)
            if st and st.assigned:
                return  # sudah ditugaskan (anti duplikat)
            first = key not in self.logged_cases
            if first:
                self.logged_cases[key] = 1
                while len(self.logged_cases) > 5000:
                    self.logged_cases.popitem(last=False)
        if first:
            self._log_case(inc, self.clock())  # catat DULU, apa pun nasib kartunya
        cands = self.sheet.read_candidates()  # I/O lambat: JANGAN di dalam lock
        with self.lock:
            st = self.threads.get(key)
            if st and st.assigned:
                return  # event lain sudah menugaskan selagi Sheet dibaca
            if not cands:
                tl = None
            else:
                tl = cands[0]
            if tl is None:
                send_no_tl = True
            else:
                send_no_tl = False
                # reservasi dulu di memori agar event ganda tidak lolos
                st = self.threads.setdefault(key, ThreadState(inc.group_id, inc.thread_key))
                now = self.clock()
                st.assigned.append((now, tl))
        if send_no_tl:
            self.api.send_card(inc.group_id, inc.thread_key, NO_TL_TEXT)
            return
        ok = self.api.send_card(inc.group_id, inc.thread_key, card_text(tl))
        if not ok:
            with self.lock:  # kartu gagal: batalkan agar bisa dicoba lagi
                st.assigned.remove((now, tl))
            log.warning("kartu gagal terkirim, tidak dicatat")
            return
        self.sheet.append_row([fmt_wib(now), "ASSIGNED", fmt_wib(now), tl, "", "0", inc.thread_key, inc.group_id])

    def _log_case(self, inc: Incoming, now: datetime):
        """Catat isi template ke CASE_LOG (A-M). Kegagalan di sini TIDAK boleh mengganggu alur utama."""
        try:
            c = parse_case(inc.text)
            self.sheet.append_case([
                fmt_wib(now), inc.thread_key, inc.group_id, inc.sender_id,   # A-D
                c["l3_reason_code"], c["issue"], c["action"],                # E-G
                inc.sender_email,                                            # H
                c["session_id"], c["case_id"], c["related_case"], c["hc"],   # I-L
                c["parse_status"]])                                          # M
        except Exception:
            log.exception("catat CASE_LOG gagal (diabaikan)")

    def _handle_tl(self, inc: Incoming, key):
        with self.lock:
            st = self.threads.get(key)
            if not st or not st.assigned or st.acked:
                return
            st.acked = True
            assigned_at = st.first_assigned
        t = fmt_wib(inc.sent_at)
        self.sheet.append_row([fmt_wib(assigned_at), "ACK_LOGGED", t, inc.sender_id, t, "1", inc.thread_key, inc.group_id])

    # ---------- eskalasi ----------
    def escalate_tick(self):
        """Dipanggil berkala (mis. tiap 15 detik). Aman dipanggil sering."""
        now = self.clock()
        with self.lock:
            pending = [s for s in self.threads.values() if not s.acked and s.assigned
                       and (now - s.last_assigned).total_seconds() / 60 < EXPIRE_MINUTES]
        if not pending:
            return  # tidak ada yang perlu dicek: hemat kuota Sheets
        limit = self.sheet.read_limit_minutes()
        with self.lock:
            due = []
            for st in self.threads.values():
                if st.acked or not st.assigned:
                    continue
                age = (now - st.last_assigned).total_seconds() / 60
                if age < limit or age >= EXPIRE_MINUTES:
                    continue
                if len(st.assigned) >= MAX_MENTIONS_PER_THREAD:
                    continue
                due.append(st)
            due.sort(key=lambda s: s.last_assigned)
        for st in due[:15]:
            try:
                self._escalate(st, now)
            except Exception:
                log.exception("eskalasi gagal")

    def _escalate(self, st: ThreadState, now: datetime):
        with self.lock:
            if st.acked:
                return
        cands = self.sheet.read_candidates()  # I/O lambat: di luar lock agar balasan TL tidak tertahan
        with self.lock:
            if st.acked:  # TL membalas selagi Sheet dibaca -> batalkan, jangan kirim kartu
                return
            tl, reminder = self._next_in_rotation(st, cands)
            if tl is None:
                return
            now = self.clock()  # waktu SEBENARNYA kartu dikirim (bukan awal tick)
            st.assigned.append((now, tl))  # reservasi
        if not self.api.send_card(st.group_id, st.thread_id, card_text(tl, reminder)):
            with self.lock:
                st.assigned.remove((now, tl))
            return
        self.sheet.append_row([fmt_wib(now), "ASSIGNED", fmt_wib(now), tl, "", "0", st.thread_id, st.group_id])


def card_text(tl_id: str, reminder: bool = False) -> str:
    head = CARD_TEXT + (" (pengingat)" if reminder else "")
    return f'{head}, <mention-tag target="seatalk://user?id={tl_id}"/>'
