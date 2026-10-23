import { useRef, useState } from "react";
import { api, assetUrl } from "../../api";
import type { Project, ReferenceAsset, ReferenceRole } from "../../types";
import { useI18n } from "../../i18n";

const ROLES: ReferenceRole[] = ["product", "person", "logo", "style"];

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
          if (file) addReference(file, "product");
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
