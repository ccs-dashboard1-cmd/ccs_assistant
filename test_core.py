import unittest
from datetime import datetime, timedelta
from core import Bot, WIB

T0 = datetime(2026, 10, 31, 10, 0, 0, tzinfo=WIB)

class Clock:
    def __init__(s): s.t = T0
    def __call__(s): return s.t
    def adv(s, sec): s.t += timedelta(seconds=sec)

class FakeSheet:
    def __init__(s, cands=("1", "2", "3", "4", "5", "6"), tls=("1", "2", "3", "4", "5", "6"), limit=1):
        s.cands, s.tls, s.limit, s.rows, s.log = list(cands), list(tls), limit, [], []
        s.cases = []
    def read_candidates(s): return list(s.cands)
    def read_tl_ids(s): return list(s.tls)
    def read_limit_minutes(s): return s.limit
    def append_row(s, r): s.rows.append(r)
    def append_case(s, r): s.cases.append(r)
    def load_log(s): return s.log

class FakeApi:
    def __init__(s): s.sent, s.ok = [], True
    def send_card(s, g, t, text):
        if s.ok: s.sent.append((g, t, text))
        return s.ok

def ev(eid, sender, msg="m1", thread="", group="G", sent=None, stype=1, text="", email=""):
    m = {"message_id": msg, "thread_id": thread, "sender": {"seatalk_id": sender, "sender_type": stype, "email": email},
         "message_sent_time": int((sent or T0).timestamp()), "text": {"plain_text": text}}
    return {"event_id": eid, "event": {"group_id": group, "message": m}}

def mk(**kw):
    c, s, a = Clock(), FakeSheet(**kw), FakeApi()
    return Bot(s, a, clock=c), c, s, a

class T(unittest.TestCase):
    def test_human_assigns_first_candidate(self):
        b, c, s, a = mk()
        b.on_event(ev("e1", "900"), "mention")
        self.assertEqual(len(a.sent), 1)
        self.assertIn("id=1", a.sent[0][2]); self.assertEqual(a.sent[0][1], "m1")
        self.assertEqual(s.rows[0][1:], ["ASSIGNED", s.rows[0][2], "1", "", "0", "m1", "G"])

    def test_duplicate_event_and_second_message_in_thread(self):
        b, c, s, a = mk()
        b.on_event(ev("e1", "900"), "mention")
        b.on_event(ev("e1", "900"), "mention")
        b.on_event(ev("e2", "901", msg="m2", thread="m1"), "mention")
        self.assertEqual(len(a.sent), 1); self.assertEqual(len(s.rows), 1)

    def test_tl_reply_logs_ack_once(self):
        b, c, s, a = mk()
        b.on_event(ev("e1", "900"), "mention")
        c.adv(20)
        b.on_event(ev("e2", "1", msg="m2", thread="m1", sent=c.t), "thread")
        b.on_event(ev("e3", "2", msg="m3", thread="m1", sent=c.t), "thread")
        acks = [r for r in s.rows if r[1] == "ACK_LOGGED"]
        self.assertEqual(len(acks), 1)
        self.assertEqual(acks[0][0], s.rows[0][0]); self.assertEqual(acks[0][3], "1"); self.assertEqual(acks[0][5], "1")

    def test_tl_message_without_assignment_ignored(self):
        b, c, s, a = mk()
        b.on_event(ev("e1", "1", msg="x", thread="zz"), "thread")
        self.assertEqual(s.rows, [])

    def test_tl_mention_only_no_assignment(self):
        b, c, s, a = mk()
        b.on_event(ev("e1", "2", msg="x"), "mention")
        self.assertEqual((s.rows, a.sent), ([], []))

    def test_bot_and_nonmention_human_ignored(self):
        b, c, s, a = mk()
        b.on_event(ev("e1", "900", stype=2), "mention")
        b.on_event(ev("e2", "900"), "thread")
        self.assertEqual((s.rows, a.sent), ([], []))

    def test_no_candidates(self):
        b, c, s, a = mk(cands=[])
        b.on_event(ev("e1", "900"), "mention")
        self.assertEqual(len(a.sent), 1); self.assertIn("Belum ada TL", a.sent[0][2]); self.assertEqual(s.rows, [])

    def test_card_failure_not_logged_and_retryable(self):
        b, c, s, a = mk()
        a.ok = False
        b.on_event(ev("e1", "900"), "mention")
        self.assertEqual(s.rows, [])
        a.ok = True
        b.on_event(ev("e2", "900"), "mention")
        self.assertEqual(len(s.rows), 1)

    def test_escalation_after_limit_and_stops_after_ack(self):
        b, c, s, a = mk()
        b.on_event(ev("e1", "900"), "mention")
        c.adv(30); b.escalate_tick(); self.assertEqual(len(a.sent), 1)
        c.adv(31); b.escalate_tick()
        self.assertEqual(len(a.sent), 2); self.assertIn("id=2", a.sent[1][2]); self.assertEqual(a.sent[1][1], "m1")
        b.on_event(ev("e2", "2", msg="r", thread="m1", sent=c.t), "thread")
        c.adv(120); b.escalate_tick(); self.assertEqual(len(a.sent), 2)

    def test_loop_round_robin_then_reminder(self):
        b, c, s, a = mk(cands=["1", "2", "3"])
        b.on_event(ev("e1", "900"), "mention")
        for _ in range(5):
            c.adv(61); b.escalate_tick()
        ids = [x[2].split("id=")[1][:1] for x in a.sent]
        self.assertEqual(ids, ["1", "2", "3", "1", "2", "3"])
        self.assertNotIn("pengingat", a.sent[1][2]); self.assertNotIn("pengingat", a.sent[2][2])
        self.assertIn("pengingat", a.sent[3][2])

    def test_loop_stops_at_cap_and_expiry(self):
        import core
        b, c, s, a = mk(cands=["1", "2", "3"])
        b.on_event(ev("e1", "900"), "mention")
        for _ in range(30):
            c.adv(61); b.escalate_tick()
        self.assertEqual(len(a.sent), core.MAX_MENTIONS_PER_THREAD)
        b2, c2, s2, a2 = mk()
        b2.on_event(ev("e1", "900"), "mention")
        c2.adv(41 * 60); b2.escalate_tick(); self.assertEqual(len(a2.sent), 1)

    def test_loop_stops_after_ack_in_later_round(self):
        b, c, s, a = mk(cands=["1", "2"])
        b.on_event(ev("e1", "900"), "mention")
        for _ in range(3):
            c.adv(61); b.escalate_tick()
        self.assertEqual(len(a.sent), 4)
        b.on_event(ev("e2", "1", msg="r", thread="m1", sent=c.t), "thread")
        c.adv(300); b.escalate_tick(); self.assertEqual(len(a.sent), 4)
        self.assertEqual(len([r for r in s.rows if r[1] == "ACK_LOGGED"]), 1)

    def test_rotation_skips_inactive_then_loops(self):
        b, c, s, a = mk(cands=["1", "3"])
        b.on_event(ev("e1", "900"), "mention")
        c.adv(61); b.escalate_tick()
        self.assertIn("id=3", a.sent[1][2])
        c.adv(61); b.escalate_tick(); self.assertEqual(len(a.sent), 3)
        self.assertIn("id=1", a.sent[2][2]); self.assertIn("pengingat", a.sent[2][2])

    def test_ack_during_slow_sheet_read_cancels_reminder(self):
        """Kasus nyata: TL membalas saat bot sedang membaca Sheet untuk kartu pengingat."""
        b, c, s, a = mk()
        b.on_event(ev("e1", "900"), "mention")          # kartu ke TL 1
        c.adv(70)                                        # lewat batas (limit=1 menit)
        orig = s.read_candidates
        def slow_read():
            # selagi Sheet dibaca, TL 2 membalas di thread
            b.on_event(ev("e2", "2", msg="m2", thread="m1", sent=c.t), "thread")
            return orig()
        s.read_candidates = slow_read
        b.escalate_tick()
        self.assertEqual(len(a.sent), 1)                 # tidak ada kartu pengingat
        self.assertEqual([r[1] for r in s.rows], ["ASSIGNED", "ACK_LOGGED"])

    def test_escalation_timestamp_is_send_time(self):
        b, c, s, a = mk()
        b.on_event(ev("e1", "900"), "mention")
        c.adv(70)
        orig = s.read_candidates
        def slow_read():
            c.adv(100)                                   # Sheet lambat 100 detik
            return orig()
        s.read_candidates = slow_read
        b.escalate_tick()
        self.assertEqual(len(a.sent), 2)
        self.assertEqual(s.rows[1][0], s.rows[1][2])
        self.assertIn("T10:02:50", s.rows[1][0])         # 10:00:00 + 70 + 100 detik

    def test_restart_reloads_state(self):
        b, c, s, a = mk()
        s.log = [["2026-10-31T10:00:00+07:00", "ASSIGNED", "x", "1", "", "0", "m1", "G"]]
        b.load_state()
        b.on_event(ev("e1", "900", msg="m9", thread="m1"), "mention")
        self.assertEqual(a.sent, [])
        c.adv(61); b.escalate_tick(); self.assertIn("id=2", a.sent[0][2])

    def test_threads_independent_by_group(self):
        b, c, s, a = mk()
        b.on_event(ev("e1", "900", group="G1"), "mention")
        b.on_event(ev("e2", "900", group="G2"), "mention")
        self.assertEqual(len(a.sent), 2)

TPL = ("Hi Team @CCS Assistant, Mohon bantuan arahan lebih lanjut untuk kendala\n\n"
       "L3 Reason Code : Pickup Delay\nSession ID : S123\nCase ID : C456\nRelated Case : C111\nHC : HCP3\n"
       "Issue/Summary : paket belum diambil\nAction Taken :\n\nData Complaint\nUsername Shopee : budi\n"
       "Nomor Pesanan / Nomor Resi : SPX999\n\nThank You")

class TCase(unittest.TestCase):
    def test_parse_full_template(self):
        from core import parse_case
        c = parse_case(TPL)
        self.assertEqual((c["l3_reason_code"], c["session_id"], c["case_id"], c["related_case"], c["hc"], c["parse_status"]),
                         ("Pickup Delay", "S123", "C456", "C111", "HCP3", "OK"))
        self.assertEqual((c["issue"], c["action"]), ("paket belum diambil", ""))
        self.assertNotIn("budi", str(c)); self.assertNotIn("SPX999", str(c))

    def test_parse_partial_empty_value_does_not_swallow_next_label(self):
        from core import parse_case
        c = parse_case("L3 Reason Code :\nSession ID : S1\nCase ID :\nHC : HCP1")
        self.assertEqual((c["l3_reason_code"], c["session_id"], c["case_id"], c["hc"], c["parse_status"]), ("", "S1", "", "HCP1", "PARTIAL"))

    def test_parse_no_template_and_hc_fallback(self):
        from core import parse_case
        self.assertEqual(parse_case("halo tolong bantu")["parse_status"], "NO_TEMPLATE")
        self.assertEqual(parse_case("kendala hcp 3 nih")["hc"], "HCP3")

    def test_case_logged_on_mention(self):
        b, c, s, a = mk()
        b.on_event(ev("e1", "900", text=TPL, email="a.b@shopee.com"), "mention")
        self.assertEqual(len(s.cases), 1)
        r = s.cases[0]
        self.assertEqual(len(r), 13)
        self.assertEqual(r[1:4], ["m1", "G", "900"])
        self.assertEqual(r[4:7], ["Pickup Delay", "paket belum diambil", ""])      # E L3, F Issue, G Action
        self.assertEqual(r[7], "a.b@shopee.com")                                    # H email
        self.assertEqual(r[8:], ["S123", "C456", "C111", "HCP3", "OK"])            # I..M

    def test_case_logged_even_when_no_tl(self):
        b, c, s, a = mk(cands=())
        b.on_event(ev("e1", "900", text=TPL), "mention")
        self.assertEqual(len(s.cases), 1); self.assertEqual(s.rows, [])
        self.assertEqual(len(a.sent), 1)  # balasan "tidak ada TL"

    def test_case_logged_even_when_card_fails(self):
        b, c, s, a = mk()
        a.ok = False
        b.on_event(ev("e1", "900", text=TPL), "mention")
        self.assertEqual(len(s.cases), 1); self.assertEqual(s.rows, [])

    def test_case_logged_once_per_thread_across_retries(self):
        b, c, s, a = mk()
        a.ok = False
        b.on_event(ev("e1", "900", text=TPL), "mention")
        a.ok = True
        b.on_event(ev("e2", "900", text=TPL), "mention")   # event baru, thread sama (kartu gagal tadi dicoba lagi)
        self.assertEqual(len(s.cases), 1); self.assertEqual(len(s.rows), 1)

    def test_case_log_failure_does_not_break_flow(self):
        b, c, s, a = mk()
        def boom(r): raise RuntimeError("x")
        s.append_case = boom
        b.on_event(ev("e1", "900", text=TPL), "mention")
        self.assertEqual(len(a.sent), 1); self.assertEqual(len(s.rows), 1)

    def test_no_case_row_for_duplicate_or_tl_reply(self):
        b, c, s, a = mk()
        b.on_event(ev("e1", "900", text=TPL), "mention")
        b.on_event(ev("e1", "900", text=TPL), "mention")
        b.on_event(ev("e2", "1", msg="m2", thread="m1", text=TPL), "thread")
        self.assertEqual(len(s.cases), 1)

    def test_data_user_and_username_sosmed_not_in_action(self):
        from core import parse_case
        c = parse_case("L3 Reason Code : X\nIssue/Summary : i\nAction Taken : izin linking dan edu hasil tim kak\nData User: Username Sosmed: kesha_ayres\nThank You")
        self.assertEqual(c["action"], "izin linking dan edu hasil tim kak")
        c = parse_case("L3 Reason Code : X\nAction Taken : izin linking dan edu hasil tim kak Data User: Username Sosmed: kesha_ayres")
        self.assertEqual(c["action"], "izin linking dan edu hasil tim kak")
        c = parse_case("Action Taken : cek data user tidak sesuai, minta data username baru")
        self.assertEqual(c["action"], "cek data user tidak sesuai, minta data username baru")

    def test_template_terbaru_terima_kasih(self):
        from core import parse_case
        txt = ("Hi Team @CCS Assistant, Mohon bantuan arahan lebih lanjut untuk kendala\n\n"
               "L3 Reason Code : Withdrawal is not received in bank account\nSession ID : -\nCase ID : 2119711512482476111\n"
               "Related Case : -\nHC : #HCP1SocialMedia\nIssue/Summary : user terkendala transfer\n"
               "Action Taken : apakah bisa dibantu treat eskalasi ke ID - SWAT CS - HCP1\n\n"
               "Data User\nUsername Shopee : -\nNomor Pesanan / Nomor Resi : -\n\nTerima Kasih")
        c = parse_case(txt)
        self.assertEqual(c["action"], "apakah bisa dibantu treat eskalasi ke ID - SWAT CS - HCP1")
        self.assertEqual((c["l3_reason_code"], c["case_id"], c["session_id"], c["parse_status"]),
                         ("Withdrawal is not received in bank account", "2119711512482476111", "-", "OK"))
        c = parse_case("Action Taken : tindak lanjut\nTerima Kasih")
        self.assertEqual(c["action"], "tindak lanjut")

    def test_multiline_issue_and_action(self):
        from core import parse_case
        c = parse_case("L3 Reason Code : X\nIssue/Summary : baris satu\nlanjut baris dua\n\nlanjut tiga\n"
                       "Action Taken : eskalasi ke ID - SWAT\nCS - HCP1\n\nData Complaint\nUsername Shopee : budi\nThank You")
        self.assertEqual(c["issue"], "baris satu lanjut baris dua lanjut tiga")
        self.assertEqual(c["action"], "eskalasi ke ID - SWAT CS - HCP1")
        self.assertNotIn("budi", str(c)); self.assertNotIn("Thank", str(c))

    def test_status_ignores_issue_action(self):
        from core import parse_case
        self.assertEqual(parse_case("Issue/Summary : a\nAction Taken : b")["parse_status"], "NO_TEMPLATE")


if __name__ == "__main__":
    unittest.main()
