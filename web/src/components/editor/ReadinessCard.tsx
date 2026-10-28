import { useState } from "react";
import { api, assetUrl } from "../../api";
import { useI18n } from "../../i18n";
import type { MessageKey } from "../../messages";
import { outdatedShots, preparedSubjects, subjectStatus } from "../../readiness";
import type { Project } from "../../types";

interface ReadinessCardProps {
  project: Project;
  run: (action: () => Promise<Project>) => Promise<Project | null>;
  /** 确认后进入下一步（分镜画面）。 */
  onConfirmed: () => void;
}

/**
 * 素材准备清单：每个主体是否就绪（外观、参考图、颜色标注），缺什么就地补，全部就绪后确认。
 * 确认之前不能生成 AI 画面；确认后改了设定会提示哪些镜头可能不一致。
 */
export function ReadinessCard({ project, run, onConfirmed }: ReadinessCardProps) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const subjects = preparedSubjects(project);
  const statuses = subjects.map((subject) => subjectStatus(project, subject));
  const readyCount = statuses.filter((s) => s.ready).length;
  const allReady = readyCount === statuses.length;
  const confirmed = project.look?.confirmed !== false;
  const revised = !!project.look?.revised_at;
  const outdated = outdatedShots(project);

  async function confirm() {
    setBusy(true);
    const result = await run(() => api.confirmLook(project.id));
    setBusy(false);
    if (result) onConfirmed();
  }

  function setNoReference(code: string, value: boolean) {
    const look = project.look;
    if (!look) return Promise.resolve(null);
    const next = { ...look, subjects: look.subjects.map((s) => (s.code === code ? { ...s, no_reference: value } : s)) };
    return run(() => api.updateProject(project.id, { look: next }));
  }

  function focusSubject(code: string) {
    document.getElementById(`look-subject-${code}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <div className={`readiness ${(confirmed && !revised) || subjects.length === 0 ? "done" : ""}`}>
      <div className="readiness-head">
        <strong>{t("ready.title")}</strong>
        {subjects.length > 0 && (
          <span className={`tag ${allReady ? "ok" : "warn"}`}>{t("ready.count", { n: readyCount, total: subjects.length })}</span>
        )}
        {confirmed && !revised && <span className="tag ok">{t("ready.confirmed")}</span>}
      </div>

      {subjects.length === 0 ? (
        <p className="muted small">{t("ready.noSubjectsShort")}</p>
      ) : (
        <ul className="readiness-list">
          {statuses.map(({ subject, ready, missing, images }) => (
            <li key={subject.code} className={ready ? "ok" : "missing"}>
              <span className="readiness-mark" aria-hidden="true">
                {ready ? "✓" : "!"}
              </span>
              <span className="readiness-thumbs">
                {images.slice(0, 4).map((image) => (
                  <img key={image} src={assetUrl(project.id, image)} alt="" />
                ))}
              </span>
              <span className="readiness-name">
                <b>{subject.code}</b> {subject.name}
              </span>
              <span className="readiness-detail muted small">
                {ready
                  ? subject.no_reference && !images.length
                    ? t("ready.noReference")
                    : t("ready.okDetail", { n: images.length })
                  : missing.map((item) => t(`ready.missing.${item}` as MessageKey)).join(t("ready.sep"))}
              </span>
              <span className="readiness-actions">
                {!ready && (
                  <button type="button" className="btn small" onClick={() => focusSubject(subject.code)}>
                    {t("ready.fix")}
                  </button>
                )}
                {(missing.includes("images") || subject.no_reference) && !images.length && (
                  <label className="check small" title={t("ready.noReferenceHint")}>
                    <input
                      type="checkbox"
                      checked={!!subject.no_reference}
                      onChange={(e) => setNoReference(subject.code, e.target.checked)}
                    />
                    {t("ready.noReferenceToggle")}
                  </label>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      {confirmed && revised && (
        <p className="hint warn small">
          {outdated.length ? t("ready.revisedShots", { list: outdated.map((i) => i + 1).join("、") }) : t("ready.revised")}
        </p>
      )}

      <div className="readiness-foot">
        {subjects.length === 0 ? (
          <button type="button" className="btn" onClick={onConfirmed}>
            {t("ready.next")}
          </button>
        ) : !confirmed || revised ? (
          <>
            <button type="button" className="btn primary" disabled={busy || !allReady} onClick={confirm}>
              {busy ? t("ready.confirming") : revised ? t("ready.reconfirm") : t("ready.confirm")}
            </button>
            {!allReady && <span className="muted small">{t("ready.needAll")}</span>}
          </>
        ) : (
          <button type="button" className="btn" onClick={onConfirmed}>
            {t("ready.next")}
          </button>
        )}
      </div>
    </div>
  );
}
