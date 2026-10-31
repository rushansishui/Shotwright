import type { Aspect, VideoType } from "./types";

/** 新建视频的草稿：存在浏览器里，刷新不丢；编辑页「以此为基础新建」也是写这份草稿再回首页。 */
export interface Draft {
  step: Step;
  idea: string;
  subject: string;
  title: string;
  lines: string[];
  videoType: VideoType;
  aspect?: Aspect;
  duration?: number;
  language?: string;
  /** 用户自己改过类型 / 比例 / 时长：刷新后仍然按用户选的来，不再让 AI 判断。 */
  customized?: boolean;
}

export type Step = 1 | 2 | 3;
export const DRAFT_KEY = "shotwright.newVideoDraft";

export function loadDraft(): Partial<Draft> {
  try {
    return JSON.parse(localStorage.getItem(DRAFT_KEY) ?? "{}") as Partial<Draft>;
  } catch {
    return {};
  }
}

export function saveDraft(draft: Draft) {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    /* 无痕模式等情况下存不了，忽略 */
  }
}

