/** Mirrors the backend Pydantic response models (backend/app/schemas). */

export type ExperienceLevel = 'intern' | 'entry' | 'mid' | 'senior' | 'staff'
export type Severity = 'high' | 'medium' | 'low'

export interface Profile {
  target_roles: string[]
  location: string | null
  remote_ok: boolean
  experience_level: ExperienceLevel | null
  updated_at: string
}

export interface Me {
  user_id: string
  /** null until onboarding saves a profile. */
  profile: Profile | null
}

export interface StructureCheck {
  id: string
  label: string
  passed: boolean
  points: number
  max_points: number
  detail: string
}

export interface ResumeFeedbackItem {
  check: string
  severity: Severity
  message: string
}

export interface ResumeSummary {
  id: string
  filename: string
  is_active: boolean
  /** 0-1; null if scoring failed. */
  structure_score: number | null
  created_at: string
}

export interface Resume extends ResumeSummary {
  structure_checks: StructureCheck[]
  general_feedback: ResumeFeedbackItem[]
  skills: { canonical: string; category: string; count: number; in_skills_section: boolean }[]
  suggested_roles: string[]
  sections: { key: string; heading: string }[]
  bullets: { section: string | null; text: string }[]
}

export interface SkillWeight {
  skill: string
  weight: number
}

export interface WeakRequirement {
  text: string
  section: string
  similarity: number
}

export interface AnalysisFeedbackItem {
  type: 'missing_skill' | 'weak_requirement' | 'structure' | 'strength'
  severity: Severity
  message: string
}

export interface AnalysisSummary {
  id: string
  resume_id: string
  job_id: string | null
  job_title: string | null
  /** 0-100. */
  final_score: number | null
  created_at: string
}

export interface Analysis extends AnalysisSummary {
  /** Each 0-1. skill_score is null when the posting named no recognized skills. */
  skill_score: number | null
  semantic_score: number | null
  structure_score: number | null
  tfidf_score: number | null
  matched_skills: SkillWeight[]
  missing_skills: SkillWeight[]
  weak_requirements: WeakRequirement[]
  feedback_items: AnalysisFeedbackItem[]
}

/** Body for POST /analyses: exactly one of job_description, job_id, job_url. */
export interface AnalysisCreate {
  resume_id: string
  job_description?: string
  job_id?: string
  job_url?: string
  job_title?: string
}

export interface ProfileUpdate {
  target_roles: string[]
  location: string | null
  remote_ok: boolean
  experience_level: ExperienceLevel | null
}

// ---------------------------------------------------------------- jobs and applications

export interface JobSummary {
  id: string
  company: string
  title: string
  location: string | null
  is_remote: boolean
  url: string
  level: ExperienceLevel | null
  min_years: number | null
  posted_at: string | null
  status: 'open' | 'closed'
}

export interface JobDetail extends JobSummary {
  description_text: string
  skills: { skill: string; category: string; weight: number }[]
  requirements: { section: string | null; text: string }[]
}

export interface RecommendationItem {
  job: JobSummary
  /** 0-100, after the eligibility penalty. */
  score: number
  subscores: { skill: number | null; semantic: number | null; structure: number }
  /** Job level minus the user's; null when either is unknown. */
  levels_above: number | null
  stretch: boolean
  matched_skills: SkillWeight[]
  missing_skills: SkillWeight[]
}

export interface Recommendations {
  resume_id: string
  items: RecommendationItem[]
}

export interface RecommendationFilters {
  remote?: boolean
  location?: string
  level?: ExperienceLevel
  show_stretch?: boolean
  limit?: number
}

export type ApplicationStatus = 'saved' | 'applied' | 'interviewing' | 'offer' | 'rejected'

export interface Application {
  id: string
  job: JobSummary
  status: ApplicationStatus
  notes: string | null
  created_at: string
  updated_at: string
}
