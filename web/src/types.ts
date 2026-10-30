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
  /** 参考图（素材 id）：产品实拍、人物照片，生成 AI 画面时作为参考。 */
  images?: string[];
  /** 参考图的颜色/款式（素材 id → 「浅粉色」这类说明），写进提示词，让模型照着这一款的颜色画。 */
  image_labels?: Record<string, string>;
  /** 用户确认这个主体不需要参考图（例如路人），准备清单里算作就绪。 */
  no_reference?: boolean;
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
  /** 素材是否已确认（服务端维护）：false 时不能生成 AI 画面；null 是旧项目，按已确认处理。 */
  confirmed?: boolean | null;
  /** 确认之后又改了设定的时间（秒）。 */
  revised_at?: number | null;
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
  /** 这个镜头用哪些主体参考图；为空时用各主体的主图（第一张）。 */
  refs?: string[];
  color: string;
  fit: FitMode;
  /** 铺满裁切时的取景中心，0–1。 */
  focus_x: number;
  focus_y: number;
  /** 视频素材的入点、出点（秒）；出点为 null 表示到结尾。 */
  trim_start: number;
  trim_end: number | null;
  /** 入出点之间剪掉的片段（素材秒数），按文字剪辑时生成。 */
  skips?: [number, number][];
  /** 入出点和取景是针对哪个素材设的；和 selected 不同时按默认处理。 */
  edit_asset: string | null;
  fill: FillMode;
  /** 原声音量，0 表示不用原声。 */
  source_volume: number;
  locked: boolean;
  /** 已提交、尚未取回结果的收费任务；服务重启后会继续轮询。 */
  pending?: PendingTask | null;
  takes?: Take[];
  /** 审片结论：ok 通过，redo 重做；空表示还没审。 */
  review?: "" | "ok" | "redo";
  /** 标记重做时的一句修改意见。 */
  review_note?: string;
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
  /** original：原片里的人声（剪辑页生成），不再配音，文案只做字幕。 */
  speech?: "tts" | "original";
  status: string;
  error: string | null;
}

/** 修改镜头时可以传的字段。 */
export interface ShotUpdate {
  text?: string;
  title?: string;
  role?: ShotRole;
  visual?: VisualPatch;
  speech?: "tts" | "original";
}

/** 剪辑页：原片的语音识别结果。words 是 [文字, 开始秒, 结束秒]。 */
export interface TranscriptData {
  state: "none" | "running" | "done" | "failed";
  progress?: number;
  /** 识别进行到哪一步：extract 抽音轨 / model 准备模型 / download 下载模型 / load 加载模型 / recognize 识别中。 */
  stage?: "extract" | "model" | "download" | "load" | "recognize";
  elapsed?: number;
  error?: string;
  language?: string;
  /** 识别时选的语言；和 language 不同时说明已按实际说的语言识别。 */
  requested_language?: string | null;
  duration?: number | null;
  words?: [string, number, number][];
  sentences?: [number, number][];
  fillers?: number[];
  /** 剪辑进度（服务端保存）。 */
  draft?: CutDraft;
}

export interface CutDraft {
  removed: number[];
  texts: Record<string, string>;
  pauses: boolean;
  max_pause: number | null;
  scenes: boolean;
  saved_at?: number;
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

export type MaterialKind = "image" | "video" | "audio";
/** 素材用途：产品图、人物照片、Logo、风格参考、画面素材（直接当镜头画面）、背景音乐。 */
export type MaterialRole = "product" | "person" | "logo" | "style" | "footage" | "bgm";

/** 新建视频时交给后端的素材说明；上传后带上 asset_id。 */
export interface MaterialNote {
  kind: MaterialKind;
  role: MaterialRole;
  name?: string;
  note?: string;
  asset_id?: string;
}

/** 读音纠正：配音时把 word 换成读音相同的 say，字幕仍显示 word。 */
export interface Pronunciation {
  word: string;
  say: string;
  note: string;
}

export interface Project {
  /** create：创作里生成的视频；cut：剪辑里导入已有视频的任务。 */
  origin?: "create" | "cut";
  id: string;
  title: string;
  subject: string;
  /** 用户最初随手写的想法（创作记录）。 */
  idea?: string;
  /** 生成分镜时用的脚本，每段一个镜头（创作记录，之后改镜头文案不影响）。 */
  script?: string[];
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
/** 生成前检查发现的风险（见后端 services/precheck.py）。 */
export interface PrecheckRisk {
  code: "hands" | "text" | "people" | "motion" | "camera" | "sequence" | "duplicate" | "too_long";
  params: Record<string, string | number>;
  message: string;
  advice: string;
}

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
  created_at?: number;
  /** 最近一次成片；stale 表示成片之后项目又改过。 */
  render?: { id: string; duration: number | null; created_at: number; stale: boolean } | null;
  /** 封面图片地址（最近的成片或第一个有画面的镜头）；没有画面时为空。 */
  cover?: string | null;
  /** create：创作里生成的视频；cut：剪辑里导入已有视频的任务。 */
  origin?: "create" | "cut";
  /** 剪辑任务还没生成镜头时，原片的素材 id（回到剪辑页用）。 */
  source_asset?: string | null;
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
  /** 「怎么开通」：去哪里注册、拿哪个 Key、要开通什么、怎么收费。 */
  setup?: string;
  setup_en?: string;
  /** 自己的 Key 留空时借用的服务（例如 Seedance 借用豆包的方舟 Key）。 */
  shares_key_with?: { kind: string; id: string }[];
  fields: { key: string; label: string; required: boolean; secret: boolean; default?: string; help?: string }[];
  required_any?: string[][];
  testable?: boolean;
  capabilities: string[];
  max_duration: number | null;
  has_audio: boolean;
  video_to_video: boolean;
  max_ref_images: number;
  word_timestamps: boolean;
}

export type Providers = Record<string, ProviderInfo[]>;

/** 设置页：config.toml 里填写的值（密钥只有是否已填和后 4 位）。 */
export interface SettingsData {
  config_path: string;
  editable: boolean;
  proxy: string;
  defaults: Record<string, string>;
  providers: Record<string, Record<string, { configured: boolean; fields: Record<string, { set: boolean; value: string }> }>>;
}

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

export interface GeneratePlanItem {
  shot_id: string;
  shot_index: number;
  reason: "missing" | "redo";
  mode: VisualMode;
  provider: string | null;
  paid: boolean;
  cost: number;
  note: string;
  review_note: string;
}

export interface GeneratePlan {
  items: GeneratePlanItem[];
  total: number;
  paid: boolean;
  spent: number;
  budget: number | null;
  currency: string;
}

export interface GenerateJob {
  state: "running" | "done" | "cancelled" | "stopped" | "interrupted";
  total: number;
  shot_ids: string[];
  quality: "draft" | "final";
  estimate: number;
  current: string | null;
  done: string[];
  failed: { shot_id: string; error: string }[];
  skipped: string[];
  cancel_requested: boolean;
  started_at: number;
  finished_at: number | null;
  stopped_reason: string;
}

export interface GenerateAllStatus {
  plan: GeneratePlan;
  job: GenerateJob | null;
}
