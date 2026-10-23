import { useEffect, useState } from "react";
import { api } from "../../api";
import { useI18n } from "../../i18n";
import type { MessageKey } from "../../messages";
import type { Look, LookSubject, Project, SubjectKind } from "../../types";

const KINDS: SubjectKind[] = ["product", "person", "object", "place"];
const EMPTY: Look = { subjects: [], scene: "", style: "", avoid: "" };
const CODE = /^[A-Za-z][A-Za-z0-9_-]{0,11}$/;

interface LookSectionProps {
  project: Project;
  run: (action: () => Promise<Project>, shotId?: string) => Promise<Project | null>;
}

/** 全片设定：写分镜时由 AI 生成，这里可以改。改完后，之后生成的每个镜头都会带上。 */
export function LookSection({ project, run }: LookSectionProps) {
  const { t } = useI18n();
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
        <div className="look-subject" key={index}>
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
        </div>
      ))}
      <div className="row">
        <button type="button" className="btn small" onClick={addSubject} disabled={draft.subjects.length >= 12}>
          {t("look.add")}
        </button>
        {invalid && <span className="hint error small">{t("look.badCode")}</span>}
        {dirty && !invalid && <span className="muted small">{t("look.unsaved")}</span>}
      </div>
      <label className="check">
        <input
          type="checkbox"
          checked={!!project.chain_frames}
          onChange={(e) => run(() => api.updateProject(project.id, { chain_frames: e.target.checked }))}
        />
        {t("look.chainFrames")}
      </label>
      <p className="muted small">{t("look.chainFramesHint")}</p>
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
