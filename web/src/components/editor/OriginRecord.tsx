import { useEffect, useState } from "react";
import { api } from "../../api";
import { BRIEF_TEMPLATES } from "../../briefs";
import { useI18n } from "../../i18n";
import type { MessageKey } from "../../messages";
import type { Project } from "../../types";
import { DRAFT_KEY, type Draft } from "../../draft";

/** 复制到剪贴板；浏览器不允许时退回到选中文字再复制。 */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

/** 视频诉求的上限（UTF-8 字节），和后端一致。 */
const MAX_SUBJECT_BYTES = 4096;
const bytes = (text: string) => new TextEncoder().encode(text).length;

type Field = "idea" | "subject" | "script";

interface OriginRecordProps {
  project: Project;
  run: (action: () => Promise<Project>) => Promise<Project | null>;
}

/**
 * 创作记录：这个视频最初的想法、视频诉求和生成分镜时的脚本。
 * 可以编辑、复制，也可以「以此为基础新建」：带着这些内容回到首页的新建流程，改完生成新的分镜。
 * 这里改的只是记录，不会改动现有镜头。
 */
export function OriginRecord({ project, run }: OriginRecordProps) {
  const { t } = useI18n();
  const [copied, setCopied] = useState<Field | null>(null);
  const [editing, setEditing] = useState<Field | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  /** 保存后让 AI 按改过的内容重写后面的内容（想法 → 诉求 → 脚本）。 */
  const [sync, setSync] = useState(true);
  const [syncing, setSyncing] = useState<"subject" | "script" | null>(null);
  const [syncError, setSyncError] = useState("");
  const idea = project.idea?.trim() ?? "";
  const subject = project.subject?.trim() ?? "";
  const recorded = (project.script ?? []).filter((line) => line.trim());
  // 旧项目没有保存脚本时，用现在的镜头文案代替（并注明）。
  const lines = recorded.length ? recorded : project.shots.map((shot) => shot.text.trim()).filter(Boolean);
  const fromShots = !recorded.length && lines.length > 0;
  const scriptText = lines.join("\n");
  const values: Record<Field, string> = { idea, subject, script: scriptText };

  // 切换到别的项目时退出编辑。
  useEffect(() => setEditing(null), [project.id]);

  async function copy(key: Field, text: string) {
    if (await copyText(text)) {
      setCopied(key);
      window.setTimeout(() => setCopied((current) => (current === key ? null : current)), 1500);
    }
  }

  function edit(field: Field) {
    setEditing(field);
    setDraft(values[field]);
  }

  async function save() {
    if (!editing) return;
    const field = editing;
    const changed = draft.trim() !== values[field].trim();
    setSaving(true);
    setSyncError("");
    const body =
      field === "script"
        ? { script: draft.split("\n").map((line) => line.trim()).filter(Boolean) }
        : field === "idea"
          ? { idea: draft.trim() }
          : { subject: draft.trim() };
    const result = await run(() => api.updateProject(project.id, body));
    setSaving(false);
    if (!result) return;
    setEditing(null);
    if (sync && changed && field !== "script" && draft.trim()) await cascade(result, field);
  }

  /** 改了想法：重新整理诉求、再写脚本；改了诉求：重写脚本。每一步写好就保存，中途失败也保留已完成的部分。 */
  async function cascade(latest: Project, from: "idea" | "subject") {
    const videoType = latest.video_type ?? "product_ad";
    const options = {
      video_type: videoType,
      language: latest.language,
      aspect: latest.canvas.aspect,
      duration: latest.target_seconds ?? 30,
      materials: [],
    };
    try {
      let brief = latest.subject ?? "";
      if (from === "idea") {
        setSyncing("subject");
        const templates = BRIEF_TEMPLATES[latest.language.toLowerCase().startsWith("zh") ? "zh" : "en"];
        brief = (await api.normalizeBrief({ idea: latest.idea ?? "", template: templates[videoType].template, ...options })).brief;
        await run(() => api.updateProject(project.id, { subject: brief }));
      }
      setSyncing("script");
      const script = await api.writeScript({ brief, ...options });
      await run(() => api.updateProject(project.id, { script: script.lines }));
    } catch (e) {
      setSyncError(t("origin.syncFailed", { message: (e as Error).message }));
    } finally {
      setSyncing(null);
    }
  }

  function startNew() {
    let existing: Partial<Draft> = {};
    try {
      existing = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? "{}");
    } catch {
      existing = {};
    }
    const unsaved = !!(existing.idea?.trim() || existing.subject?.trim() || existing.lines?.some((line) => line.trim()));
    if (unsaved && !window.confirm(t("origin.replaceDraft"))) return;
    // 有诉求时直接回到脚本（或诉求）这一步；没有诉求的旧项目从想法开始，把原来的文案带过去。
    const next: Draft = {
      step: subject ? (lines.length ? 3 : 2) : 1,
      idea: idea || (subject ? "" : scriptText),
      subject,
      title: project.title,
      lines: subject ? lines : [],
      videoType: project.video_type ?? "product_ad",
      aspect: project.canvas.aspect,
      duration: project.target_seconds ?? 30,
      language: project.language,
    };
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(next));
    } catch {
      /* 存不了草稿时仍然回首页 */
    }
    window.location.hash = "#/";
  }

  const tooLong = editing === "subject" && bytes(draft) > MAX_SUBJECT_BYTES;
  const draftLines = editing === "script" ? draft.split("\n").filter((line) => line.trim()).length : 0;

  const block = (field: Field, label: MessageKey, hint?: string) => {
    const value = values[field];
    const isEditing = editing === field;
    return (
      <div className={`origin-block ${isEditing ? "editing" : ""}`}>
        <div className="origin-head">
          <span className="label">{t(label)}</span>
          {isEditing && field === "script" && <span className="muted small">{t("origin.scriptEditHint", { n: draftLines })}</span>}
          {isEditing && field === "subject" && (
            <span className={`muted small ${tooLong ? "bad" : ""}`}>
              {bytes(draft)} / {MAX_SUBJECT_BYTES} {t("home.subjectBytes")}
            </span>
          )}
          {!isEditing && hint && <span className="muted small">{hint}</span>}
          <span className="origin-tools">
            {isEditing ? (
              <>
                <button type="button" className="btn small primary" disabled={saving || tooLong || draft === value} onClick={save}>
                  {saving ? t("origin.saving") : t("origin.save")}
                </button>
                <button type="button" className="btn small ghost" disabled={saving} onClick={() => setEditing(null)}>
                  {t("origin.cancel")}
                </button>
              </>
            ) : (
              <>
                <button type="button" className="btn small ghost" disabled={editing !== null || syncing !== null} onClick={() => edit(field)}>
                  {value ? t("origin.edit") : t("origin.add")}
                </button>
                {value && (
                  <button type="button" className="btn small ghost" onClick={() => copy(field, value)}>
                    {copied === field ? t("origin.copied") : t("origin.copy")}
                  </button>
                )}
              </>
            )}
          </span>
        </div>
        {isEditing && field !== "script" && (
          <label className="check origin-sync">
            <input type="checkbox" checked={sync} onChange={(e) => setSync(e.target.checked)} />
            {t(field === "idea" ? "origin.syncFromIdea" : "origin.syncFromSubject")}
          </label>
        )}
        {isEditing ? (
          <textarea
            className="input origin-input"
            rows={field === "idea" ? 4 : 12}
            value={draft}
            autoFocus
            aria-label={t(label)}
            placeholder={t(`origin.placeholder.${field}` as MessageKey)}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setEditing(null);
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !tooLong) save();
            }}
          />
        ) : syncing && ((field === "subject" && syncing === "subject") || field === "script") ? (
          <p className="muted small origin-syncing">{t(syncing === "subject" && field === "script" ? "origin.waiting" : "origin.syncing")}</p>
        ) : !value ? (
          <p className="muted small">{t(`origin.empty.${field}` as MessageKey)}</p>
        ) : field === "script" ? (
          <ol className="origin-script">
            {lines.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ol>
        ) : (
          <p className="origin-text">{value}</p>
        )}
      </div>
    );
  };

  return (
    <details className="origin-record card">
      <summary>
        <span className="origin-title">{t("origin.title")}</span>
        <span className="muted small">{t("origin.summary")}</span>
      </summary>
      <div className="origin-body">
        {block("idea", "origin.idea")}
        {block("subject", "origin.subject")}
        {block("script", "origin.script", fromShots ? t("origin.fromShots") : t("origin.scriptCount", { n: lines.length }))}
        {syncError && <p className="hint error">{syncError}</p>}
        <p className="muted small">{t("origin.editNote")}</p>
        <div className="origin-actions">
          <button type="button" className="btn primary" disabled={editing !== null || syncing !== null} onClick={startNew}>
            {t("origin.startNew")}
          </button>
          <span className="muted small">{t("origin.startNewHint")}</span>
        </div>
      </div>
    </details>
  );
}
