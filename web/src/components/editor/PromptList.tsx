import { useEffect, useState } from "react";
import { api } from "../../api";
import { useI18n } from "../../i18n";
import type { MessageKey } from "../../messages";
import type { Project, PromptPreview } from "../../types";

type Run = (action: () => Promise<Project>, shotId?: string) => Promise<Project | null>;

/**
 * 提示词总览：一个镜头一段，看这条片子到底会跟模型说什么，也可以直接改。
 *
 * 镜头卡片上只写镜头自己的画面描述；交给模型的是拼好的整段——画面描述、运镜、衔接、
 * 主体外观、场景风格、参考图要求和禁止项。这里一次全列出来，改完一处就能在同一个位置
 * 核对整段。只读时不花钱；改过的整段会原样提交（prompt_override），可以随时恢复自动拼接。
 */
export function PromptList({
  project,
  run,
  onOpenShot,
}: {
  project: Project;
  run: Run;
  onOpenShot: (index: number) => void;
}) {
  const { t } = useI18n();
  const [previews, setPreviews] = useState<PromptPreview[] | null>(null);
  const [notice, setNotice] = useState("");
  const [only, setOnly] = useState<"all" | "ai">("ai");
  const [tick, setTick] = useState(0);

  // 画面来源、画面描述、运镜、衔接、全片设定、手动改过的整段，改了都要重算——提示词是这些拼起来的。
  const key = [
    project.shots
      .map(
        (s) =>
          `${s.id}:${s.visual.mode}:${s.visual.provider}:${s.visual.prompt}:${s.visual.image_prompt ?? ""}:${s.visual.camera}:${s.visual.link ?? ""}:${s.visual.prompt_override ?? ""}:${(s.visual.refs ?? []).join(",")}`,
      )
      .join("|"),
    JSON.stringify(project.look),
    project.language,
    tick,
  ].join("#");

  useEffect(() => {
    let stopped = false;
    api
      .projectPrompts(project.id)
      .then((data) => !stopped && setPreviews(data.shots))
      .catch(() => !stopped && setPreviews([]));
    return () => {
      stopped = true;
    };
  }, [project.id, key]);

  const shown = (previews ?? []).filter((item) => only === "all" || item.supported);
  const withPrompt = (previews ?? []).filter((item) => item.supported).length;

  async function copyAll() {
    const blocks = shown.map((item) => {
      const number = project.shots.findIndex((s) => s.id === item.shot_id) + 1;
      const note = item.supported ? "" : `（${t("prompt.noPromptShort")}）`;
      return `【${t("prompt.shot", { n: number })}】${note}\n${item.text || ""}`;
    });
    try {
      await navigator.clipboard.writeText(blocks.join("\n\n"));
      setNotice(t("prompt.copiedAll", { n: shown.length }));
    } catch {
      setNotice(t("prompt.copyFailed"));
    }
  }

  /** 保存手动改过的整段提示词；`text` 传空字符串表示恢复自动拼接。 */
  async function saveOverride(shotId: string, text: string, index: number) {
    const done = await run(
      () => api.updateShot(project.id, shotId, { visual: { prompt_override: text } }),
      shotId,
    );
    if (!done) return;
    setTick((n) => n + 1);
    setNotice(text.trim() ? t("prompt.savedManual", { n: index + 1 }) : t("prompt.savedAuto", { n: index + 1 }));
  }

  return (
    <div className="prompt-list">
      <div className="board-toolbar">
        <p className="muted small">{t("prompt.intro")}</p>
        <div className="row">
          <label className="switch" title={t("prompt.onlyHint")}>
            <input type="checkbox" checked={only === "ai"} onChange={(e) => setOnly(e.target.checked ? "ai" : "all")} />
            {t("prompt.onlyAi")}
          </label>
          <button type="button" className="btn small" disabled={!shown.length} onClick={copyAll}>
            {t("prompt.copyAll")}
          </button>
        </div>
      </div>
      {notice && <p className="hint small">{notice}</p>}
      {previews === null && <p className="muted small">{t("prompt.loading")}</p>}
      {previews !== null && project.shots.length === 0 && <p className="muted">{t("editor.noShots")}</p>}
      {previews !== null && project.shots.length > 0 && withPrompt === 0 && (
        <p className="muted small">{t("prompt.noneAi")}</p>
      )}

      {shown.map((item) => {
        const index = project.shots.findIndex((s) => s.id === item.shot_id);
        return (
          <PromptBlock
            key={item.shot_id}
            preview={item}
            index={index}
            onCopy={async (text) => {
              try {
                await navigator.clipboard.writeText(text);
                setNotice(t("prompt.copiedOne", { n: index + 1 }));
              } catch {
                setNotice(t("prompt.copyFailed"));
              }
            }}
            onOpen={() => index >= 0 && onOpenShot(index)}
            onSave={item.supported ? (text) => saveOverride(item.shot_id, text, index) : undefined}
          />
        );
      })}
    </div>
  );
}

function PromptBlock(props: {
  preview: PromptPreview;
  index: number;
  onCopy: (text: string) => void;
  onOpen: () => void;
  /** 保存手动改过的整段；传空字符串恢复自动拼接。不支持提示词的来源不给。 */
  onSave?: (text: string) => Promise<void>;
}) {
  const { t } = useI18n();
  const { preview } = props;
  const [showLast, setShowLast] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const n = props.index + 1;
  const over = preview.characters > preview.budget;
  const editing = draft !== null;

  async function save(text: string) {
    if (!props.onSave) return;
    setSaving(true);
    try {
      await props.onSave(text);
      setDraft(null);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className={`prompt-block ${preview.empty ? "is-empty" : ""} ${preview.manual ? "is-manual" : ""}`}>
      <header className="prompt-head">
        <button type="button" className="board-index" title={t("prompt.openShot")} onClick={props.onOpen}>
          {n}
        </button>
        <span className="prompt-meta">
          {t(`shot.mode.${preview.mode}` as MessageKey)}
          {preview.provider_name ? ` · ${preview.provider_name}` : ""}
          {preview.supported && (
            <>
              {" · "}
              <span className={over ? "warn" : ""}>
                {t("prompt.chars", { n: preview.characters, budget: preview.budget })}
              </span>
            </>
          )}
          {preview.reference_count > 0 && ` · ${t("prompt.refs", { n: preview.reference_count })}`}
          {preview.manual && <span className="tag manual">{t("prompt.manual")}</span>}
        </span>
        <span className="row">
          {props.onSave && !editing && (
            <button
              type="button"
              className="btn small ghost"
              disabled={preview.empty && !preview.manual}
              title={preview.empty ? t("prompt.editEmptyHint") : t("prompt.editHint")}
              onClick={() => setDraft(preview.text)}
            >
              {t("prompt.edit")}
            </button>
          )}
          {preview.manual && !editing && props.onSave && (
            <button
              type="button"
              className="btn small ghost"
              disabled={saving}
              title={t("prompt.restoreHint")}
              onClick={() => save("")}
            >
              {t("prompt.restore")}
            </button>
          )}
          <button type="button" className="btn small ghost" onClick={() => props.onCopy(editing ? draft : preview.text)}>
            {t("prompt.copy")}
          </button>
        </span>
      </header>

      {editing ? (
        <div className="prompt-edit">
          <textarea
            className="input prompt-edit-area"
            rows={12}
            value={draft}
            autoFocus
            aria-label={t("prompt.editLabel", { n })}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setDraft(null);
              // Ctrl+Enter 保存；单独 Enter 用来换行，提示词是多行的。
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                void save(draft);
              }
            }}
          />
          <div className="row">
            <button type="button" className="btn small primary" disabled={saving} onClick={() => save(draft)}>
              {saving ? t("prompt.saving") : t("prompt.save")}
            </button>
            <button type="button" className="btn small ghost" onClick={() => setDraft(null)}>
              {t("prompt.cancel")}
            </button>
            {preview.manual && (
              <button type="button" className="btn small ghost" disabled={saving} onClick={() => setDraft(preview.text)}>
                {t("prompt.revertDraft")}
              </button>
            )}
            <span className="muted small">{t("prompt.editHint")}</span>
          </div>
        </div>
      ) : preview.empty ? (
        <p className="hint small">{t("prompt.empty")}</p>
      ) : (
        <pre className="prompt-text">{preview.text}</pre>
      )}

      {!editing && preview.manual && <p className="muted small">{t("prompt.manualHint")}</p>}

      {(preview.translated || preview.dropped.length > 0 || preview.trimmed > 0 || preview.truncated || preview.first_frame) && (
        <ul className="prompt-notes">
          {preview.translated && <li>{t("prompt.translated")}</li>}
          {preview.dropped.length > 0 && (
            <li>
              {t("prompt.dropped", {
                list: preview.dropped.map((key) => t(`prompt.section.${key}` as MessageKey)).join("、"),
              })}
            </li>
          )}
          {preview.trimmed > 0 && <li>{t("prompt.trimmed", { n: preview.trimmed })}</li>}
          {preview.truncated && <li className="warn">{t("prompt.truncated")}</li>}
          {preview.first_frame && <li>{t("prompt.firstFrame")}</li>}
        </ul>
      )}

      {preview.references.length > 0 && (
        <p className="muted small">
          {t("prompt.refList", {
            list: preview.references
              .map((ref, i) => `${i + 1}: ${ref.subject || t("prompt.refNoSubject")}${ref.label ? `（${ref.label}）` : ""}`)
              .join("，"),
          })}
        </p>
      )}

      {preview.last_sent && (
        <div className="prompt-last">
          <button type="button" className="btn small ghost" onClick={() => setShowLast(!showLast)}>
            {showLast ? t("prompt.hideLast") : t("prompt.showLast")}
          </button>
          {showLast && (
            <>
              <pre className="prompt-text sent">{preview.last_sent}</pre>
              {preview.last_sent !== preview.text && <p className="muted small">{t("prompt.lastDiff")}</p>}
            </>
          )}
        </div>
      )}
    </section>
  );
}
