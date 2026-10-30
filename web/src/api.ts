import type {
  BgmCandidate,
  CheckIssue,
  PlatformInfo,
  BgmSearchResult,
  CostEstimate,
  GenerateAllStatus,
  GenerateJob,
  FontFamily,
  Job,
  LanguageInfo,
  MaterialNote,
  Project,
  PrecheckRisk,
  ProjectSummary,
  Providers,
  SettingsData,
  TranscriptData,
  CutDraft,
  RenderResult,
  Shot,
  ShotRole,
  VersionInfo,
  VideoType,
  VisualPatch,
  VoiceSettings,
} from "./types";

import { currentLang, translate } from "./i18n";

/** 生成选项：一次几条、先出几张关键帧、草稿还是成片分辨率。 */
export interface GenerateOptions {
  count?: number;
  keyframes?: number;
  quality?: "draft" | "final";
}

const BASE = "/api/v2";

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(BASE + path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      "Accept-Language": currentLang() === "zh" ? "zh-CN" : "en",
    },
  });
  if (!response.ok) {
    let message = translate("common.requestFailed", { status: response.status });
    try {
      const data = await response.json();
      if (typeof data.detail === "string") message = data.detail;
      // 页面已经是新版、后台服务还是旧版时，新接口会落到静态文件上，返回这两种通用错误。
      if (["Method Not Allowed", "Not Found", "接口不存在", "No such API endpoint"].includes(data.detail)) message = translate("common.serverOutdated");
      // 新页面传了新字段、后台还是旧版时，后台会拒绝"不认识的字段"：提示重启服务，而不是显示英文报错。
      else if (Array.isArray(data.detail) && data.detail.some((d: { type?: string }) => d.type === "extra_forbidden"))
        message = translate("common.serverOutdated");
      else if (Array.isArray(data.detail)) message = data.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* 响应不是 JSON */
    }
    throw new ApiError(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  providers: () => request<Providers>("/providers"),
  settings: () => request<SettingsData>("/settings"),
  transcript: (id: string, asset: string) => request<TranscriptData>(`/projects/${id}/transcripts/${asset}`),
  startTranscript: (id: string, asset: string, body: { provider?: string | null; language?: string | null } = {}) =>
    request<TranscriptData>(`/projects/${id}/transcripts/${asset}`, json("POST", body)),
  saveCutDraft: (
    id: string,
    asset: string,
    draft: { removed: number[]; texts: Record<string, string>; pauses: boolean; max_pause: number | null; scenes: boolean },
  ) => request<CutDraft>(`/projects/${id}/transcripts/${asset}/draft`, json("PUT", draft)),
  suggestRetakes: (id: string, asset: string) =>
    request<{ remove: number[] }>(`/projects/${id}/transcripts/${asset}/retakes`, json("POST")),
  cut: (
    id: string,
    body: { asset_id: string; removed: number[]; max_pause: number | null; split_scenes: boolean; texts: Record<string, string> },
  ) => request<Project>(`/projects/${id}/cut`, json("POST", body)),
  saveProviderSettings: (kind: string, id: string, values: Record<string, string>) =>
    request<SettingsData>(`/settings/providers/${kind}/${id}`, json("PUT", { values })),
  saveDefaultProvider: (kind: string, provider: string) =>
    request<SettingsData>("/settings/defaults", json("PUT", { kind, provider })),
  saveAppSettings: (proxy: string) => request<SettingsData>("/settings/app", json("PUT", { proxy })),
  testProvider: (kind: string, id: string) =>
    request<{ ok: boolean; message: string }>(`/settings/providers/${kind}/${id}/test`, json("POST")),
  voices: (language?: string, provider?: string) => {
    const params = new URLSearchParams();
    if (language) params.set("language", language);
    if (provider) params.set("provider", provider);
    const query = params.toString();
    return request<{ id: string; name: string; language: string | null }[]>(
      query ? `/voices?${query}` : "/voices",
    );
  },
  languages: () => request<LanguageInfo[]>("/languages"),
  versions: (id: string) => request<VersionInfo[]>(`/projects/${id}/versions`),
  createVersion: (id: string, language: string) => request<Project>(`/projects/${id}/versions`, json("POST", { language })),
  listProjects: () => request<ProjectSummary[]>("/projects"),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (body: {
    mode: "llm" | "split" | "empty";
    title?: string;
    idea?: string;
    language?: string;
    subject?: string;
    script?: string;
    canvas?: { aspect: string; fps?: number };
    voice?: Partial<VoiceSettings>;
    /** 为空时由大模型按目标时长和诉求自行决定镜头数。 */
    shots?: number | null;
    /** 目标总时长（秒）；为空时由大模型估算。 */
    duration?: number | null;
    video_type?: VideoType | null;
    /** cut：剪辑页建的任务，在「剪辑」的任务列表里显示。 */
    origin?: "create" | "cut";
  }) => request<Project>("/projects", json("POST", body)),
  updateProject: (
    id: string,
    body: Partial<
      Pick<
        Project,
        "title" | "video_type" | "look" | "titles" | "chain_frames" | "budget" | "sfx" | "delivery" | "language" | "canvas" | "voice" | "subtitle" | "bgm" | "loudness" | "references" | "overlays" | "transition" | "idea" | "script" | "subject" | "ai_badge" | "pronunciations"
      >
    >,
  ) =>
    request<Project>(`/projects/${id}`, json("PATCH", body)),
  /** AI 生成标题：候选大标题、片头副标题、镜头小标题（已有的不覆盖）。 */
  suggestTitles: (id: string, options: { shot_titles?: boolean; apply_headline?: boolean } = {}) =>
    request<Project>(`/projects/${id}/titles/suggest`, json("POST", options)),
  /** 把随手写的想法按视频类型的模板整理成完整诉求（不建项目、不收费）。 */
  normalizeBrief: (body: {
    idea: string;
    template: string;
    video_type: VideoType;
    language: string;
    aspect: string;
    duration: number;
    materials: MaterialNote[];
  }) => request<{ brief: string }>("/brief/normalize", json("POST", body)),
  /** 按诉求写口播脚本，每段一个镜头（不建项目、不收费）。 */
  writeScript: (body: { brief: string; video_type: VideoType; language: string; aspect: string; duration: number; materials: MaterialNote[] }) =>
    request<{ title: string; lines: string[]; seconds: number }>("/brief/script", json("POST", body)),
  /** 给项目写分镜：有定稿脚本时一段一个镜头；上传的素材放进主体、主体库、镜头和背景音乐。 */
  storyboard: (id: string, body: { subject: string; idea?: string; script: string[] | null; duration: number; materials: MaterialNote[] }) =>
    request<Project>(`/projects/${id}/storyboard`, json("POST", body)),
  /** 把新建时上传的素材放到该放的地方（不调用大模型）。 */
  applyMaterials: (id: string, materials: MaterialNote[]) => request<Project>(`/projects/${id}/materials`, json("POST", { materials })),
  /** 给全片设定里的主体生成定妆图（AI 图片，收费，调用前要让用户确认）。 */
  /** 确认素材已准备好，之后才能生成 AI 画面；没准备好时返回 409 并说明缺什么。 */
  confirmLook: (id: string) => request<Project>(`/projects/${id}/look/confirm`, json("POST", {})),
  subjectImage: (id: string, code: string) =>
    request<Project>(`/projects/${id}/look/${encodeURIComponent(code)}/image`, json("POST", { confirm_charge: true })),
  /** 看主体的参考图写外观描述（领型、有无帽子等），只返回文字。 */
  describeSubject: (id: string, code: string) =>
    request<{ appearance: string }>(`/projects/${id}/look/${encodeURIComponent(code)}/describe`, json("POST", {})),
  /** AI 检查文案里容易读错的词，追加到读音纠正表。 */
  suggestPronunciations: (id: string) => request<Project>(`/projects/${id}/pronunciations/suggest`, json("POST", {})),
  /** 按当前配音设置和读音纠正合成一句试听，返回音频。 */
  previewVoice: async (id: string, text: string): Promise<Blob> => {
    const response = await fetch(`${BASE}/projects/${id}/voice/preview`, {
      ...json("POST", { text }),
      headers: { "Content-Type": "application/json", "Accept-Language": currentLang() === "zh" ? "zh-CN" : "en" },
    });
    if (!response.ok) {
      let message = translate("common.requestFailed", { status: response.status });
      try {
        const data = await response.json();
        if (typeof data.detail === "string") message = data.detail;
        if (["Method Not Allowed", "Not Found", "接口不存在", "No such API endpoint"].includes(data.detail)) message = translate("common.serverOutdated");
      } catch {
        /* 响应不是 JSON */
      }
      throw new ApiError(message);
    }
    return response.blob();
  },
  /** 从镜头的候选素材里删掉一个。 */
  removeCandidate: (id: string, shotId: string, assetId: string) =>
    request<Project>(`/projects/${id}/shots/${shotId}/candidates/${assetId}`, json("DELETE")),
  /** AI 从文案里挑字幕高亮关键词（替换 subtitle.keywords）。 */
  suggestKeywords: (id: string) => request<Project>(`/projects/${id}/subtitle/keywords`, json("POST", {})),
  /** 复制成新项目继续编辑（素材、设置、镜头和渲染缓存一起复制）。 */
  duplicateProject: (id: string, title = "") => request<Project>(`/projects/${id}/duplicate`, json("POST", { title })),
  deleteProject: (id: string) => request<void>(`/projects/${id}`, json("DELETE")),
  addShot: (id: string, after?: string) => request<Project>(`/projects/${id}/shots`, json("POST", { text: "", after })),
  splitShot: (id: string, shotId: string, at: number) =>
    request<Project>(`/projects/${id}/shots/${shotId}/split`, json("POST", { at })),
  /** 把配音文案从中间最近的句子处拆成两个镜头。 */
  splitText: (id: string, shotId: string) => request<Project>(`/projects/${id}/shots/${shotId}/split-text`, json("POST", {})),
  /** 生成前检查：画面描述的高风险内容、配音是否超过模型单次上限。不花钱。 */
  precheck: (id: string, shotId: string, prompt?: string) =>
    request<{ risks: PrecheckRisk[] }>(`/projects/${id}/shots/${shotId}/precheck`, json("POST", prompt === undefined ? {} : { prompt })),
  mergeShot: (id: string, shotId: string) => request<Project>(`/projects/${id}/shots/${shotId}/merge`, json("POST", {})),
  reorder: (id: string, order: string[]) => request<Project>(`/projects/${id}/shots/order`, json("PUT", { order })),
  updateShot: (
    id: string,
    shotId: string,
    body: {
      text?: string;
      title?: string;
      duration?: number;
      clear_duration?: boolean;
      role?: ShotRole;
      visual?: VisualPatch;
      speech?: "tts" | "original";
    },
  ) => request<Project>(`/projects/${id}/shots/${shotId}`, json("PATCH", body)),
  deleteShot: (id: string, shotId: string) => request<Project>(`/projects/${id}/shots/${shotId}`, json("DELETE")),
  /** 收费的 AI 生成必须传 confirmCharge=true（用户已确认），否则后端返回 402。 */
  candidates: (id: string, shotId: string, confirmCharge = false, editSelected = false, options: GenerateOptions = {}) =>
    request<Project>(
      `/projects/${id}/shots/${shotId}/candidates`,
      json("POST", { confirm_charge: confirmCharge, edit_selected: editSelected, ...options }),
    ),
  /** 单镜头 AI 改写：只改画面描述、运镜和衔接，不生成画面、不收费。 */
  rewriteShot: (id: string, shotId: string, instruction: string) =>
    request<Project>(`/projects/${id}/shots/${shotId}/rewrite`, json("POST", { instruction })),
  /** 一键补全：扩写过短的画面描述、补衔接说明，没有全片设定时一并生成。 */
  expand: (id: string, onlyShort = true) => request<Project>(`/projects/${id}/expand`, json("POST", { only_short: onlyShort })),
  /** 一键生成：要生成哪些镜头、预计费用，以及最近一次的进度。 */
  generateAllStatus: (id: string, quality: "draft" | "final" = "final", onlyRedo = false) =>
    request<GenerateAllStatus>(`/projects/${id}/generate-all?quality=${quality}&only_redo=${onlyRedo}`),
  generateAll: (id: string, body: { confirm_charge: boolean; quality: "draft" | "final"; only_redo?: boolean }) =>
    request<GenerateJob>(`/projects/${id}/generate-all`, json("POST", body)),
  cancelGenerateAll: (id: string) => request<GenerateJob>(`/projects/${id}/generate-all/cancel`, json("POST")),
  /** 服务器上正在检索或生成的镜头（页面刷新后用来恢复"生成中"状态）。 */
  inProgress: (id: string) => request<Record<string, { mode: string; seconds: number; queued?: boolean }>>(`/projects/${id}/in-progress`),
  /** 检索背景音乐候选（只返回元数据+试听地址，不下载）；query 留空时由服务端用大模型提炼。 */
  bgmCandidates: (id: string, query = "") =>
    request<BgmSearchResult>(`/projects/${id}/bgm/candidates`, json("POST", { query })),
  /** 下载选中的候选，注册为素材并设为当前背景音乐，返回更新后的项目。 */
  bgmDownload: (id: string, candidate: BgmCandidate) =>
    request<Project>(`/projects/${id}/bgm/download`, json("POST", candidate)),
  /** 上传素材。`asAudio` 用于背景音乐：音乐文件常装在视频容器里，需要按音频登记。 */
  upload: (id: string, file: File, asAudio = false) => {
    const form = new FormData();
    form.append("file", file);
    const query = asAudio ? "?as_audio=true" : "";
    return request<{ id: string; kind: string }>(`/projects/${id}/assets${query}`, {
      method: "POST",
      body: form,
    });
  },
  /** 导入原片并按画面变化自动切成镜头。 */
  importFootage: (id: string, file: File, replaceShots = false) => {
    const form = new FormData();
    form.append("file", file);
    const query = replaceShots ? "?replace_shots=true" : "";
    return request<{ source_asset: string; shots: string[]; segments: number }>(
      `/projects/${id}/footage${query}`,
      { method: "POST", body: form },
    );
  },
  render: (id: string, confirmCharge = false, quality: "draft" | "final" = "final") =>
    request<Job>(`/projects/${id}/renders`, json("POST", { confirm_charge: confirmCharge, quality })),
  platforms: () => request<PlatformInfo[]>("/platforms"),
  /** 字体库：内置字体和导入的字体，按字体族分组。 */
  fonts: () => request<FontFamily[]>("/fonts"),
  /** 导入自己的字体（.ttf / .otf），所有项目都能用。 */
  importFont: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ file: string; family: string }>("/fonts", { method: "POST", body: form });
  },
  deleteFont: (name: string) => request<void>(`/fonts/${encodeURIComponent(name)}`, { method: "DELETE" }),
  searchAmbience: (id: string, query = "") =>
    request<Project>(`/projects/${id}/sfx/ambience`, json("POST", { query })),
  checkup: (id: string, quality: "draft" | "final" = "final") =>
    request<{ issues: CheckIssue[] }>(`/projects/${id}/checkup?quality=${quality}`),
  costEstimate: (id: string, quality: "draft" | "final" = "final") =>
    request<CostEstimate>(`/projects/${id}/cost-estimate?quality=${quality}`),
  job: (jobId: string) => request<Job>(`/renders/${jobId}`),
  cancel: (jobId: string) => request<{ ok: boolean }>(`/renders/${jobId}/cancel`, json("POST")),
  renders: (id: string) =>
    request<{ jobs: Job[]; renders: (RenderResult & { created_at: number })[] }>(`/projects/${id}/renders`),
};

export const assetUrl = (projectId: string, assetId: string) => `${BASE}/projects/${projectId}/assets/${assetId}`;
export const renderUrl = (projectId: string, renderId: string, name: string) =>
  `${BASE}/projects/${projectId}/renders/${renderId}/${name}`;

/** 与后端 SilentTTS.estimate_seconds 一致的时长估算。 */
const PAUSE_MARK = /\[(?:停顿|pause)(?:\s+(\d+(?:\.\d+)?))?\]/gi;
const EMPHASIS_MARK = /\*([^*\n]{1,60})\*/g;

/** 去掉配音标注（[停顿]、*强调*），和后端 services/speech.plain 一致。 */
export function plainText(text: string): string {
  return text.replace(PAUSE_MARK, "").replace(EMPHASIS_MARK, "$1").trim();
}

/** 标注带来的额外静音（秒）。 */
function markPauses(text: string): number {
  let total = 0;
  for (const match of text.matchAll(PAUSE_MARK)) total += Math.min(3, Math.max(0.1, match[1] ? Number(match[1]) : 0.5));
  total += 0.12 * (text.match(EMPHASIS_MARK)?.length ?? 0);
  return total;
}

export function estimateSeconds(shot: Shot, rate: number): number {
  if (shot.duration) return shot.duration;
  const text = plainText(shot.text);
  const cjk = (text.match(/[぀-ヿ㐀-䶿一-鿿가-힯]/g) || []).length;
  const words = (text.replace(/[぀-ヿ㐀-䶿一-鿿가-힯]/g, " ").match(/[A-Za-z0-9']+/g) || []).length;
  const seconds = Math.max(1.5, (cjk / 4.5 + words / 2.6) / Math.max(rate, 0.1)) + markPauses(shot.text);
  return Math.round((seconds + 0.25) * 10) / 10;
}

export const fontUrl = (name: string) => `${BASE}/fonts/${encodeURIComponent(name)}`;
