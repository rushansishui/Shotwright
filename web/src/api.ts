import type { Job, Project, ProjectSummary, Providers, RenderResult, Shot, ShotVisual, VoiceSettings } from "./types";

const BASE = "/api/v2";

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(BASE + path, {
    ...init,
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
  });
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") message = data.detail;
      else if (Array.isArray(data.detail)) message = data.detail.map((d: { msg: string }) => d.msg).join("；");
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
  voices: () => request<{ id: string; name: string }[]>("/voices"),
  listProjects: () => request<ProjectSummary[]>("/projects"),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (body: {
    mode: "llm" | "split" | "empty";
    subject?: string;
    script?: string;
    canvas?: { aspect: string; fps?: number };
    voice?: Partial<VoiceSettings>;
    shots?: number;
  }) => request<Project>("/projects", json("POST", body)),
  updateProject: (id: string, body: Partial<Pick<Project, "title" | "canvas" | "voice" | "subtitle" | "bgm">>) =>
    request<Project>(`/projects/${id}`, json("PATCH", body)),
  deleteProject: (id: string) => request<void>(`/projects/${id}`, json("DELETE")),
  addShot: (id: string, after?: string) => request<Project>(`/projects/${id}/shots`, json("POST", { text: "", after })),
  reorder: (id: string, order: string[]) => request<Project>(`/projects/${id}/shots/order`, json("PUT", { order })),
  updateShot: (
    id: string,
    shotId: string,
    body: { text?: string; duration?: number; clear_duration?: boolean; visual?: Partial<ShotVisual> },
  ) => request<Project>(`/projects/${id}/shots/${shotId}`, json("PATCH", body)),
  deleteShot: (id: string, shotId: string) => request<Project>(`/projects/${id}/shots/${shotId}`, json("DELETE")),
  candidates: (id: string, shotId: string) =>
    request<Project>(`/projects/${id}/shots/${shotId}/candidates`, json("POST")),
  upload: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ id: string; kind: string }>(`/projects/${id}/assets`, { method: "POST", body: form });
  },
  render: (id: string) => request<Job>(`/projects/${id}/renders`, json("POST")),
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
