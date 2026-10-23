import type {
  BgmCandidate,
  BgmSearchResult,
  CostEstimate,
  Job,
  LanguageInfo,
  Project,
  ProjectSummary,
  Providers,
  RenderResult,
  Shot,
  VersionInfo,
  VisualPatch,
  VoiceSettings,
} from "./types";

import { currentLang, translate } from "./i18n";

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
    language?: string;
    subject?: string;
    script?: string;
    canvas?: { aspect: string; fps?: number };
    voice?: Partial<VoiceSettings>;
    /** 为空时由大模型按目标时长和诉求自行决定镜头数。 */
    shots?: number | null;
    /** 目标总时长（秒）；为空时由大模型估算。 */
    duration?: number | null;
  }) => request<Project>("/projects", json("POST", body)),
  updateProject: (
    id: string,
    body: Partial<
      Pick<
        Project,
        "title" | "language" | "canvas" | "voice" | "subtitle" | "bgm" | "references" | "overlays" | "transition" | "ai_badge"
      >
    >,
  ) =>
    request<Project>(`/projects/${id}`, json("PATCH", body)),
  deleteProject: (id: string) => request<void>(`/projects/${id}`, json("DELETE")),
  addShot: (id: string, after?: string) => request<Project>(`/projects/${id}/shots`, json("POST", { text: "", after })),
  splitShot: (id: string, shotId: string, at: number) =>
    request<Project>(`/projects/${id}/shots/${shotId}/split`, json("POST", { at })),
  mergeShot: (id: string, shotId: string) => request<Project>(`/projects/${id}/shots/${shotId}/merge`, json("POST", {})),
  reorder: (id: string, order: string[]) => request<Project>(`/projects/${id}/shots/order`, json("PUT", { order })),
  updateShot: (
    id: string,
    shotId: string,
    body: { text?: string; duration?: number; clear_duration?: boolean; visual?: VisualPatch },
  ) => request<Project>(`/projects/${id}/shots/${shotId}`, json("PATCH", body)),
  deleteShot: (id: string, shotId: string) => request<Project>(`/projects/${id}/shots/${shotId}`, json("DELETE")),
  /** 收费的 AI 生成必须传 confirmCharge=true（用户已确认），否则后端返回 402。 */
  candidates: (id: string, shotId: string, confirmCharge = false, editSelected = false) =>
    request<Project>(
      `/projects/${id}/shots/${shotId}/candidates`,
      json("POST", { confirm_charge: confirmCharge, edit_selected: editSelected }),
    ),
  /** 服务器上正在检索或生成的镜头（页面刷新后用来恢复"生成中"状态）。 */
  inProgress: (id: string) => request<Record<string, { mode: string; seconds: number }>>(`/projects/${id}/in-progress`),
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
  costEstimate: (id: string) => request<CostEstimate>(`/projects/${id}/cost-estimate`),
  job: (jobId: string) => request<Job>(`/renders/${jobId}`),
  cancel: (jobId: string) => request<{ ok: boolean }>(`/renders/${jobId}/cancel`, json("POST")),
  renders: (id: string) =>
    request<{ jobs: Job[]; renders: (RenderResult & { created_at: number })[] }>(`/projects/${id}/renders`),
};

export const assetUrl = (projectId: string, assetId: string) => `${BASE}/projects/${projectId}/assets/${assetId}`;
export const renderUrl = (projectId: string, renderId: string, name: string) =>
  `${BASE}/projects/${projectId}/renders/${renderId}/${name}`;

/** 与后端 SilentTTS.estimate_seconds 一致的时长估算。 */
export function estimateSeconds(shot: Shot, rate: number): number {
  if (shot.duration) return shot.duration;
  const cjk = (shot.text.match(/[぀-ヿ㐀-䶿一-鿿가-힯]/g) || []).length;
  const words = (shot.text.replace(/[぀-ヿ㐀-䶿一-鿿가-힯]/g, " ").match(/[A-Za-z0-9']+/g) || [])
    .length;
  const seconds = Math.max(1.5, (cjk / 4.5 + words / 2.6) / Math.max(rate, 0.1));
  return Math.round((seconds + 0.25) * 10) / 10;
}
