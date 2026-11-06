// Thin typed client for the SPARK API. Session is an HttpOnly cookie, so every
// call sends credentials and the browser never sees the token.

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

export const enc = encodeURIComponent

async function upload<T>(path: string, file: File): Promise<T> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(path, { method: 'POST', body: fd, credentials: 'include' })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* non-JSON */ }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

// ---------- types ----------
export interface Persona { key: string; name: string; upn: string; roles: string[]; description: string }
export interface AuthConfig { mode: string; env: string; personas?: Persona[] }
export interface User { oid: string; upn: string; name: string; roles: string[]; is_admin: boolean; auth_mode: string }

export interface IngestReport {
  students?: number
  sections?: string[]
  items_active?: number
  items_not_yet_active?: string[]
  due_dates_inferred?: number
  due_dates_manifest?: number
  due_dates_missing?: string[]
  graded_cells?: number
  with_submit_time?: number
  late?: number
  needs_grading_excluded?: number
  history_loaded?: boolean
  history_users_not_in_gradebook?: number
  files?: Record<string, string | null>
  note?: string
  synthetic?: boolean
  excluded_no_section?: number
  current_grade_source?: 'configured_column' | 'percentage_column' | 'computed_points'
  current_grade_column?: string | null
}
export interface IngestRun {
  id: number; status: 'running' | 'succeeded' | 'failed' | 'skipped'
  started_at: string | null; finished_at: string | null; source_version: string
  triggered_by: string; report: IngestReport; error: string
}
export interface CourseSummary {
  id: string; name: string; lms: string; role: string; has_data: boolean
  last_run: IngestRun | null; last_success: IngestRun | null
  last_opened_at?: string | null; position?: number | null; archived?: boolean; archived_at?: string | null
}
export type CourseSort = 'recent' | 'custom' | 'name' | 'updated'
export interface CourseList {
  courses: CourseSummary[]; can_add_courses: boolean; hidden_courses: HiddenCourse[]; upload_max_mb: number
  course_sort: CourseSort
}
export interface Section { SECTION_ID: string; SECTION_NAME: string; STUDENTS: number; AVG_SCORE: number | null }
export interface CourseDetail extends Omit<CourseSummary, 'last_run'> { sections: Section[] }

export interface RosterRow {
  STUDENT_ID: string; STUDENT_NAME: string; STUDENT_SORTABLE_NAME?: string; SECTION_ID: string; SECTION_NAME: string
  CURRENT_SCORE: number | null; CURRENT_GRADE: string | null
  grade_ratio: number | null; avg_percentage: number | null; late_rate: number | null
  missed_rate: number | null; num_assignments: number; n_timed_items: number; success_prob: number | null
}
export interface AssignmentRow {
  ASSIGNMENT_ID: string; ASSIGNMENT: string; ASSIGNMENT_GROUP_ID: string
  DUE_AT: string | null; DUE_SOURCE: string | null; SUBMITTED_AT: string | null; GRADED_AT: string | null
  POINTS_EARNED: number; POINTS_POSSIBLE: number; PERCENTAGE: number | null
  LATE: boolean; MISSED_ASSIGNMENT: boolean; SUBMISSION_DELTA_DAYS: number | null
}
export interface StudentDetail {
  profile: { STUDENT_ID: string; STUDENT_NAME: string; SECTION_NAME: string; CURRENT_SCORE: number | null
             CURRENT_GRADE: string | null; LAST_ACCESS: string | null }
  kpis: { risk_label: Risk | null; success_prob: number | null; points_earned: number; points_possible: number; grade_ratio: number | null; items_graded: number
          items_missed: number; items_with_submit_time: number; late: number
          avg_days_before_due: number | null; class_rank_pct: number | null }
  assignments: AssignmentRow[]
  model: ModelInfo | null
}
export type Risk = 'Critical' | 'High' | 'Medium' | 'Low'
export interface ClusterStudent {
  STUDENT_ID: string; STUDENT_NAME: string; SECTION_NAME: string | null; cluster_label: Risk; risk_rank: number
  grade_ratio: number | null; avg_percentage: number | null; std_percentage: number | null
  late_rate: number | null; on_time_rate: number | null; missed_rate: number | null
  num_assignments: number; n_timed_items: number; days_since_last_submission: number | null; pace: number | null
}
export interface ClusterSummary {
  cluster_label: Risk; students: number; mean_grade_ratio: number; mean_avg_percentage: number
  mean_std_percentage: number; mean_late_rate: number; mean_on_time_rate: number; mean_missed_rate: number
}
export interface AdmissionsInfo {
  available: boolean; match_rate: number; matched: number
  numeric_fields: Record<string, string>; yesno_fields: Record<string, string>; category_fields: Record<string, string>
}
export interface Analytics {
  available: boolean; note?: string; summary: ClusterSummary[]; students: ClusterStudent[]; admissions?: AdmissionsInfo
}
export interface MetricInfo { label: string; higher_is_better: boolean }
export interface SectionStat {
  SECTION_ID: string; SECTION_NAME: string; N_STUDENTS: number; COVERAGE_RATE: number; MEAN_GRADE_PCT: number
  MEDIAN_GRADE_PCT: number; AT_RISK_PCT: number; LATE_RATE: number; MISSED_RATE: number
  DAYS_SINCE_LAST: number | null; HEALTH_INDEX: number; RANK: number
}
export interface Director {
  metric: string; metrics: Record<string, MetricInfo>; weights: Record<string, number>; cutoff: number
  kpis: { sections: number; students: number; top_section: string | null; lowest_section: string | null; below_cutoff: number }
  sections: SectionStat[]
}
export interface SectionStudent {
  STUDENT_ID: string; STUDENT_NAME: string; HEALTH_INDEX: number; GRADE_PCT: number | null; CURRENT_GRADE: string | null
  avg_percentage: number | null; late_rate: number | null; missed_rate: number | null; n_timed_items: number
  cluster_label: Risk | null; ENROLLMENT_STATE: string
}
export interface ModelMetrics { folds: number; accuracy?: number; roc_auc?: number; baseline_accuracy?: number; note?: string }
export interface ModelSummary {
  id: number; course_id: string; course_name: string; trained_at: string; trained_by: string
  config: { success_threshold: number; include_grade_features: boolean; classifier: string }
  n_students: number; n_success: number; success_rate: number; metrics: ModelMetrics; features: string[]
  importance: { feature: string; importance: number }[] | null
}
export interface ModelsView {
  own: ModelSummary | null; used: ModelSummary | null; alias_source: string | null
  candidates: ModelSummary[]; history: ModelSummary[]; classifiers: Record<string, string>; can_train: boolean
}
export interface ModelInfo {
  model_id: number; source_course_id: string; source_course_name: string | null; aliased: boolean
  trained_at: string; threshold: number; metrics: ModelMetrics
}
export interface ZyStatus { exists: boolean; file_name?: string; uploaded_at?: string; people?: number; matched?: number; chapters?: number }
export interface CourseData { has_data: boolean; db_size_mb: number; runs: IngestRun[]; zybooks: ZyStatus; role: string }
export interface AdmissionsStatus {
  exists: boolean; students: number; files: number; last_ingest: string | null
  legacy_sensitive_columns?: string[]
  history: { file_name: string; added_at: string; rows_in_file: number; new_rows: number; total_after: number }[]
}
export interface AdmissionsResult { file_name: string; status: string; rows_in_file: number; new_rows: number; total_after: number }
export interface InventoryRow {
  id: string; name: string; lms: string; has_data: boolean; db_size_mb: number; last_ingest: string | null
  students: number | null; model: boolean; alias_source: string | null; zybooks: boolean
}

export type Figure = { data: unknown[]; layout: Record<string, unknown> }

export interface Preferences { cutoff_percentage: number; show_cutoff: boolean; smooth_curve: boolean }

// ---------- calls ----------
// ---------- removing courses ----------
export interface RemovalInfo {
  course: { id: string; name: string; lms: string; code: string }
  can_hide: boolean; can_delete: boolean; delete_blocked_reason: string | null; others_with_access: number
  impact: { students: number | null; ingest_runs: number; success_models: number; ei_sessions: number
            models_reused_by: { id: string; name: string }[] }
}
export interface HiddenCourse { id: string; name: string; lms: string }

// ---------- uploaded exports ----------
export interface UploadedFileInfo {
  filename: string; kind: 'gradebook' | 'grade_history' | null; rows: number; students: number; items: number
  sections: string[]; suggested_course_code: string | null; first_date: string | null; last_date: string | null
  error: string | null
}
export interface UploadResult {
  ok: boolean; files: UploadedFileInfo[]; errors: string[]; warnings: string[]; suggested_course_code: string | null
  course_code?: string; course_id?: string; stored?: Record<string, string>
  run?: { id: number; status: string; error: string } | null; note?: string
}

async function postForm<T>(path: string, files: File[], fields: Record<string, string>): Promise<T> {
  const fd = new FormData()
  files.forEach((f) => fd.append('files', f))
  Object.entries(fields).forEach(([k, v]) => fd.append(k, v))
  const res = await fetch(path, { method: 'POST', body: fd, credentials: 'include' })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* non-JSON */ }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

// ---------- Blackboard mount ----------
export interface MountStatus {
  configured: boolean; root: string | null; mount_point: string | null; is_mount: boolean | null; fstype: string | null; is_webdav: boolean | null; server_ok: boolean | null; server_ms: number | null; reachable: boolean
  latency_ms: number | null; courses: number | null; error: string | null; hint: string | null; checked_at: string
}
export interface WatchFile { name: string; size: number; mtime: number }
export interface Watch {
  course_id: string; status: string; detail: string; files: Record<string, WatchFile | null>
  seen_since: string | null; last_checked_at: string | null; last_ingest_run_id: number | null
}
export interface PollSummary {
  started_at?: string; checked?: number; ingested?: string[]; failed?: string[]; waiting?: string[]
  duration_s?: number; error?: string; skipped?: string
}
export interface PollerStatus {
  enabled: boolean; interval_s: number; settle_s: number; io_timeout_s: number; leader: boolean; pid: number
  last_run: PollSummary | null; stuck_for_s: number | null
}
export interface MountView { mount: MountStatus; poller: PollerStatus | null; watches: Watch[] }
export interface CourseWatch { watched: boolean; auto?: boolean; interval_s?: number; settle_s?: number; watch?: Watch | null }

// ---------- Extra Instruction (EI) ----------
export interface EISignals {
  current_grade_pct: number | null; avg_pct: number | null; std_pct: number | null; late_rate: number | null
  missing_rate: number | null; near_due_rate: number | null; zero_count: number; missing_count: number
  low_score_count: number; days_since_last_submission: number | null; items: number; timed_items: number
}
export interface EIModule { key: string; title: string; why?: string; when_to_use?: string; student_outputs: string[] }
export interface WorkbookConfig {
  include_overview: boolean; include_student_snapshot: boolean; include_time_plan: boolean; include_study_plan: boolean
  include_practice: boolean; include_reflection: boolean; include_resources: boolean; include_checklist: boolean
  tone: string; length: string; custom_instructor_notes: string
}
export interface EISessionSummary {
  id: number; created_by: string; created_at: string | null; updated_at: string | null; model: string
  has_plan: boolean; has_workbook: boolean; objectives_preview: string
}
export interface EISessionFull extends EISessionSummary {
  objectives: string; instructor_notes: string; student_notes: string; plan_md: string; workbook_md: string
  temperature: number; workbook_config: Partial<WorkbookConfig>; modules: EIModule[]
}
export interface LLMInfo { enabled: boolean; provider: string; disabled_reason?: string | null; models: string[]; default_model: string; rate_per_hour: number }
export interface EIContext {
  student: { id: string; name: string }; as_of: string | null; signals: EISignals; activity_index: number | null
  risk_label: Risk | null; triage: { needs_intervention: boolean; reasons: string[] }
  recommended_modules: EIModule[]; all_modules: EIModule[]
  workbook_options: { tones: string[]; lengths: string[]; default: WorkbookConfig }
  llm: LLMInfo; sessions: EISessionSummary[]
}
export interface EIInputs { objectives: string; instructor_notes: string; student_notes: string }
export interface EIPromptReq extends EIInputs { kind: 'plan' | 'workbook' | 'objectives'; workbook_config: WorkbookConfig; module_keys?: string[] | null }
export interface EIPrompt { kind: string; system: string; user: string; redactions: number; modules: EIModule[] | null }
export interface EIGenerateReq extends EIPromptReq { model: string; temperature: number; session_id?: number | null }

const eiBase = (id: string, sid: string) => `/api/courses/${enc(id)}/students/${enc(sid)}/ei`

export interface StreamHandlers {
  onMeta?: (m: { kind: string; model: string; redactions: number; modules: EIModule[] | null }) => void
  onDelta: (text: string) => void
}

/** POST + server-sent events (EventSource can't POST). Resolves with the saved session id. */
export async function streamGenerate(id: string, sid: string, body: EIGenerateReq, h: StreamHandlers,
                                     signal?: AbortSignal): Promise<number> {
  const res = await fetch(`${eiBase(id, sid)}/generate`, {
    method: 'POST', credentials: 'include', signal,
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body: JSON.stringify(body),
  })
  if (!res.ok || !res.body) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* non-JSON */ }
    throw new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let i: number
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i)
      buf = buf.slice(i + 2)
      let ev = 'message', data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) ev = line.slice(7)
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      const payload = data ? JSON.parse(data) : null
      if (ev === 'meta') h.onMeta?.(payload)
      else if (ev === 'delta') h.onDelta(payload)
      else if (ev === 'error') throw new ApiError(502, payload?.detail ?? 'AI service error')
      else if (ev === 'done') return payload.session_id as number
    }
  }
  throw new ApiError(502, 'The response ended early')
}

export const api = {
  authConfig: () => request<AuthConfig>('/auth/config'),
  simLogin: (persona: string) => request<{ user: User }>('/auth/sim/login', { method: 'POST', body: JSON.stringify({ persona }) }),
  logout: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),
  me: () => request<{ user: User }>('/api/me'),
  courses: () => request<CourseList>('/api/courses'),
  course: (id: string) => request<CourseDetail>(`/api/courses/${enc(id)}`),
  ingest: (id: string, force = false) =>
    request<{ run: IngestRun }>(`/api/courses/${enc(id)}/ingest?force=${force}`, { method: 'POST' }),
  students: (id: string, sectionId?: string) =>
    request<{ students: RosterRow[]; model: ModelInfo | null }>(
      `/api/courses/${enc(id)}/students${sectionId ? `?section_id=${enc(sectionId)}` : ''}`),
  student: (id: string, sid: string) => request<StudentDetail>(`/api/courses/${enc(id)}/students/${enc(sid)}`),
  figure: (id: string, sid: string, name: string, p: Preferences) =>
    request<{ data: unknown[]; layout: Record<string, unknown> }>(
      `/api/courses/${enc(id)}/students/${enc(sid)}/figures/${name}?cutoff=${p.cutoff_percentage}` +
      `&show_cutoff=${p.show_cutoff}&smooth=${p.smooth_curve}`),
  analytics: (id: string) => request<Analytics>(`/api/courses/${enc(id)}/analytics`),
  director: (id: string, metric: string, cutoff: number) =>
    request<Director>(`/api/courses/${enc(id)}/director?metric=${metric}&cutoff=${cutoff}`),
  directorSection: (id: string, sectionId: string) =>
    request<{ section_id: string; section_name: string; students: SectionStudent[] }>(
      `/api/courses/${enc(id)}/director/sections/${enc(sectionId)}`),
  /** Any server-rendered figure by URL (path + query built by the caller). */
  figureAt: (url: string) => request<Figure>(url),
  riskReportUrl: (id: string) => `/api/courses/${enc(id)}/analytics/report.csv`,
  courseData: (id: string) => request<CourseData>(`/api/courses/${enc(id)}/data`),
  exportDbUrl: (id: string) => `/api/courses/${enc(id)}/data/export.duckdb`,
  models: (id: string) => request<ModelsView>(`/api/courses/${enc(id)}/models`),
  trainModel: (id: string, body: { success_threshold: number; include_grade_features: boolean; classifier: string }) =>
    request<{ model: ModelSummary }>(`/api/courses/${enc(id)}/models/train`, { method: 'POST', body: JSON.stringify(body) }),
  setAlias: (id: string, source: string | null) =>
    request<{ alias_source: string | null }>(`/api/courses/${enc(id)}/models/alias`,
      { method: 'PUT', body: JSON.stringify({ source_course_id: source }) }),
  uploadZybooks: (id: string, file: File) =>
    upload<{ result: { people: number; matched: number; matched_by: Record<string, number>; chapters: number }; status: ZyStatus }>(
      `/api/courses/${enc(id)}/zybooks`, file),
  zybooksStudent: (id: string, sid: string) =>
    request<{ available: boolean; kpis?: { avg_participation: number; avg_zylab: number; total_minutes: number } }>(
      `/api/courses/${enc(id)}/zybooks/students/${enc(sid)}`),
  admissions: () => request<AdmissionsStatus>('/api/admin/admissions'),
  uploadAdmissions: (file: File, dryRun: boolean) =>
    upload<{ dry_run: boolean; results: AdmissionsResult[]; status: AdmissionsStatus }>(
      `/api/admin/admissions?dry_run=${dryRun}`, file),
  scrubAdmissions: () => request<{ dropped: string[]; status: AdmissionsStatus }>('/api/admin/admissions/scrub', { method: 'POST' }),
  inventory: () => request<{ courses: InventoryRow[]; admissions: AdmissionsStatus }>('/api/admin/inventory'),
  ei: (id: string, sid: string) => request<EIContext>(eiBase(id, sid)),
  eiMethodology: (id: string, sid: string) => request<{ markdown: string }>(`${eiBase(id, sid)}/methodology`),
  eiRecommend: (id: string, sid: string, body: EIInputs) =>
    request<{ recommended_modules: EIModule[] }>(`${eiBase(id, sid)}/recommend`, { method: 'POST', body: JSON.stringify(body) }),
  eiPrompt: (id: string, sid: string, body: EIPromptReq) =>
    request<EIPrompt>(`${eiBase(id, sid)}/prompt`, { method: 'POST', body: JSON.stringify(body) }),
  eiObjectives: (id: string, sid: string, model: string, temperature: number) =>
    request<{ objectives: string }>(`${eiBase(id, sid)}/objectives`, { method: 'POST', body: JSON.stringify({ model, temperature }) }),
  eiSession: (id: string, sid: string, sessionId: number) => request<EISessionFull>(`${eiBase(id, sid)}/sessions/${sessionId}`),
  eiDelete: (id: string, sid: string, sessionId: number) =>
    request<{ deleted: number }>(`${eiBase(id, sid)}/sessions/${sessionId}`, { method: 'DELETE' }),
  eiExportUrl: (id: string, sid: string, sessionId: number, part: 'plan' | 'workbook', fmt: 'html' | 'pdf' | 'md') =>
    `${eiBase(id, sid)}/sessions/${sessionId}/export?part=${part}&fmt=${fmt}`,
  setCourseOrder: (ids: string[]) =>
    request<{ order: string[]; course_sort: CourseSort }>('/api/me/course-order', { method: 'PUT', body: JSON.stringify({ course_ids: ids }) }),
  setCourseSort: (mode: CourseSort) =>
    request<{ course_sort: CourseSort }>('/api/me/course-sort', { method: 'PUT', body: JSON.stringify({ mode }) }),
  archiveCourse: (id: string, archived: boolean) =>
    request<{ archived: boolean }>(`/api/courses/${enc(id)}/archive`, { method: 'POST', body: JSON.stringify({ archived }) }),
  removalInfo: (id: string) => request<RemovalInfo>(`/api/courses/${enc(id)}/removal`),
  hideCourse: (id: string) => request<{ hidden: string }>(`/api/courses/${enc(id)}/hide`, { method: 'POST' }),
  restoreCourse: (id: string) => request<{ restored: string }>(`/api/me/hidden-courses/${enc(id)}/restore`, { method: 'POST' }),
  deleteCourse: (id: string, confirm: string) =>
    request<{ deleted: string; removed: string[] }>(`/api/courses/${enc(id)}/delete`, { method: 'POST', body: JSON.stringify({ confirm }) }),
  uploadExports: (id: string, files: File[], dryRun: boolean) =>
    postForm<UploadResult>(`/api/courses/${enc(id)}/uploads`, files, { dry_run: String(dryRun) }),
  addCourse: (files: File[], courseCode: string, name: string, dryRun: boolean) =>
    postForm<UploadResult>('/api/uploads/courses', files, { course_code: courseCode, name, dry_run: String(dryRun) }),
  mount: () => request<MountView>('/api/admin/mount'),
  pollNow: (courseId?: string) =>
    request<MountView & { summary: PollSummary }>(`/api/admin/mount/poll${courseId ? `?course_id=${enc(courseId)}` : ''}`, { method: 'POST' }),
  courseWatch: (id: string) => request<CourseWatch>(`/api/courses/${enc(id)}/watch`),
  prefs: () => request<Preferences>('/api/me/preferences'),
  savePrefs: (p: Preferences) => request<Preferences>('/api/me/preferences', { method: 'PUT', body: JSON.stringify(p) }),
}
