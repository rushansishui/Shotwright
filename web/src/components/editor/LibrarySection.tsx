import { useEffect, useState } from "react";
import { api } from "../../api";
import { useI18n } from "../../i18n";
import type { BrandPack, Project, SubjectTemplate } from "../../types";

interface LibrarySectionProps {
  project: Project;
  run: (action: () => Promise<Project>, shotId?: string) => Promise<Project | null>;
  setError: (msg: string) => void;
}

/**
 * 品牌包与主体库：做同一个品牌、同一条产品线的视频时，不用每次重新传 Logo、
 * 重新写外观描述。存在所有项目之外，一键套用。
 */
export function LibrarySection({ project, run, setError }: LibrarySectionProps) {
  const { t } = useI18n();
  const [brands, setBrands] = useState<BrandPack[]>([]);
  const [subjects, setSubjects] = useState<SubjectTemplate[]>([]);
  const [brandName, setBrandName] = useState("");
  const [busy, setBusy] = useState(false);
  const [opened, setOpened] = useState(false);
  /** 全片设定里的主体；没有全片设定时为空。 */
  const subjectsInLook = project.look?.subjects ?? [];

  const refresh = () => {
    api.listBrands().then((data) => setBrands(data.brands)).catch(() => setBrands([]));
    api.listSubjects().then((data) => setSubjects(data.subjects)).catch(() => setSubjects([]));
  };
  useEffect(() => {
    if (!opened) return;
    refresh();
  }, [opened]);

  const withBusy = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const saveBrand = () =>
    withBusy(async () => {
      const name = brandName.trim() || project.title;
      await api.saveBrandFromProject(project.id, name);
      setBrandName("");
      refresh();
    });

  const saveSubject = (code: string) =>
    withBusy(async () => {
      await api.saveSubjectToLibrary(project.id, code);
      refresh();
    });

  return (
    <details className="library" open={opened} onToggle={(e) => setOpened((e.target as HTMLDetailsElement).open)}>
      <summary>{t("library.title")}</summary>
      <p className="muted small">{t("library.intro")}</p>

      <div className="library-block">
        <span className="label">{t("library.brands")}</span>
        <div className="inline-field">
          <input
            className="input"
            value={brandName}
            maxLength={60}
            placeholder={t("library.brandNamePlaceholder", { name: project.title })}
            onChange={(e) => setBrandName(e.target.value)}
          />
          <button type="button" className="btn" disabled={busy} onClick={saveBrand}>
            {t("library.saveBrand")}
          </button>
        </div>
        {brands.length === 0 ? (
          <p className="muted small">{t("library.noBrands")}</p>
        ) : (
          <ul className="library-list">
            {brands.map((pack) => (
              <li key={pack.id}>
                <span className="library-swatch" style={{ background: pack.color }} aria-hidden="true" />
                <span className="library-name">{pack.name}</span>
                <button
                  type="button"
                  className="btn small"
                  disabled={busy}
                  onClick={() =>
                    withBusy(async () => {
                      await run(() => api.applyBrand(project.id, pack.id));
                    })
                  }
                >
                  {t("library.apply")}
                </button>
                <button
                  type="button"
                  className="btn small ghost danger"
                  disabled={busy}
                  onClick={() => withBusy(async () => {
                    await api.deleteBrand(pack.id);
                    refresh();
                  })}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="library-block">
        <span className="label">{t("library.subjects")}</span>
        {subjectsInLook.length === 0 ? (
          <p className="muted small">{t("library.noSubjects")}</p>
        ) : (
          <div className="row library-save-subjects">
            {subjectsInLook.map((subject) => (
              <button
                key={subject.code}
                type="button"
                className="btn small"
                disabled={busy}
                title={t("library.saveSubjectHint")}
                onClick={() => saveSubject(subject.code)}
              >
                {t("library.saveSubject", { code: subject.code, name: subject.name || subject.kind })}
              </button>
            ))}
          </div>
        )}
        {subjects.length > 0 && (
          <ul className="library-list">
            {subjects.map((item) => (
              <li key={item.id}>
                <span className="library-name">
                  {item.code} · {item.name || item.kind}
                </span>
                <span className="muted small library-appearance">{item.appearance.slice(0, 40)}</span>
                <button
                  type="button"
                  className="btn small"
                  disabled={busy}
                  onClick={() =>
                    withBusy(async () => {
                      await run(() => api.applySubjectFromLibrary(project.id, item.id).then(() => api.getProject(project.id)));
                    })
                  }
                >
                  {t("library.apply")}
                </button>
                <button
                  type="button"
                  className="btn small ghost danger"
                  disabled={busy}
                  onClick={() => withBusy(async () => {
                    await api.deleteLibrarySubject(item.id);
                    refresh();
                  })}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
}
