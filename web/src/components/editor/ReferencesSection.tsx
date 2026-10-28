import { useRef, useState } from "react";
import { api, assetUrl } from "../../api";
import type { Project, ReferenceAsset, ReferenceRole } from "../../types";
import { useI18n } from "../../i18n";

/** 品牌与风格参考只放 Logo 和风格图；产品、人物的图片属于全片设定里的主体。 */
const ROLES: ReferenceRole[] = ["logo", "style"];
const MAX_SUBJECT_IMAGES = 10;

interface ReferencesSectionProps {
  project: Project;
  run: (action: () => Promise<Project>, shotId?: string) => Promise<Project | null>;
  setError: (msg: string) => void;
}

export function ReferencesSection({ project, run, setError }: ReferencesSectionProps) {
  const { t } = useI18n();
  const fileInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const id = project.id;

  async function addReference(file: File, role: ReferenceRole) {
    setBusy(true);
    setError("");
    try {
      const asset = await api.upload(id, file);
      if (asset.kind !== "image") throw new Error(t("references.notImage"));
      const next: ReferenceAsset[] = [
        ...project.references,
        { asset_id: asset.id, role, label: file.name.slice(0, 60), enabled: true },
      ];
      await run(() => api.updateProject(id, { references: next }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function patch(index: number, change: Partial<ReferenceAsset>) {
    const next = project.references.map((ref, i) => (i === index ? { ...ref, ...change } : ref));
    return run(() => api.updateProject(id, { references: next }));
  }

  function remove(index: number) {
    const next = project.references.filter((_, i) => i !== index);
    return run(() => api.updateProject(id, { references: next }));
  }

  /** 旧项目里放在这里的产品 / 人物图：归到全片设定的主体下，生成时才会作为这个主体的参考图。 */
  function moveToSubject(index: number, code: string) {
    const ref = project.references[index];
    const look = project.look;
    if (!look || !ref) return;
    const subjects = look.subjects.map((s) =>
      s.code === code && !(s.images ?? []).includes(ref.asset_id) && (s.images ?? []).length < MAX_SUBJECT_IMAGES
        ? { ...s, images: [...(s.images ?? []), ref.asset_id] }
        : s,
    );
    const references = project.references.filter((_, i) => i !== index);
    return run(() => api.updateProject(id, { look: { ...look, subjects }, references }));
  }

  const subjects = (project.look?.subjects ?? []).filter((s) => s.kind !== "place");

  return (
    <div className="field">
      <div className="field-row">
        <span className="label">{t("references.title")}</span>
        <span className="muted small">{t("references.hint")}</span>
      </div>
      <input
        ref={fileInput}
        type="file"
        accept="image/*"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) addReference(file, "logo");
          e.target.value = "";
        }}
      />
      <div className="row">
        <button type="button" className="btn" disabled={busy} onClick={() => fileInput.current?.click()}>
          {busy ? t("references.uploading") : t("references.add")}
        </button>
      </div>

      {project.references.length === 0 && <p className="muted small">{t("references.empty")}</p>}

      {project.references.length > 0 && (
        <div className="reference-list">
          {project.references.map((ref, index) => {
            const asset = project.assets[ref.asset_id];
            return (
              <div key={ref.asset_id} className={`reference-item ${ref.enabled ? "" : "off"}`}>
                {asset ? (
                  <img src={assetUrl(id, asset.id)} alt={ref.label} />
                ) : (
                  <span className="muted small">{t("references.missing")}</span>
                )}
                <div className="reference-body">
                  <input
                    className="input"
                    value={ref.label}
                    placeholder={t("references.labelPlaceholder")}
                    onChange={(e) => patch(index, { label: e.target.value })}
                  />
                  {ref.role === "product" || ref.role === "person" ? (
                    <div className="row">
                      <span className="hint warn small">{t("references.oldRole", { role: t(`references.role.${ref.role}`) })}</span>
                      {subjects.length > 0 && (
                        <select
                          className="input compact"
                          aria-label={t("references.moveTo")}
                          value=""
                          onChange={(e) => e.target.value && moveToSubject(index, e.target.value)}
                        >
                          <option value="">{t("references.moveTo")}</option>
                          {subjects.map((s) => (
                            <option key={s.code} value={s.code}>
                              {s.code} {s.name}
                            </option>
                          ))}
                        </select>
                      )}
                    </div>
                  ) : (
                    <select
                      className="input"
                      value={ref.role}
                      onChange={(e) => patch(index, { role: e.target.value as ReferenceRole })}
                    >
                      {ROLES.map((role) => (
                        <option key={role} value={role}>
                          {t(`references.role.${role}`)}
                        </option>
                      ))}
                    </select>
                  )}
                  <div className="row">
                    <label className="switch">
                      <input
                        type="checkbox"
                        checked={ref.enabled}
                        onChange={(e) => patch(index, { enabled: e.target.checked })}
                      />
                      {t("references.enabled")}
                    </label>
                    <button type="button" className="btn ghost" onClick={() => remove(index)}>
                      {t("references.remove")}
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
