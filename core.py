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
_STOP = (r"Data\s*(?:Complaint|User)", r"Username[^:\n]*", r"Nomor\s*Pesanan[^:\n]*", r"Thank\s*You", r"Terima\s*Kasih")
# label pembatas yang menempel di tengah baris (mis. "...hasil tim kak Data User: Username Sosmed: x"): potong dari situ
_CUT = re.compile(r"\s*\b(?:Data\s*(?:Complaint|User)\s*:|Username\s*\w*\s*:|Nomor\s*Pesanan[^:\n]*:).*$", re.I)
MAX_FIELD_CHARS = 1500
# awalan di depan label yang diabaikan: spasi, tanda -, –, —, •, *, >, atau penomoran "1." / "2)"
_LEAD = r"[ \t]*(?:[-\u2013\u2014\u2022*>\u00b7]+|\d+[.)])?[ \t]*"
_IDS = ("session_id", "case_id", "related_case")  # berisi nomor panjang; teks tambahan dibuang
_NUM = re.compile(r"\d{10,}")


# ---------- pencocok L3 Reason Code (logika sama dengan skrip Apps Script l3.gs) ----------
_ZW = re.compile("[\u200b\u200c\u200d\ufeff\u00a0]")
_SESS = re.compile(r"(?:session\s*id|season\s*id|sesi|case/session)", re.I)
_L3_PREFIX = re.compile(r"^\s*(?:l3(?:\s*reason\s*code)?\s*[-:\u2013\u2014.)]*|[-\u2013\u2014\u2022*>\u00b7]+|\d+[.)])\s*", re.I)


def _norm(s) -> str:
    return re.sub(r"\s+", " ", _ZW.sub(" ", str(s))).strip().lower()


def _clean(s) -> str:
    """Buang Session ID yang menempel dan awalan seperti 'L3-', 'L3:', '-', '*', '1.' sebelum dicocokkan."""
    t = _SESS.split(_ZW.sub(" ", str(s)))[0]
    t = re.sub(r"\d{10,}", "", t)
    for _ in range(4):
        u = _L3_PREFIX.sub("", t, count=1)
        if u == t:
            break
        t = u
    return _norm(t)


def _ratio(a: str, b: str) -> float:
    la, lb = len(a), len(b)
    if not la and not lb:
        return 1.0
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return 1 - prev[lb] / max(la, lb)


class L3Matcher:
    """Cocokkan L3 dari agen ke daftar resmi (sheet 'Reason Code' kolom D).
    match() -> (remarks, final): ('Sesuai', nama) | ('Dikoreksi', nama resmi) | ('Tidak cocok', '') | ('', '').
    Diganti hanya bila SANGAT yakin (kemiripan >= 0,9 dan unggul >= 0,03 dari kandidat kedua)."""
    AUTO = 0.9
    MARGIN = 0.03

    def __init__(self, names):
        self.canon: dict = {}
        for n in names:
            n = str(n).strip()
            if n and _norm(n) not in self.canon:
                self.canon[_norm(n)] = n
        self._cache: dict = {}

    def __bool__(self):
        return bool(self.canon)

    def match(self, raw) -> tuple:
        v = str(raw)
        if not v.strip():
            return "", ""
        if v == self.canon.get(_norm(v)):
            return "Sesuai", v
        hit = self._best(v)
        return ("Dikoreksi", hit) if hit else ("Tidak cocok", "")

    def _best(self, v: str) -> str:
        if v in self._cache:
            return self._cache[v]
        c = _clean(v)
        hit = ""
        if c:
            if c in self.canon:
                hit = self.canon[c]
            else:
                b1, b1k, b2 = 0.0, "", 0.0
                for k in self.canon:
                    lo, hi = sorted((len(c), len(k)))
                    if hi == 0 or lo / hi < 0.87:   # tak mungkin cukup mirip: lewati (hemat waktu)
                        continue
                    s = _ratio(c, k)
                    if s > b1:
                        b1, b1k, b2 = s, k, b1
                    elif s > b2:
                        b2 = s
                if b1 >= self.AUTO and b1 - b2 >= self.MARGIN:
                    hit = self.canon[b1k]
        if len(self._cache) > 2000:
            self._cache.clear()
        self._cache[v] = hit
        return hit



def _hc_norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", _ZW.sub(" ", str(s)).lower())


_HC_TOKEN = re.compile(r"hcp\s*([1-3])([^,;\n(]*)", re.I)


class HcMatcher:
    """Cocokkan HC dari agen ke 'LIST HARD COMPLAINT' (sheet 'RC & HC' kolom F); level HCP harus sama.
    match() -> nama HC resmi, atau '' bila tidak cocok / bukan HC. Logika sama dengan skrip Apps Script l3.gs."""

    def __init__(self, names):
        self.canon: dict = {}
        for n in names:
            n = str(n).strip()
            if n and _hc_norm(n) not in self.canon:
                self.canon[_hc_norm(n)] = n

    def __bool__(self):
        return bool(self.canon)

    def match(self, raw) -> str:
        raw = str(raw)
        if re.search(r"isi\s+dengan\s+format", raw, re.I):
            return ""  # teks petunjuk template, bukan jawaban
        m = _HC_TOKEN.search(raw)
        if not m:
            return ""
        n = _hc_norm("hcp" + m.group(1) + m.group(2))
        if n in self.canon:
            return self.canon[n]
        lvl = "hcp" + m.group(1)
        cand = [k for k in self.canon if k.startswith(lvl)]
        pre = ""
        for k in cand:
            if len(k) >= 8 and n.startswith(k) and len(k) > len(pre):
                pre = k
        if pre:
            return self.canon[pre]
        b1, b1k, b2 = 0.0, "", 0.0
        for k in cand:
            s = _ratio(n, k)
            if s > b1:
                b1, b1k, b2 = s, k, b1
            elif s > b2:
                b2 = s
        if b1 >= 0.65 and b1 - b2 >= 0.15:
            return self.canon[b1k]
        return ""



def parse_case(text: str) -> dict:
    """Ambil field template Agent. Nilai bisa lebih dari satu baris (diambil sampai label berikutnya).
    Label Data Complaint/Data User/Username (Shopee/Sosmed)/Nomor Pesanan-Resi hanya jadi pembatas dan TIDAK disimpan.
    parse_status (OK/PARTIAL/NO_TEMPLATE) hanya dihitung dari 5 field inti."""
    out = {k: "" for k, _ in CASE_FIELDS}
    pats = [(k, re.compile(rf"^{_LEAD}{p}[ \t]*:[ \t]*(.*)$", re.I)) for k, p in CASE_FIELDS]
    stops = [re.compile(rf"^{_LEAD}{p}[ \t]*(:.*)?$", re.I) for p in _STOP]
    cur, buf = None, []

    def flush():
        if cur and not out[cur]:
            out[cur] = _CUT.sub("", " ".join(x for x in buf if x)).strip()[:MAX_FIELD_CHARS]

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
    for k in _IDS:  # "Sesi: 2121…546 / 10 November, 18:02:59." -> "2121…546"; "-" / teks tanpa nomor dibiarkan
        nums = _NUM.findall(out[k])
        if nums:
            out[k] = ", ".join(nums)
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
        self._l3 = L3Matcher([])
        self._hc = HcMatcher([])
        self._l3_next = None  # kapan daftar Reason Code dimuat ulang
        self._l3_ttl = 600

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

    def _l3_matcher(self, now: datetime) -> L3Matcher:
        """Daftar Reason Code dimuat dari sheet dan disegarkan tiap 10 menit. Gagal -> pakai yang lama."""
        if self._l3_next is None or now >= self._l3_next:
            ok = False
            try:
                fn = getattr(self.sheet, "read_reason_codes", None)
                names = fn() if fn else []
                if names:
                    self._l3, ok = L3Matcher(names), True
            except Exception:
                log.exception("baca Reason Code gagal (pakai daftar lama)")
            try:
                fn = getattr(self.sheet, "read_hc_list", None)
                hcs = fn() if fn else []
                if hcs:
                    self._hc = HcMatcher(hcs)
            except Exception:
                log.exception("baca daftar HC gagal (pakai daftar lama)")
            self._l3_next = now + timedelta(seconds=self._l3_ttl if ok else 60)
        return self._l3

    def _log_case(self, inc: Incoming, now: datetime):
        """Catat isi template ke CASE_LOG (A-M, +N/O/P bila daftar RC & HC tersedia).
        Kolom E = isi asli dari agen. N = Sesuai/Dikoreksi/Tidak cocok. O = L3 resmi. P = HC resmi.
        Kegagalan di sini TIDAK boleh mengganggu alur utama."""
        try:
            c = parse_case(inc.text)
            row = [
                fmt_wib(now), inc.thread_key, inc.group_id, inc.sender_id,   # A-D
                c["l3_reason_code"], c["issue"], c["action"],                # E-G
                inc.sender_email,                                            # H
                c["session_id"], c["case_id"], c["related_case"], c["hc"],   # I-L
                c["parse_status"]]                                           # M
            try:
                m = self._l3_matcher(now)
                no = list(m.match(c["l3_reason_code"])) if m else None       # N, O
                hc = self._hc.match(c["hc"]) if self._hc else None           # P
                if hc is not None:
                    row += (no or ["", ""]) + [hc]
                elif no is not None:
                    row += no
            except Exception:
                log.exception("cocokkan L3/HC gagal (diabaikan)")
                del row[13:]
            self.sheet.append_case(row)
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
