"""EI prompts and workbook configuration, ported from llm_client.py and workbook_config.py.

Prompt text is kept as written in the original, with two edits: "(from Canvas)" now
reads "(from the gradebook)" since data may come from Blackboard, and prompts are
built as (system, user) pairs so they can be previewed exactly as sent.

Session length: 30 minutes throughout (decision 2026-11-01). The original lesson-plan
user prompt asked for a 60-90 minute plan, which contradicted SYSTEM_PROMPT.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

TASK, FORMAT, VOICE = " Create", " Interactive Study Session", " Professor"
CONTEXT = ("Use the learning objectives, performance summary, instructor notes, and student notes to create a "
           "30-minute interactive and customized list of exercises to assist a struggling student during extra "
           "instruction (EI).")

SYSTEM_PROMPT = f"""TASK:{TASK} a 30 minute study guide with example questions with solutions.

FORMAT:{FORMAT}.

VOICE:{VOICE}.

CONTEXT: {CONTEXT}

You specialize in computer science and math, but can generalize.

Always:
- Diagnose likely misconceptions based on scores and notes
- Prioritize at most 3 learning goals per session
- Propose concrete, time-boxed activities
- Show code examples when possible
- End with a concise study guide for students to continue on their own after the session
"""

WORKBOOK_SYSTEM_PROMPT = """
TASK: Be thoughful and creative and provide a short, actionable student workbook based on the recommended intervention modules show below.

VOICE: You are an expert but friendly learning coach.

FORMAT: A report for the student to use as a study guide in beautified markdown.
"""


@dataclass
class Profile:
    """What the LLM sees about a student. Never includes name, username or ID."""
    course_name: str
    learning_objectives: str
    performance_summary: str
    instructor_notes: str = ""
    student_notes: str = ""


def lesson_plan_prompt(p: Profile) -> tuple[str, str]:
    user = f"""
Course: {p.course_name}

Learning objectives:
{p.learning_objectives}

Performance summary (from the gradebook):
{p.performance_summary}

Instructor notes:
{p.instructor_notes or 'None provided'}

Student notes (self-report):
{p.student_notes or 'None provided'}

Generate:
1. A short diagnostic summary (2–3 sentences).
2. 2–3 prioritized learning goals for THIS EI session.
3. A 30 minute EI plan broken into time-boxed activities.
4. 5–10 targeted practice problems or questions.
5. A short study guide (bullet list) aligned to the learning objectives.
"""
    return SYSTEM_PROMPT, user


def objectives_prompt(p: Profile) -> tuple[str, str]:
    user = f"""TASK: Using the learning objectives and performance summary, identify the top 3 learning objectives the student should focus on during extra instruction (EI).
    Keep the response concise, in bullet point format and aligned to the student's needs. Also only provide a plain text version suitable for a text area input.
    CONTEXT:
    Course: {p.course_name}
    Performance summary (from the gradebook):
    {p.performance_summary} """
    return SYSTEM_PROMPT, user


# ---------------------------------------------------------------- workbook
TONES = ["Professional", "Supportive Coach", "Direct & Tactical"]
LENGTHS = ["Short", "Medium", "Detailed"]


@dataclass
class WorkbookConfig:
    include_overview: bool = True
    include_student_snapshot: bool = True
    include_time_plan: bool = True
    include_study_plan: bool = True
    include_practice: bool = True
    include_reflection: bool = True
    include_resources: bool = True
    include_checklist: bool = True
    tone: str = "Professional"
    length: str = "Medium"
    custom_instructor_notes: str = ""

    def __post_init__(self):
        if self.tone not in TONES:
            self.tone = "Professional"
        if self.length not in LENGTHS:
            self.length = "Medium"


def workbook_config_text(cfg: WorkbookConfig, signals_text: str) -> str:
    """build_workbook_prompt() from workbook_config.py."""
    parts = [f"Write a {cfg.length.lower()} student workbook in a {cfg.tone.lower()} tone.",
             "INCLUDE the following sections:"]
    if cfg.custom_instructor_notes.strip():
        parts.append(f"Instructor notes/constraints: {cfg.custom_instructor_notes.strip()}")
    sections = [
        (cfg.include_overview, "Section: Overview (purpose, how to use this workbook)."),
        (cfg.include_student_snapshot, f"Section: Student Snapshot using these stats: {signals_text}"),
        (cfg.include_time_plan, "Section: Time & Planning (weekly plan, priorities, deadlines)."),
        (cfg.include_study_plan, "Section: Study Plan (learning gaps → concrete actions)."),
        (cfg.include_practice, "Section: Practice Activities (targeted drills + examples)."),
        (cfg.include_reflection, "Section: Reflection Prompts (what I know / don’t know / next steps)."),
        (cfg.include_resources, "Section: Resources & Help-Seeking (tutoring, office hours, peer study)."),
        (cfg.include_checklist, "Section: Weekly Checklist (checkbox-style list)."),
    ]
    parts += [text for on, text in sections if on]
    return "\n\n".join(parts)


def workbook_prompt(p: Profile, signals_text: str, modules: list[dict[str, Any]], cfg: WorkbookConfig) -> tuple[str, str]:
    """generate_intervention_workbook() user prompt (the path the Streamlit app used)."""
    recs = ""
    for i, m in enumerate(modules, start=1):
        recs += f"{i}. {m['title']}  ({m['key']})\n   Why: {m['why']}\n   Student outputs:\n"
        for out in m.get("student_outputs", []):
            recs += f"    - {out}\n"   # the original omitted this newline, running the bullets together
    user = f"""
TASK: Be creative and provide a short, actionable student workbook based on the recommended intervention modules show below.

VOICE: You are an expert but friendly learning coach.

FORMAT: A report for the student to use as a study guide in beautified markdown and professional formatting. Avoid tables if possible.
{workbook_config_text(cfg, signals_text)}

CONTEXT:

Course: {p.course_name}

Learning objectives:
{p.learning_objectives}

Performance summary:
{p.performance_summary}

Computed signals:
{signals_text}

Instructor notes:
{p.instructor_notes or 'None provided'}

Student notes:
{p.student_notes or 'None provided'}

Recommended intervention modules:
{recs}
"""
    return WORKBOOK_SYSTEM_PROMPT, user


def config_from_dict(d: dict | None) -> WorkbookConfig:
    known = set(asdict(WorkbookConfig()).keys())
    return WorkbookConfig(**{k: v for k, v in (d or {}).items() if k in known})
