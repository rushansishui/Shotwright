import { useEffect, useRef, useState } from "react";
import { api, assetUrl } from "../../api";
import { useI18n } from "../../i18n";
import type { MessageKey } from "../../messages";
import type { Look, LookSubject, Project, SubjectKind } from "../../types";

const GRADES = ["", "natural", "warm", "cool", "vivid", "film", "bw"];
const KINDS: SubjectKind[] = ["product", "person", "object", "place"];
const EMPTY: Look = { subjects: [], scene: "", style: "", avoid: "" };
const CODE = /^[A-Za-z][A-Za-z0-9_-]{0,11}$/;
/** 每个主体最多几张参考图（和后端 MAX_SUBJECT_IMAGES 一致）。 */
const MAX_IMAGES = 10;

interface LookSectionProps {
  project: Project;
  run: (action: () => Promise<Project>, shotId?: string) => Promise<Project | null>;
  setError?: (message: string) => void;
}

/** 全片设定：写分镜时由 AI 生成，这里可以改。改完后，之后生成的每个镜头都会带上。 */
export function LookSection({ project, run, setError }: LookSectionProps) {
  const { t } = useI18n();
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadFor, setUploadFor] = useState<number | null>(null);
  const [uploading, setUploading] = useState<number | null>(null);
  const [generating, setGenerating] = useState<string | null>(null);
  const [describing, setDescribing] = useState<string | null>(null);
  const saved = project.look ?? EMPTY;
  const [draft, setDraft] = useState<Look>(saved);
  useEffect(() => setDraft(project.look ?? EMPTY), [project.look]);

  const codes = draft.subjects.map((s) => s.code);
  const invalid = draft.subjects.some((s) => !CODE.test(s.code)) || new Set(codes).size !== codes.length;
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);

  function save(next: Look = draft) {
    if (invalid || JSON.stringify(next) === JSON.stringify(saved)) return;
    run(() => api.updateProject(project.id, { look: next }));
  }

  function setSubject(index: number, change: Partial<LookSubject>) {
    setDraft({ ...draft, subjects: draft.subjects.map((s, i) => (i === index ? { ...s, ...change } : s)) });
  }

  function addSubject() {
    let n = draft.subjects.length + 1;
    while (codes.includes(`O${n}`)) n += 1;
    setDraft({ ...draft, subjects: [...draft.subjects, { code: `O${n}`, name: "", kind: "object", appearance: "" }] });
  }

  async function uploadImages(index: number, files: File[]) {
    if (invalid) {
      setError?.(t("look.badCode"));
      return;
    }
    const subject = draft.subjects[index];
    const room = MAX_IMAGES - (subject.images?.length ?? 0);
    if (room <= 0) return;
    setUploading(index);
    try {
      const ids: string[] = [];
      for (const file of files.slice(0, room)) {
        const asset = await api.upload(project.id, file);
        if (asset.kind !== "image") throw new Error(t("references.notImage"));
        ids.push(asset.id);
      }
      const next = {
        ...draft,
        subjects: draft.subjects.map((s, i) => (i === index ? { ...s, images: [...(s.images ?? []), ...ids] } : s)),
      };
      setDraft(next);
      await run(() => api.updateProject(project.id, { look: next }));
    } catch (e) {
      setError?.((e as Error).message);
    } finally {
      setUploading(null);
    }
  }

  async function generateImage(index: number) {
    const subject = draft.subjects[index];
    if (invalid || dirty) {
      // 先保存外观描述，定妆图按保存后的描述生成。
      if (invalid) return setError?.(t("look.badCode"));
      await run(() => api.updateProject(project.id, { look: draft }));
    }
    const has = (subject.images?.length ?? 0) > 0;
    if (!window.confirm(t(has ? "look.portraitConfirmMore" : "look.portraitConfirm", { name: subject.name || subject.code }))) return;
    setGenerating(subject.code);
    try {
      await run(() => api.subjectImage(project.id, subject.code));
    } finally {
      setGenerating(null);
    }
  }

  /** 看参考图写外观：把领型、有没有帽子这些结构写进描述，模型就不会按常见款式自己加帽子。 */
  async function describe(index: number) {
    const subject = draft.subjects[index];
    if (invalid) return setError?.(t("look.badCode"));
    if (dirty) await run(() => api.updateProject(project.id, { look: draft }));
    setDescribing(subject.code);
    try {
      const { appearance } = await api.describeSubject(project.id, subject.code);
      const next = { ...draft, subjects: draft.subjects.map((s, i) => (i === index ? { ...s, appearance } : s)) };
      setDraft(next);
      await run(() => api.updateProject(project.id, { look: next }));
    } catch (e) {
      setError?.((e as Error).message);
    } finally {
      setDescribing(null);
    }
  }

  function removeImage(index: number, image: string) {
    const next = {
      ...draft,
      subjects: draft.subjects.map((s, i) => (i === index ? { ...s, images: (s.images ?? []).filter((id) => id !== image) } : s)),
    };
    setDraft(next);
    save(next);
  }

  /** 把某张参考图设为主图：没单独挑参考图的镜头都按主图的颜色和款式生成。 */
  function makeMain(index: number, image: string) {
    const next = {
      ...draft,
      subjects: draft.subjects.map((s, i) => (i === index ? { ...s, images: [image, ...(s.images ?? []).filter((id) => id !== image)] } : s)),
    };
    setDraft(next);
    save(next);
  }

  function removeSubject(index: number) {
    const next = { ...draft, subjects: draft.subjects.filter((_, i) => i !== index) };
    setDraft(next);
    save(next);
  }

  return (
    <div className="field look">
      <div className="field-row">
        <span className="label">{t("look.title")}</span>
        <span className="muted small">{t("look.hint")}</span>
      </div>
      {draft.subjects.length === 0 && <p className="muted small">{t("look.empty")}</p>}
      {draft.subjects.map((subject, index) => (
        <div className="look-subject" key={index} id={`look-subject-${subject.code}`}>
          <div className="look-row">
            <input
              className="input code"
              aria-label={t("look.code")}
              value={subject.code}
              aria-invalid={!CODE.test(subject.code) || codes.indexOf(subject.code) !== index}
              onChange={(e) => setSubject(index, { code: e.target.value.trim() })}
              onBlur={() => save()}
            />
            <input
              className="input"
              aria-label={t("look.name")}
              placeholder={t("look.name")}
              value={subject.name}
              onChange={(e) => setSubject(index, { name: e.target.value })}
              onBlur={() => save()}
            />
            <select
              className="input"
              aria-label={t("look.kind")}
              value={subject.kind}
              onChange={(e) => {
                const next = {
                  ...draft,
                  subjects: draft.subjects.map((s, i) => (i === index ? { ...s, kind: e.target.value as SubjectKind } : s)),
                };
                setDraft(next);
                save(next);
              }}
            >
              {KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {t(`look.kind.${kind}` as MessageKey)}
                </option>
              ))}
            </select>
            <button type="button" className="btn small ghost" onClick={() => removeSubject(index)}>
              {t("look.remove")}
            </button>
          </div>
          <textarea
            className="input"
            rows={2}
            aria-label={t("look.appearance")}
            placeholder={t("look.appearancePlaceholder")}
            value={subject.appearance}
            onChange={(e) => setSubject(index, { appearance: e.target.value })}
            onBlur={() => save()}
          />
          {subject.kind !== "place" && (subject.images?.length ?? 0) > 0 && (
            <div className="look-describe">
              <button
                type="button"
                className="btn small ghost"
                disabled={describing !== null || uploading !== null}
                title={t("look.describeHint")}
                onClick={() => describe(index)}
              >
                {describing === subject.code ? t("look.describeBusy") : t("look.describe")}
              </button>
              <span className="muted small">{t("look.describeHint")}</span>
            </div>
          )}
          {subject.kind !== "place" && !(subject.images?.length ?? 0) && (
            <label className="check small look-no-ref" title={t("ready.noReferenceHint")}>
              <input
                type="checkbox"
                checked={!!subject.no_reference}
                onChange={(e) => {
                  const next = {
                    ...draft,
                    subjects: draft.subjects.map((s, i) => (i === index ? { ...s, no_reference: e.target.checked } : s)),
                  };
                  setDraft(next);
                  save(next);
                }}
              />
              {t("ready.noReferenceToggle")}
            </label>
          )}
          {subject.kind !== "place" && (
            <div className="look-images">
              {(subject.images ?? []).map((image, position) => (
                <div className="look-image-cell" key={image}>
                  <div className="look-image">
                    {project.assets[image] ? (
                      <img src={assetUrl(project.id, image)} alt={`${subject.name || subject.code} ${position + 1}`} />
                    ) : (
                      <span className="muted small">{t("references.missing")}</span>
                    )}
                    {position === 0 && (subject.images?.length ?? 0) > 1 && <span className="look-image-main">{t("look.imageMain")}</span>}
                    {position > 0 && (
                      <button
                        type="button"
                        className="make-main"
                        title={t("look.imageMakeMain")}
                        aria-label={t("look.imageMakeMain")}
                        onClick={() => makeMain(index, image)}
                      >
                        ★
                      </button>
                    )}
                    <button
                      type="button"
                      className="remove"
                      aria-label={t("look.imageRemove")}
                      title={t("look.imageRemove")}
                      onClick={() => removeImage(index, image)}
                    >
                      ×
                    </button>
                  </div>
                  {subject.kind !== "person" && (
                    <input
                      className="input look-image-label"
                      maxLength={30}
                      aria-label={t("look.imageLabel")}
                      placeholder={t("look.imageLabelPlaceholder")}
                      value={subject.image_labels?.[image] ?? ""}
                      onChange={(e) => setSubject(index, { image_labels: { ...(subject.image_labels ?? {}), [image]: e.target.value } })}
                      onBlur={() => save()}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                      }}
                    />
                  )}
                </div>
              ))}
              {(subject.images?.length ?? 0) < MAX_IMAGES && (
                <button
                  type="button"
                  className="look-image-add"
                  disabled={uploading !== null}
                  onClick={() => {
                    setUploadFor(index);
                    fileInput.current?.click();
                  }}
                >
                  <span aria-hidden="true">+</span>
                  {uploading === index ? t("look.imageUploading") : t(subject.kind === "person" ? "look.imageAddPerson" : "look.imageAdd")}
                </button>
              )}
              {(subject.images?.length ?? 0) < MAX_IMAGES && (
                <button
                  type="button"
                  className="look-image-add ai"
                  disabled={uploading !== null || generating !== null || !subject.appearance.trim()}
                  title={subject.appearance.trim() ? t("look.portraitHint") : t("look.portraitNeedsAppearance")}
                  onClick={() => generateImage(index)}
                >
                  <span aria-hidden="true">✦</span>
                  {generating === subject.code ? t("look.portraitBusy") : t(subject.kind === "person" ? "look.portraitPerson" : "look.portrait")}
                </button>
              )}
              {(subject.images?.length ?? 0) === 0 && <span className="muted small look-image-hint">{t("look.imageHint")}</span>}
              {(subject.images?.length ?? 0) > 1 && subject.kind !== "person" && (
                <p className="muted small look-variants-hint">{t("look.variantsHint")}</p>
              )}
            </div>
          )}
        </div>
      ))}
      <input
        ref={fileInput}
        type="file"
        accept="image/*"
        multiple
        hidden
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length && uploadFor !== null) uploadImages(uploadFor, files);
        }}
      />
      <div className="row">
        <button type="button" className="btn small" onClick={addSubject} disabled={draft.subjects.length >= 12}>
          {t("look.add")}
        </button>
        {invalid && <span className="hint error small">{t("look.badCode")}</span>}
        {dirty && !invalid && <span className="muted small">{t("look.unsaved")}</span>}
      </div>
      <details className="look-advanced">
        <summary>{t("look.advanced")}</summary>
        <label className="look-field">
          <span className="label">{t("look.grade")}</span>
          <select
            className="input"
            value={draft.grade ?? ""}
            onChange={(e) => {
              const next = { ...draft, grade: e.target.value };
              setDraft(next);
              save(next);
            }}
          >
            {GRADES.map((grade) => (
              <option key={grade} value={grade}>
                {t(`look.grade.${grade || "none"}` as MessageKey)}
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={!!project.chain_frames}
            onChange={(e) => run(() => api.updateProject(project.id, { chain_frames: e.target.checked }))}
          />
          {t("look.chainFrames")}
        </label>
        <p className="muted small">{t("look.chainFramesHint")}</p>
      </details>
      {(["scene", "style", "avoid"] as const).map((key) => (
        <label className="look-field" key={key}>
          <span className="label">{t(`look.${key}` as MessageKey)}</span>
          <textarea
            className="input"
            rows={2}
            value={draft[key]}
            placeholder={t(`look.${key}Placeholder` as MessageKey)}
            onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
            onBlur={() => save()}
          />
        </label>
      ))}
    </div>
  );
}
