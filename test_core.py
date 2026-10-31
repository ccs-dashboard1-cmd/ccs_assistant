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
    def read_candidates(s): return list(s.cands)
    def read_tl_ids(s): return list(s.tls)
    def read_limit_minutes(s): return s.limit
    def append_row(s, r): s.rows.append(r)
    def load_log(s): return s.log

class FakeApi:
    def __init__(s): s.sent, s.ok = [], True
    def send_card(s, g, t, text):
        if s.ok: s.sent.append((g, t, text))
        return s.ok

def ev(eid, sender, msg="m1", thread="", group="G", sent=None, stype=1):
    m = {"message_id": msg, "thread_id": thread, "sender": {"seatalk_id": sender, "sender_type": stype},
         "message_sent_time": int((sent or T0).timestamp())}
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

    def test_escalation_max5_and_expiry(self):
        b, c, s, a = mk()
        b.on_event(ev("e1", "900"), "mention")
        for _ in range(10):
            c.adv(61); b.escalate_tick()
        self.assertEqual(len(a.sent), 5)
        b2, c2, s2, a2 = mk()
        b2.on_event(ev("e1", "900"), "mention")
        c2.adv(41 * 60); b2.escalate_tick(); self.assertEqual(len(a2.sent), 1)

    def test_escalation_skips_used_and_inactive(self):
        b, c, s, a = mk(cands=["1", "3"])
        b.on_event(ev("e1", "900"), "mention")
        c.adv(61); b.escalate_tick()
        self.assertIn("id=3", a.sent[1][2])
        c.adv(61); b.escalate_tick(); self.assertEqual(len(a.sent), 2)

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

if __name__ == "__main__":
    unittest.main()
