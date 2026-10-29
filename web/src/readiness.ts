import type { LookSubject, Project } from "./types";

/** 主体缺什么：外观描述、参考图、多张图的颜色标注。规则和后端 services/readiness.py 一致。 */
export type Missing = "appearance" | "images" | "labels";

export interface SubjectStatus {
  subject: LookSubject;
  ready: boolean;
  missing: Missing[];
  images: string[];
}

export function subjectStatus(project: Project, subject: LookSubject): SubjectStatus {
  const images = (subject.images ?? []).filter((id) => project.assets[id]);
  if (subject.kind === "place") return { subject, ready: true, missing: [], images };
  const missing: Missing[] = [];
  if (!subject.appearance.trim()) missing.push("appearance");
  if (!images.length && !subject.no_reference) missing.push("images");
  if (images.length > 1 && subject.kind !== "person" && images.some((id) => !subject.image_labels?.[id])) missing.push("labels");
  return { subject, ready: missing.length === 0, missing, images };
}

/** 需要准备的主体（非场景）。 */
export function preparedSubjects(project: Project): LookSubject[] {
  return (project.look?.subjects ?? []).filter((s) => s.kind !== "place");
}

/** 还不能生成 AI 画面：有主体、而且还没确认（旧项目 confirmed 为空，不拦）。 */
export function generationLocked(project: Project): boolean {
  return project.look?.confirmed === false && preparedSubjects(project).length > 0;
}

/** 确认之后改了设定：在修改之前生成、现在选中的 AI 画面可能和新设定不一致。 */
export function outdatedShots(project: Project): number[] {
  const revised = project.look?.revised_at;
  if (!revised) return [];
  const result: number[] = [];
  project.shots.forEach((shot, index) => {
    const visual = shot.visual;
    if (visual.mode !== "ai" && visual.mode !== "image") return;
    const take = (visual.takes ?? []).find((item) => item.asset_id === visual.selected);
    if (take && take.created_at < revised) result.push(index);
  });
  return result;
}
