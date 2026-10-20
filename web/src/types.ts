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

export interface Project {
  id: string;
  title: string;
  subject: string;
  canvas: { aspect: Aspect; fps: number };
  voice: VoiceSettings;
  subtitle: SubtitleStyle;
  bgm: { asset_id: string | null; volume: number };
  shots: Shot[];
  assets: Record<string, Asset>;
  revision: number;
  updated_at: number;
}

export interface ProjectSummary {
  id: string;
  title: string;
  shots: number;
  aspect: Aspect;
  updated_at: number;
}

export interface ProviderInfo {
  id: string;
  name: string;
  paid: boolean;
  offline: boolean;
  configured: boolean;
  default: boolean;
  docs_url: string;
  fields: { key: string; label: string; required: boolean; secret: boolean }[];
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
