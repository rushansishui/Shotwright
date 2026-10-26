import type { Project, Shot } from "./types";

/** 画面描述的长度：中文按字，其他语言按词。和后端 storyboard._prompt_size 的口径一致。 */
export function promptSize(text: string, zh: boolean): number {
  if (zh) return (text.match(/[\u3400-\u9fff]/g) ?? []).length;
  return (text.match(/[A-Za-z]+(?:'[A-Za-z]+)?/g) ?? []).length;
}

/** 低于这个长度算偏短（与后端自检的下限一致）。 */
const MIN_SIZE = { zh: 90, en: 45 };

/** 这个镜头的画面描述是否需要补全：太短或缺少衔接说明（锁定、纯色、上传的镜头不算）。 */
export function needsExpansion(project: Project, shot: Shot): boolean {
  const visual = shot.visual;
  if (visual.locked || visual.mode === "color" || visual.mode === "upload") return false;
  const zh = project.language.toLowerCase().startsWith("zh");
  return promptSize(visual.prompt, zh) < (zh ? MIN_SIZE.zh : MIN_SIZE.en) || !(visual.link ?? "").trim();
}

/** 项目是否值得提示「一键补全」：没有全片设定，或有镜头的画面描述偏短。 */
export function projectNeedsExpansion(project: Project): number {
  const look = project.look;
  const lookEmpty = !look || (!look.subjects.length && !look.scene.trim() && !look.style.trim() && !look.avoid.trim());
  const short = project.shots.filter((shot) => needsExpansion(project, shot)).length;
  return short + (lookEmpty && project.shots.length ? 1 : 0);
}
