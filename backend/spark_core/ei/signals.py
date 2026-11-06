"""Intervention signals, triage and module selection.

Ported from intervention_workbook.py and profiling.py. The module library, the
recommendation rules and weights, the Activity Index formula and the trigger
thresholds are unchanged. Differences:

  * Signals are computed from the course's STUDENT_PERFORMANCE rows (any LMS), not
    from raw Canvas JSON.
  * "Missing" = scored zero with no submission (MISSED_ASSIGNMENT), the same definition
    the rest of SPARK uses. The Canvas version counted every unscored item, which for
    Blackboard would include ungraded and future work.
  * Late and near-due rates use only items that have a submission time (hand-graded
    quizzes/exams have none and would otherwise count as on time).
  * Days since last submission is measured to the export's snapshot time, not today.
  * needs_intervention() no longer crashes when a student has no grade yet, and returns
    (bool, list[str]) as the UI expects.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class InterventionModule:
    key: str
    title: str
    when_to_use: str
    student_outputs: tuple[str, ...]


MODULES: dict[str, InterventionModule] = {
    "time_task_triage": InterventionModule(
        "time_task_triage", "Time & Task Triage (Weekly Scheduling)",
        "Late/missing work, low completion, inconsistent study time.",
        ("A realistic weekly schedule with protected study blocks", "Two 'appointment with myself' study commitments",
         "A 7-day micro-plan for due dates")),
    "active_study_cycle": InterventionModule(
        "active_study_cycle", "Active Study Cycle (Preview → Attend → Notes → Review)",
        "Student attends/turns in work but scores stay low; passive studying.",
        ("Preview checklist for each class meeting", "A 10-minute post-class review ritual",
         "Two focused study blocks with clear goals")),
    "homework_execution": InterventionModule(
        "homework_execution", "Homework Execution (Start Early + No-Peeking + Timeboxing)",
        "Homework scores low, procrastination, lots of 'stuck' time.",
        ("A 'start today' rule for each assignment", "A timebox plan (15-30 min cycles) + what to do when stuck",
         "A short error-log for repeated mistakes")),
    "help_seeking": InterventionModule(
        "help_seeking", "Help-Seeking & Office Hours (Prepared Questions)",
        "Student isn't using support well or arrives unprepared.",
        ("A list of 3 prepared questions + 'what I tried'", "A teach-back script for tutoring/office hours",
         "A plan for next help session (time/place/topic)")),
    "exam_readiness": InterventionModule(
        "exam_readiness", "Exam Readiness (Practice Testing + Exam Tactics)",
        "Exams/quizzes are the main driver of low grade; test anxiety.",
        ("A practice-test plan (spaced sessions)", "A test-day time budget + 'start with easiest' rule",
         "A post-test reflection + improvement plan")),
    "metacognition": InterventionModule(
        "metacognition", "Metacognitive Check (Find Confusion Early)",
        "Student can't articulate what's confusing; lots of shallow errors.",
        ("A 'what confuses me' tracker", "A self-check rubric after practice", "A plan to turn confusion into questions")),
    "study_groups": InterventionModule(
        "study_groups", "Study Groups (Prepared Sessions + Teach-Back)",
        "Student isolates; needs accountability; benefits from peer learning.",
        ("Study-group agenda template", "Preparedness checklist", "Teach-back prompts to explain concepts aloud")),
    "mindset_self_talk": InterventionModule(
        "mindset_self_talk", "Mindset & Self-talk (Optional Add-on)",
        "Notes show low confidence/avoidance; anxiety spiral.",
        ("A reframe script (actions → results)", "A 2-minute reset routine before studying/tests",
         "A short self-talk audit")),
}


# ---------------------------------------------------------------- signals
def compute_signals(rows: pd.DataFrame, as_of: datetime | None = None) -> dict[str, Any]:
    """rows: STUDENT_PERFORMANCE for one student."""
    empty = {"current_grade_pct": None, "avg_pct": None, "std_pct": None, "late_rate": None, "missing_rate": None,
             "zero_count": 0, "missing_count": 0, "low_score_count": 0, "near_due_rate": None,
             "days_since_last_submission": None, "items": 0, "timed_items": 0}
    if rows is None or rows.empty:
        return empty
    df = rows.copy()
    possible = df["POINTS_POSSIBLE"].astype(float)
    earned = df["POINTS_EARNED"].astype(float)
    pct = (earned / possible.where(possible > 0) * 100.0).fillna(100.0)   # 0-point items count as complete

    total_possible = possible.sum()
    current = float(earned.sum() / total_possible * 100.0) if total_possible > 0 else None
    timed = df[df["SUBMITTED_AT"].notna()]
    late_rate = float(timed["LATE"].astype(bool).mean()) if len(timed) else None

    near = timed.dropna(subset=["DUE_AT"])
    near_due_rate = None
    if len(near):
        hrs = (pd.to_datetime(near["DUE_AT"]) - pd.to_datetime(near["SUBMITTED_AT"])).dt.total_seconds() / 3600.0
        near_due_rate = float((hrs <= 24.0).mean())

    days_since = None
    if len(timed):
        ref = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.utcnow()
        last = pd.to_datetime(timed["SUBMITTED_AT"]).max()
        if ref.tzinfo is not None and last.tzinfo is None:
            ref = ref.tz_convert(None) if hasattr(ref, "tz_convert") else ref
        if ref.tzinfo is None and last.tzinfo is not None:
            last = last.tz_convert(None)
        days_since = max(0.0, float((ref - last).total_seconds() / 86400.0))

    missing = int(df["MISSED_ASSIGNMENT"].astype(bool).sum())
    return {
        "current_grade_pct": current,
        "avg_pct": float(pct.mean()),
        "std_pct": float(pct.std(ddof=0)) if len(pct) >= 2 else 0.0,
        "late_rate": late_rate,
        "missing_rate": float(missing / len(df)),
        "zero_count": int((earned == 0).sum()),
        "missing_count": missing,
        "low_score_count": int((pct < 70).sum()),
        "near_due_rate": near_due_rate,
        "days_since_last_submission": days_since,
        "items": int(len(df)),
        "timed_items": int(len(timed)),
    }


def compute_activity_index(signals: dict[str, Any]) -> float | None:
    """0..100 behavioural stability (unchanged weights: 0.45 late, 0.45 missing, 0.10 staleness over 14 days)."""
    late, miss, days = signals.get("late_rate"), signals.get("missing_rate"), signals.get("days_since_last_submission")
    if late is None and miss is None and days is None:
        return None
    days_norm = 0.0 if days is None else min(max(days / 14.0, 0.0), 1.0)
    late = 0.0 if late is None else min(max(late, 0.0), 1.0)
    miss = 0.0 if miss is None else min(max(miss, 0.0), 1.0)
    return float(max(0.0, min(100.0, (1.0 - (0.45 * late + 0.45 * miss + 0.10 * days_norm)) * 100.0)))


# ---------------------------------------------------------------- triage
def needs_intervention(signals: dict[str, Any], risk_label: str | None, activity: float | None) -> tuple[bool, list[str]]:
    late = signals.get("late_rate") or 0.0
    missing = signals.get("missing_rate") or 0.0
    days = signals.get("days_since_last_submission")
    grade = signals.get("current_grade_pct")
    risk = (risk_label or "").lower()

    if grade is not None and grade >= 85:
        if risk == "low":
            return False, ["Risk level is low and grade is at or above 85%. No intervention suggested."]
        return False, ["Grade is at or above 85%. No intervention suggested."]

    reasons: list[str] = []
    if grade is None:
        reasons.append("No graded work yet")
    if risk in {"critical", "high", "medium"}:
        reasons.append(f"Risk level is {risk_label}")
    if activity is not None and activity < 60:
        reasons.append(f"Activity Index is low ({activity:.0f})")
    if missing >= 0.10:
        reasons.append(f"Missing rate is high ({missing * 100:.0f}%)")
    if late >= 0.20:
        reasons.append(f"Late rate is high ({late * 100:.0f}% of timed items)")
    if days is not None and days >= 10:
        reasons.append(f"No submissions in about {days:.0f} days")
    if grade is not None and grade < 70:
        reasons.append(f"Current grade is low ({grade:.0f}%)")
    return bool(reasons), reasons or ["No major intervention triggers detected (workbook still available)."]


def recommend_modules(signals: dict[str, Any], risk_label: str | None, instructor_notes: str = "",
                      student_notes: str = "") -> list[dict[str, Any]]:
    """Ranked 2-3 modules with rationale (rules and weights unchanged)."""
    recs: list[tuple[str, float, str]] = []
    late = signals.get("late_rate") or 0.0
    miss = signals.get("missing_rate") or 0.0
    near_due = signals.get("near_due_rate") or 0.0
    avg = signals.get("avg_pct") or 0.0
    std = signals.get("std_pct") or 0.0
    low_scores = signals.get("low_score_count") or 0
    notes = f"{instructor_notes} {student_notes}".lower()

    if miss >= 0.10 or late >= 0.20 or near_due >= 0.50:
        recs.append(("time_task_triage", 0.95, "Missing/late/near-due behavior suggests a planning + time protection "
                     f"bottleneck (late={late:.2f}, missing={miss:.2f}, near_due={near_due:.2f})."))
        recs.append(("homework_execution", 0.90, "Late/missing often co-occurs with rushed homework; use "
                     "start-early + timeboxing + no-peeking."))
    if avg and avg < 75:
        recs.append(("active_study_cycle", 0.85, f"Average performance is low (avg={avg:.1f}%). Shift from passive "
                     "review to preview/notes/review + focused study blocks."))
        recs.append(("metacognition", 0.75, "Low scores often reflect untracked confusion; add 'what confuses me' "
                     "+ self-check routines."))
    if std and std >= 15:
        recs.append(("metacognition", 0.80, f"High variability (std={std:.1f}) suggests unstable understanding; "
                     "add confusion tracking + targeted questions."))
    if "exam" in notes or "test" in notes or "quiz" in notes:
        recs.append(("exam_readiness", 0.88, "Notes reference tests/exams; add practice testing + exam tactics."))
    if low_scores >= 3:
        recs.append(("exam_readiness", 0.70, f"Multiple low scores ({low_scores}) indicate need for retrieval "
                     "practice + error analysis."))
    if any(k in notes for k in ("office hour", "tutor", "help", "confus")):
        recs.append(("help_seeking", 0.72, "Notes suggest the student needs structured help-seeking: attempt first, "
                     "prepared questions, teach-back."))
    if any(k in notes for k in ("alone", "study group", "accountability")):
        recs.append(("study_groups", 0.65, "Student may benefit from prepared study-group sessions for "
                     "accountability and teach-back."))
    if any(k in notes for k in ("anx", "panic", "can't", "stupid", "hopeless", "overwhelmed")):
        recs.append(("mindset_self_talk", 0.60, "Notes suggest confidence/anxiety loop; add a small mindset/self-talk reset."))
    if not recs:
        recs.append(("active_study_cycle", 0.60, "Default: establish strong learning routines (preview/notes/review "
                     "+ focused study blocks)."))

    best: dict[str, tuple[float, str]] = {}
    for key, score, why in recs:
        if key not in best or score > best[key][0]:
            best[key] = (score, why)
    ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)
    n = 3 if (risk_label or "").lower() in {"critical", "high"} else 2
    return [{"key": k, "title": MODULES[k].title, "why": why, "student_outputs": list(MODULES[k].student_outputs)}
            for k, (_, why) in ranked[:n]]


def format_signals_for_prompt(signals: dict[str, Any], activity: float | None) -> str:
    f_pct = lambda x: "N/A" if x is None else f"{x:.1f}%"
    f_rate = lambda x: "N/A" if x is None else f"{x * 100.0:.1f}%"
    f_num = lambda x, d=1: "N/A" if x is None else f"{float(x):.{d}f}"
    return "\n".join([
        f"- Current grade (graded items): {f_pct(signals.get('current_grade_pct'))}",
        f"- Avg assignment %: {f_pct(signals.get('avg_pct'))}",
        f"- Score variability (std %): {f_num(signals.get('std_pct'))}",
        f"- Late rate (items with a submission time): {f_rate(signals.get('late_rate'))}",
        f"- Missing rate: {f_rate(signals.get('missing_rate'))}",
        f"- Near-due submission rate: {f_rate(signals.get('near_due_rate'))}",
        f"- Missing count: {signals.get('missing_count')}",
        f"- Zero count: {signals.get('zero_count')}",
        f"- Low-score count (<70%): {signals.get('low_score_count')}",
        f"- Days since last submission (as of the export): {f_num(signals.get('days_since_last_submission'))}",
        f"- Activity Index (0-100): {f_num(activity, 0)}",
    ])


def summarize_performance(rows: pd.DataFrame, threshold: float = 80.0) -> str:
    """profiling.summarize_performance: graded items below 80% (names + %), no student identifiers."""
    if rows is None or rows.empty:
        return "No graded work yet."
    d = rows[rows["POINTS_POSSIBLE"] > 0].copy()
    d["pct"] = d["POINTS_EARNED"] / d["POINTS_POSSIBLE"] * 100.0
    low = d[d["pct"] < threshold]
    if low.empty:
        return f"Student is performing at or above {threshold:.0f}% on all graded assignments so far."
    lines = ["Student is struggling on the following assignments:"]
    for r in low.itertuples():
        lines.append(f"- {r.ASSIGNMENT}: {r.pct:.1f}%")
    return "\n".join(lines)
