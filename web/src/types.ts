export type Aspect = "9:16" | "16:9" | "1:1";
export type VisualMode = "stock" | "ai" | "image" | "upload" | "color";

export interface VoiceSettings {
  provider: string;
  voice: string;
  rate: number;
  volume: number;
}

export interface SubtitleStyle {
  enabled: boolean;
  font: string;
  size: number;
  color: string;
  outline_color: string;
  outline: number;
  position: "top" | "middle" | "bottom";
  margin: number;
}

export interface PendingTask {
  provider: string;
  task_id: string;
  kind: "video_gen" | "image_gen";
  since: number;
}

export interface ShotVisual {
  mode: VisualMode;
  terms: string[];
  prompt: string;
  provider: string | null;
  candidates: string[];
  selected: string | null;
  first_frame: string | null;
  color: string;
  fit: "cover" | "contain";
  locked: boolean;
  /** 已提交、尚未取回结果的收费任务；服务重启后会继续轮询。 */
  pending?: PendingTask | null;
}

export interface Shot {
  id: string;
  text: string;
  duration: number | null;
  visual: ShotVisual;
  voice: VoiceSettings | null;
  status: string;
  error: string | null;
}

export interface Asset {
  id: string;
  kind: "video" | "image" | "audio";
  source: string;
  filename: string;
  width: number | null;
  height: number | null;
  duration: number | null;
  meta: Record<string, string>;
}

export type ReferenceRole = "product" | "person" | "logo" | "style";

export interface ReferenceAsset {
  asset_id: string;
  role: ReferenceRole;
  label: string;
  enabled: boolean;
}

export type OverlayPosition = "top" | "middle" | "bottom";
export type TransitionKind = "none" | "fade" | "slide" | "wipe";

export interface Overlay {
  id: string;
  text: string;
  image: string | null;
  start: number;
  duration: number | null;
  position: OverlayPosition;
  size: number;
  color: string;
  outline_color: string;
  margin: number;
  enabled: boolean;
}

export interface TransitionSettings {
  kind: TransitionKind;
  duration: number;
}

export interface Project {
  id: string;
  title: string;
  subject: string;
  language: string;
  target_seconds: number | null;
  /** 目标总时长（秒）；成片以实际配音为准。 */
  group: string | null;
  canvas: { aspect: Aspect; fps: number };
  voice: VoiceSettings;
  subtitle: SubtitleStyle;
  overlays: Overlay[];
  transition: TransitionSettings;
  bgm: { asset_id: string | null; candidates?: string[]; query?: string; volume: number };
  references: ReferenceAsset[];
  shots: Shot[];
  assets: Record<string, Asset>;
  /** 累计收费生成的参考费用（元）。 */
  cost_credits: number;
  revision: number;
  updated_at: number;
}

export interface ProjectSummary {
  id: string;
  title: string;
  shots: number;
  aspect: Aspect;
  language: string;
  group: string;
  updated_at: number;
}

export interface ProviderInfo {
  id: string;
  name: string;
  name_en: string;
  paid: boolean;
  offline: boolean;
  configured: boolean;
  default: boolean;
  docs_url: string;
  fields: { key: string; label: string; required: boolean; secret: boolean }[];
  capabilities: string[];
  max_duration: number | null;
  has_audio: boolean;
  video_to_video: boolean;
  max_ref_images: number;
  word_timestamps: boolean;
}

export type Providers = Record<string, ProviderInfo[]>;

export interface RenderResult {
  render_id: string;
  video: string;
  subtitles: string | null;
  duration: number;
  warnings: string[];
  stages: { key: string; kind: string; reused: boolean; seconds: number }[];
}

export interface Job {
  id: string;
  project_id: string;
  state: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: number;
  message: string;
  error: string | null;
  result: RenderResult | null;
  allow_paid: boolean;
}

/** 修改镜头画面时可以传的字段；clear_first_frame 取消首帧。 */
export type VisualPatch = Partial<ShotVisual> & { clear_first_frame?: boolean };

export interface LanguageInfo {
  code: string;
  name: string;
  name_zh: string;
  default_voice: string;
  font: string;
}

export interface VersionInfo {
  id: string;
  title: string;
  language: string;
  updated_at: number;
}

/** 背景音乐搜索结果中的单个候选（含试听地址，未下载）。 */
export interface BgmCandidate {
  provider: string;
  remote_id: string;
  url: string;
  preview_url: string;
  title: string;
  duration: number;
  author: string;
  meta: Record<string, string>;
}

export interface BgmSearchResult {
  query: string;
  candidates: BgmCandidate[];
}

export interface CostEstimateItem {
  shot_index: number;
  shot_id: string;
  visual_cost: number;
  tts_cost: number;
  note: string;
}

export interface CostEstimate {
  currency: string;
  total: number;
  total_visual: number;
  total_tts: number;
  items: CostEstimateItem[];
}
