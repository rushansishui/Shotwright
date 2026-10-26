export type Aspect = "9:16" | "16:9" | "1:1";
export type VisualMode = "stock" | "ai" | "image" | "upload" | "color";

export interface VoiceSettings {
  provider: string;
  voice: string;
  rate: number;
  volume: number;
}

/** 字幕自动边距占画面高度的比例，和后端 models.SUBTITLE_EDGE 一致。 */
export const SUBTITLE_EDGE: Record<string, number> = { "9:16": 0.09, "16:9": 0.07, "1:1": 0.07 };

export function subtitleMargin(style: { margin: number | null }, aspect: string): number {
  const height = aspect === "9:16" ? 1920 : 1080;
  return style.margin ?? Math.round(height * (SUBTITLE_EDGE[aspect] ?? 0.07));
}

export type TextAnimation = "none" | "fade" | "pop" | "slide_up" | "typewriter" | "karaoke";
export type TextExit = "none" | "fade" | "shrink" | "slide_down";
export type TextLoop = "none" | "pulse" | "swing";
export type TextAlign = "left" | "center" | "right";

/** 字幕和标题共用的文字样式（对标剪映 / CapCut 的文字面板）。 */
export interface TextStyle {
  /** 字体文件名；标题为 null 时跟随字幕字体。 */
  font: string | null;
  bold: boolean;
  italic: boolean;
  underline: boolean;
  color: string;
  opacity: number;
  outline: number;
  outline_color: string;
  shadow: number;
  shadow_color: string;
  shadow_opacity: number;
  /** 阴影方向（度）：0 向右，90 向下。 */
  shadow_angle: number;
  shadow_blur: number;
  /** 第二层描边（套在第一层外面），0 表示不加。 */
  outline2: number;
  outline2_color: string;
  glow: number;
  background: string;
  background_opacity: number;
  spacing: number;
  /** 行间距（像素）。 */
  line_spacing: number;
  align: TextAlign;
  animation: TextAnimation;
  exit: TextExit;
  loop: TextLoop;
  highlight_color: string;
}

export const TEXT_STYLE_DEFAULTS: TextStyle = {
  font: null,
  bold: true,
  italic: false,
  underline: false,
  color: "#FFFFFF",
  opacity: 1,
  outline: 3,
  outline_color: "#000000",
  shadow: 0,
  shadow_color: "#000000",
  shadow_opacity: 0.6,
  shadow_angle: 45,
  shadow_blur: 0,
  outline2: 0,
  outline2_color: "#FFFFFF",
  glow: 0,
  background: "#000000",
  background_opacity: 0,
  spacing: 0,
  line_spacing: 0,
  align: "center",
  animation: "none",
  exit: "none",
  loop: "none",
  highlight_color: "#FFE45C",
};

export interface SubtitleStyle extends TextStyle {
  enabled: boolean;
  font: string;
  size: number;
  position: "top" | "middle" | "bottom";
  /** 距边缘的距离；null 表示按画面比例自动。 */
  margin: number | null;
  /** 关键词高亮：字幕里出现这些词时换色并放大。 */
  keywords: string[];
  keyword_color: string;
  keyword_scale: number;
}

/** 字体库里的一个字体族（内置或用户导入）。 */
export interface FontFamily {
  family: string;
  name_zh: string;
  name_en: string;
  /** 选这个字体时写进项目的文件。 */
  file: string;
  files: string[];
  /** 每个文件的字重（常规 400、粗体 700）。 */
  weights: Record<string, number>;
  has_bold: boolean;
  user: boolean;
}

export interface PendingTask {
  provider: string;
  task_id: string;
  kind: "video_gen" | "image_gen";
  since: number;
}

export type FitMode = "cover" | "contain" | "blur";
export type FillMode = "slow_hold" | "hold" | "loop";

/** 镜头运动：自动、固定、推、拉、摇、移（跟拍）、环绕、升降、手持。 */
export type CameraMove = "auto" | "static" | "push_in" | "pull_out" | "pan" | "truck" | "orbit" | "crane" | "handheld";

export type ShotRole = "hook" | "product" | "demo" | "scene" | "people" | "explain" | "talking" | "cta";

export type SubjectKind = "product" | "person" | "object" | "place";

export interface LookSubject {
  code: string;
  name: string;
  kind: SubjectKind;
  appearance: string;
  reference?: string | null;
}

/** 全片设定：主体代号与外观、场景、视觉风格、禁止项，生成每个镜头时自动拼进提示词。 */
export interface Look {
  subjects: LookSubject[];
  scene: string;
  style: string;
  avoid: string;
  style_ref?: string | null;
  grade?: string;
  brand?: string | null;
}

/** 一次生成的版本记录：关键帧、多候选、再来一版共用。 */
export interface Take {
  asset_id: string;
  kind: "video" | "image" | "keyframe" | "edit" | "extend";
  batch: string;
  prompt: string;
  /** 检查得分（0–1）；没检查时为 null。 */
  score: number | null;
  issue: string;
  created_at: number;
}

export interface HeadlineStyle extends TextStyle {
  enabled: boolean;
  text: string;
  size: number;
  margin: number;
}

export interface IntroCard extends TextStyle {
  enabled: boolean;
  text: string;
  subtitle: string;
  duration: number;
  size: number;
  dim: number;
}

export interface ShotTitleStyle extends TextStyle {
  size: number;
  position: OverlayPosition;
  margin: number;
}

export interface TitleSettings {
  headline: HeadlineStyle;
  intro: IntroCard;
  shot: ShotTitleStyle;
  candidates: string[];
}

export interface ShotVisual {
  mode: VisualMode;
  terms: string[];
  prompt: string;
  /** AI 图片和关键帧用的静态画面描述；为空时用 prompt。 */
  image_prompt?: string;
  camera?: CameraMove;
  /** 衔接说明：开头怎样承接上一个镜头，结尾留给下一个镜头什么。 */
  link?: string;
  provider: string | null;
  candidates: string[];
  selected: string | null;
  /** 多选：在选中的素材之后依次播放的其他素材。 */
  sequence?: string[];
  first_frame: string | null;
  color: string;
  fit: FitMode;
  /** 铺满裁切时的取景中心，0–1。 */
  focus_x: number;
  focus_y: number;
  /** 视频素材的入点、出点（秒）；出点为 null 表示到结尾。 */
  trim_start: number;
  trim_end: number | null;
  /** 入出点和取景是针对哪个素材设的；和 selected 不同时按默认处理。 */
  edit_asset: string | null;
  fill: FillMode;
  /** 原声音量，0 表示不用原声。 */
  source_volume: number;
  locked: boolean;
  /** 已提交、尚未取回结果的收费任务；服务重启后会继续轮询。 */
  pending?: PendingTask | null;
  takes?: Take[];
}

export interface Shot {
  id: string;
  text: string;
  duration: number | null;
  visual: ShotVisual;
  /** 镜头小标题：这个镜头出现期间显示。 */
  title?: string;
  /** 镜头类型：开场钩子、产品亮相、使用演示、场景氛围、人物、原理示意、口播、结尾引导。 */
  role?: ShotRole | null;
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

/** 音效层：转场音效和环境声。 */
export interface SfxSettings {
  transitions: boolean;
  transition_volume: number;
  ambience: string | null;
  ambience_volume: number;
  ambience_query: string;
}

/** 视频类型：商品广告、品牌宣传、知识科普、口播、原片改造。 */
export type VideoType = "product_ad" | "brand" | "explainer" | "talking" | "footage";

/** 读音纠正：配音时把 word 换成读音相同的 say，字幕仍显示 word。 */
export interface Pronunciation {
  word: string;
  say: string;
  note: string;
}

export interface Project {
  id: string;
  title: string;
  subject: string;
  video_type?: VideoType | null;
  look?: Look;
  /** 标题：常驻大标题、片头标题卡、镜头小标题样式。 */
  titles?: TitleSettings;
  /** 首尾帧衔接：上一个 AI 镜头的最后一帧作为下一个镜头的首帧。 */
  chain_frames?: boolean;
  language: string;
  /** 读音纠正表。 */
  pronunciations?: Pronunciation[];
  target_seconds: number | null;
  /** 目标总时长（秒）；成片以实际配音为准。 */
  group: string | null;
  canvas: { aspect: Aspect; fps: number };
  voice: VoiceSettings;
  subtitle: SubtitleStyle;
  overlays: Overlay[];
  transition: TransitionSettings;
  ai_badge: boolean;
  bgm: {
    asset_id: string | null;
    candidates?: string[];
    query?: string;
    volume: number;
    /** 有配音时自动压低背景音乐。 */
    duck?: boolean;
    fade_in?: number;
    fade_out?: number;
    /** 镜头切换点就近对齐音乐拍点。 */
    beat_sync?: boolean;
  };
  sfx?: SfxSettings;
  /** 交付平台：决定编码参数和字幕、叠加文字的安全区。 */
  delivery?: string;
  /** 导出响度（LUFS）；null 表示不处理。 */
  loudness?: number | null;
  references: ReferenceAsset[];
  shots: Shot[];
  assets: Record<string, Asset>;
  /** 累计收费生成的参考费用（元）。 */
  cost_credits: number;
  /** 项目费用上限（元）；null 表示不限。 */
  budget?: number | null;
  revision: number;
  updated_at: number;
}

/** 交付平台规格（来自后端唯一的平台规格表）。 */
export interface PlatformInfo {
  id: string;
  name: string;
  name_en: string;
  aspects: Aspect[];
  /** 安全区：四周被平台界面遮住的比例。 */
  safe: { top: number; bottom: number; left: number; right: number };
}

/** 渲染前体检发现的一个问题。 */
export interface CheckIssue {
  code: string;
  message: string;
  shot: number | null;
  params: Record<string, string | number> | null;
  level: string;
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
  /** 成片抽帧检查发现的问题（配置了看图模型、成片档时才有）。 */
  inspection?: { shot: number; issues: string[] }[];
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
export type VisualPatch = Partial<ShotVisual> & { clear_first_frame?: boolean; clear_trim_end?: boolean };

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
