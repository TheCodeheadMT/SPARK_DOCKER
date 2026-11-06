"""Generate the synthetic Blackboard Ultra demo course (no real people or grades).

Writes a Full Gradebook and a Grade History export in Ultra's CSV layout for the
made-up course 202620-DEMO-CS101 (54 students in sections M3A, T2A and T3A):

    backend/tests/fixtures/demo_gradebook.csv, demo_grade_history.csv   (tests)
    dev_share/SPARK/202620-DEMO-CS101/...                                (demo mount)

Everything is generated from a fixed random seed: names (Demo Student0001...),
usernames (stu0001...), student IDs (9000xxxx), scores, timestamps and receipt
numbers. Re-running gives byte-identical files. The tests depend on the structure
(roster, sections, which items are active, auto-zeros), so change it with care.

    python backend/tests/fixtures/make_demo_course.py
"""
from __future__ import annotations

import csv
import io
import random
from datetime import datetime, timedelta
from pathlib import Path

SEED = 20270301
COURSE = "202620-DEMO-CS101"
EXPORT_DAY = datetime(2027, 3, 1, 12, 0)
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
HIST_FMT = "%b %d, %Y %I:%M:%S %p"

# section -> number of students (stu0001.. assigned round-robin-ish, see roster())
SECTIONS = {"M3A": 20, "T2A": 22, "T3A": 12}

# name, category, points, column id, due date (None = not scheduled yet), how it is graded
#   online: students submit, auto graded, Ultra writes automatic zeros when it closes
#   inclass: instructor enters the score (no submission time, no automatic zeros)
ITEMS = [
    ("Homework 1", "Homework", 20, 710001, "2026-12-18", "online"),
    ("Quiz 1", "Quiz", 30, 710101, "2026-12-23", "online"),
    ("Homework 2", "Homework", 20, 710002, "2026-12-25", "online"),
    ("Quiz 2", "Quiz", 30, 710102, "2026-12-30", "online"),
    ("Graded Review 1", "Exam", 100, 710201, "2027-01-06", "inclass"),
    ("Homework 3", "Homework", 20, 710003, "2027-01-08", "online"),
    ("Quiz 3", "Quiz", 30, 710103, "2027-01-13", "online"),
    ("Homework 4", "Homework", 20, 710004, "2027-01-15", "online"),
    ("Quiz 4", "Quiz", 30, 710104, "2027-01-20", "online"),
    ("Graded Review 2", "Exam", 100, 710202, "2027-01-27", "inclass"),
    ("Homework 5", "Homework", 20, 710005, "2027-01-29", "online"),
    ("Quiz 5", "Quiz", 30, 710105, "2027-02-03", "online"),
    ("Homework 6", "Homework", 20, 710006, "2027-02-05", "online"),
    ("Quiz 6", "Quiz", 30, 710106, "2027-02-10", "online"),
    ("Graded Review 3", "Exam", 100, 710203, "2027-02-17", "inclass"),
    ("Homework 7", "Homework", 20, 710007, "2027-02-19", "online"),
    ("Quiz 7", "Quiz", 30, 710107, "2027-02-24", "online"),          # a few still ungraded
    ("Homework 8", "Homework", 20, 710008, "2027-03-05", "early"),    # open; 3 early submissions
    ("Quiz 8", "Quiz", 30, 710108, None, "future"),
    ("Final Project Design", "Assignment", 25, 720001, None, "needs_grading"),
    ("Final Project Checkpoint", "Assignment", 50, 720002, None, "future"),
    ("Homework 9", "Homework", 0, 710009, None, "future"),
    ("Quiz 9", "Quiz", 30, 710109, None, "future"),
    ("Final Project Capstone", "Assignment", 150, 720003, None, "future"),
    ("Final Project Presentation", "Assignment", 25, 720004, None, "future"),
]
TOTAL_PTS = sum(p for _, _, p, *_ in ITEMS)
STAFF = ("Demo Instructor", "instructor1")
SYSTEM = ("Demo System", "system")
LETTERS = [(93, "A"), (90, "A-"), (87, "B+"), (83, "B"), (80, "B-"), (77, "C+"), (73, "C"), (70, "C-"),
           (67, "D+"), (60, "D"), (0, "F")]


def letter(pct: float) -> str:
    return next(l for cut, l in LETTERS if pct >= cut)


def fmt_score(v: float) -> str:
    return f"{v:.2f}"


def roster(rng: random.Random) -> list[dict]:
    secs = [s for s, n in SECTIONS.items() for _ in range(n)]
    rng.shuffle(secs)
    # The tests look at stu0001 and stu0005: keep both in M3A
    for k in (0, 4):
        if secs[k] != "M3A":
            j = next(j for j in range(5, len(secs)) if secs[j] == "M3A")
            secs[k], secs[j] = secs[j], secs[k]
    out = []
    for i, sec in enumerate(secs, start=1):
        out.append({
            "username": f"stu{i:04d}", "last": f"Student{i:04d}", "first": "Demo",
            "sis": f"9000{i:04d}", "section": sec,
            "ability": min(0.99, max(0.83, rng.gauss(0.88, 0.05))),    # typical fraction of points earned
            "reliability": min(1.0, max(0.85, rng.gauss(0.95, 0.04))), # chance of submitting on time
        })
    # stu0005 is the one struggling student (below 70%, misses and late work)
    out[4].update(ability=0.66, reliability=0.70)
    return out


def main() -> None:
    rng = random.Random(SEED)
    students = roster(rng)
    scores: dict[tuple[str, str], str] = {}
    hist: list[dict] = []

    def event(when, item, cat, editor, role, stu, typ, value, ev, attempt="", receipt="", activity=""):
        hist.append({"Date": when, "Column": item, "Grade Category": cat,
                     "Last Edited by: Name": editor[0], "Last Edited by: Username": editor[1],
                     "Last Edited by: Role": role, "User": f"{stu['first']} {stu['last']}",
                     "Username": stu["username"], "Submission Attempt Number": attempt,
                     "Submission Receipt Number": receipt, "Type": typ, "Value": value,
                     "Attempt Activity": activity, "Event": ev, "Formative Indicator": ""})

    def earn(stu, pts):
        frac = min(1.0, max(0.0, rng.gauss(stu["ability"], 0.08)))
        return round(pts * frac * 2) / 2      # half points

    for name, cat, pts, _id, due_s, kind in ITEMS:
        due = datetime.fromisoformat(due_s) + timedelta(hours=23, minutes=59) if due_s else None
        if kind in ("future",):
            continue
        if kind == "needs_grading":
            # one two-person group has submitted; not graded yet
            for stu in students[31:33]:
                t = datetime(2027, 2, 23, 11, 14, rng.randint(0, 59))
                event(t, name, cat, (f"Demo {students[31]['last']}", students[31]["username"]), "S", stu, "Grade",
                      "", "Group attempt submitted.", "1", "%030x" % rng.getrandbits(120), t)
                scores[(stu["username"], name)] = "Needs Grading"
            continue
        if kind == "early":
            for stu in rng.sample(students, 3):
                t = datetime(2027, 2, 26, 8) + timedelta(minutes=rng.randint(0, 3 * 24 * 60))
                v = earn(stu, pts)
                event(t, name, cat, (f"{stu['first']} {stu['last']}", stu["username"]), "S", stu, "Grade",
                      f"{v:g}", "Attempt submitted (auto graded)", "1", "%030x" % rng.getrandbits(120), t)
                scores[(stu["username"], name)] = fmt_score(v)
            continue
        if kind == "inclass":
            graded = due + timedelta(days=rng.randint(1, 3), hours=-8)
            for stu in students:
                v = earn(stu, pts)
                t = graded + timedelta(seconds=rng.randint(0, 3600))
                event(t, name, cat, STAFF, "P", stu, "Override", f"{v:g}", "Override Grade")
                event(t + timedelta(days=1), name, cat, STAFF, "P", stu, "Override", f"{v:g}", "Grade Posted")
                scores[(stu["username"], name)] = fmt_score(v)
            continue

        # online item
        ungraded = set()
        if name == "Quiz 7":   # five submissions still in progress at export time (stu0005 is one)
            ungraded = {"stu0005", *[s["username"] for s in rng.sample(students[5:], 4)]}
        # one guaranteed miss per item (so it gets an automatic zero), a different student each time
        forced_miss = None if name == "Homework 1" else students[(7 * len(scores) // 50 + 11) % len(students)]["username"]
        burst = due + timedelta(minutes=2)
        for stu in students:
            if stu["username"] in ungraded:
                continue
            on_time = name == "Homework 1" or (stu["username"] != forced_miss and rng.random() < stu["reliability"])
            if on_time:
                t = due - timedelta(minutes=rng.randint(5, 6 * 24 * 60))
                v = earn(stu, pts)
                event(t + timedelta(seconds=rng.randint(1, 40)), name, cat, (f"{stu['first']} {stu['last']}",
                      stu["username"]), "S", stu, "Grade", f"{v:g}", "Attempt submitted (auto graded)",
                      str(rng.randint(1, 3)), "%030x" % rng.getrandbits(120), t)
                scores[(stu["username"], name)] = fmt_score(v)
                continue
            # missed the deadline: Ultra writes an automatic zero just after it closes
            event(burst + timedelta(seconds=rng.randint(0, 90)), name, cat, SYSTEM, "", stu, "Override", "0",
                  "Override Grade")
            final = 0.0
            if rng.random() < 0.5:   # submitted late; the instructor replaces the zero
                t = due + timedelta(hours=rng.randint(2, 72))
                v = earn(stu, pts)
                event(t, name, cat, (f"{stu['first']} {stu['last']}", stu["username"]), "S", stu, "Grade",
                      f"{v:g}", "Attempt submitted (auto graded)", "1", "%030x" % rng.getrandbits(120), t)
                t2 = t + timedelta(hours=rng.randint(4, 48))
                event(t2, name, cat, STAFF, "P", stu, "Override", "", "Override Grade Cleared")
                event(t2 + timedelta(seconds=5), name, cat, STAFF, "P", stu, "Override", f"{v:g}", "Override Grade")
                final = v
            scores[(stu["username"], name)] = fmt_score(final)

    # ---- gradebook ----
    def header(name, pts, cid, disp="Score", upto=False):
        return f"{name} [Total Pts: {'up to ' if upto else ''}{pts:g} {disp}] |{cid}"

    calc = [header("Prog Grade", 400, 730001, "Percentage", True),
            header("Final Grade", TOTAL_PTS, 730002, "Percentage", True),
            header("Overall Grade", TOTAL_PTS, 730003, "Letter +/- 2", True)]
    item_headers = [header(n, p, cid) for n, _, p, cid, *_ in ITEMS]
    cols = ["Last Name", "First Name", "Username", "Student ID", "Last Access", "Availability", "Child Course ID",
            *calc, *item_headers]
    rows = []
    for stu in students:
        earned = possible = prog_e = prog_p = 0.0
        for n, cat, p, *_ in ITEMS:
            v = scores.get((stu["username"], n), "")
            if v and v[0].isdigit():
                earned += float(v); possible += p
                if cat in ("Homework", "Quiz"):
                    prog_e += float(v); prog_p += p
        final = 100 * earned / possible
        last = EXPORT_DAY - timedelta(minutes=rng.randint(30, 5 * 24 * 60))
        rows.append([stu["last"], stu["first"], stu["username"], stu["sis"], last.strftime("%Y-%m-%d %H:%M:%S"),
                     "Yes", f"{COURSE}-{stu['section']}", f"{100 * prog_e / prog_p:.5g}", f"{final:.7g}",
                     letter(final), *[scores.get((stu["username"], n), "") for n, *_ in ITEMS]])

    finals = {r[2]: float(r[8]) for r in rows}
    below = sorted(u for u, f in finals.items() if f < 72)
    print(f"final grades {min(finals.values()):.1f}-{max(finals.values()):.1f}%, below 72%: {below}")
    # tests/test_data_models.py::test_tiny_minority_trains_but_flags_unvalidated needs exactly one
    assert below == ["stu0005"], below

    hist.sort(key=lambda r: r["Date"], reverse=True)   # Ultra lists newest first
    for r in hist:
        r["Date"] = r["Date"].strftime(HIST_FMT)
        if r["Attempt Activity"]:
            r["Attempt Activity"] = r["Attempt Activity"].strftime(HIST_FMT)

    gb_buf, gh_buf = io.StringIO(), io.StringIO()
    w = csv.writer(gb_buf, lineterminator="\n"); w.writerow(cols); w.writerows(rows)
    dw = csv.DictWriter(gh_buf, fieldnames=list(hist[0]), lineterminator="\n"); dw.writeheader(); dw.writerows(hist)

    stamp = EXPORT_DAY.strftime("%Y-%m-%d")
    targets = {
        HERE / "demo_gradebook.csv": gb_buf, HERE / "demo_grade_history.csv": gh_buf,
        REPO / "dev_share" / "SPARK" / COURSE / "gradebook" / f"gradebook_{stamp}.csv": gb_buf,
        REPO / "dev_share" / "SPARK" / COURSE / "grade_history" / f"grade_history_{stamp}.csv": gh_buf,
    }
    for path, buf in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(buf.getvalue(), encoding="utf-8")
        print("wrote", path.relative_to(REPO))
    print(f"{len(rows)} students, {len(hist)} history events")


if __name__ == "__main__":
    main()
