# Intervention Methodology

**Gradebook → Analytics → Action**

This framework (inspired by *Teach Students How to Learn* [1]) explains how SPARK turns gradebook activity and analytics (**Success probability**, **Activity Index**, **Risk cluster**) into **repeatable, targeted interventions**, and generates a **short student workbook** tailored to each student's needs.

---

## Purpose

Students who are struggling often benefit most from **simple, structured, repeatable** supports, especially around:

- **time management** and planning
- **homework execution** habits
- **exam preparation** and test-taking routines
- **effective help-seeking** (tutoring, office hours, study groups)
- **metacognitive awareness** (knowing what they do and do not understand)

SPARK operationalizes these ideas so instructors can scale meaningful intervention without starting from scratch for every student.

---

## 1) Data ingest

**Inputs (gradebook + instructor context)**

- Assignments (name, category, points, due dates)
- Submissions (score, late, missing, timestamps)
- Course structure (assignment categories)
- Instructor notes (observations)
- Student notes (self-report, goals, barriers)

**Analytics inputs (from SPARK)**

- **Success probability** from the course's success model, when one is trained
- **Activity Index** (behavioural stability, 0 to 100)
- **Risk cluster** (Critical / High / Medium / Low)

---

## 2) Convert gradebook data into intervention signals

SPARK computes **actionable signals**, not just descriptive stats. Signals are measured as of the export's snapshot time.

### Performance and progress
- Current grade over graded items
- Average assignment percentage
- Count of low scores (below 70%)

### Behaviour and momentum
- Late rate (over items that have a submission time)
- Missing rate (scored zero with no submission)
- Near-due rate (submitted within 24 hours of the due time)
- Days since last submission
- Volatility: standard deviation of scores (%)

### Activity Index
`100 × (1 − (0.45 × late rate + 0.45 × missing rate + 0.10 × min(days since last submission / 14, 1)))`

---

## 3) Triage: is an intervention needed?

SPARK evaluates transparent triggers and shows the instructor each reason.

- No intervention is suggested when the current grade is **85% or higher**.
- Otherwise a student is flagged when any of these hold:
  - Risk cluster is **Critical**, **High** or **Medium**
  - Activity Index below **60**
  - Missing rate **10%** or more
  - Late rate **20%** or more
  - **10 or more days** since the last submission
  - Current grade below **70%**

The workbook is always available, flagged or not.

---

## 4) Three-tier support ladder

### Tier 1 | Nudge (low friction, fast)
- **When:** early warning signs (small late-rate uptick, mild downward trend)
- **Output:** 1 to 2 page workbook and one 7-day commitment

### Tier 2 | Structured plan (skills + content)
- **When:** medium or high risk, multiple late or missing items, weak category trends
- **Output:** 5 to 7 page workbook, EI agenda, practice set and study schedule

### Tier 3 | Intensive turnaround (accountability + coordination)
- **When:** critical risk, long gaps plus missing work
- **Output:** about 10 page workbook, weekly check-ins and a required help-seeking plan

Use the workbook **Length** setting (Short / Medium / Detailed) to match the tier.

---

## 5) Intervention playbook

SPARK keeps a library of eight modules and recommends **2** of them (or **3** for Critical/High risk) from the signals and keywords in the notes.

| Module | Typical trigger |
|---|---|
| Time & Task Triage | missing ≥ 10%, late ≥ 20% or near-due ≥ 50% |
| Homework Execution | same as above |
| Active Study Cycle | average below 75% |
| Metacognitive Check | average below 75% or score volatility ≥ 15 |
| Exam Readiness | notes mention exams/tests/quizzes, or 3+ low scores |
| Help-Seeking | notes mention office hours, tutoring, help or confusion |
| Study Groups | notes mention studying alone or accountability |
| Mindset & Self-talk | notes mention anxiety, panic or feeling overwhelmed |

---

## 6) How the workbook is generated

1. **Ingest** | gradebook data, computed signals and model outputs, plus learning objectives and notes
2. **Diagnose** | the primary bottleneck(s), usually one or two
3. **Select modules** | two or three at most, so the plan stays realistic
4. **Generate** | the AI model drafts the plan or workbook from the redacted prompt
5. **Review and export** | the instructor reviews it, then downloads HTML or PDF
6. **Follow up** | re-check signals after 7 to 14 days and swap modules if the bottleneck changes

---

## Privacy

- The prompt never includes the student's name, username, ID or e-mail. Those are also removed from the objectives and notes before anything is sent.
- The prompt preview shows exactly what will be sent.
- Generated plans and workbooks are saved on the SPARK server only, and every generation and export is recorded in the audit log.

---

## How instructors use this (about 10 minutes)

1. Review the intervention reasons
2. Skim the signals and recommended modules
3. Run EI using the generated plan
4. Assign the workbook as the weekly plan
5. Re-check signals after 7 to 14 days and iterate

---

## References

1. McGuire, S. Y., & McGuire, S. (2015). *Teach students how to learn: Strategies you can incorporate into any course to improve student metacognition, study skills, and motivation*. Stylus Publishing.
