import { useEffect, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import type { LanguageInfo, Project, VersionInfo } from "../types";
import { Section } from "./Controls";

/** 同一个视频的各语言版本：列出已有版本，并可以让大模型翻译出新的版本。 */
export function Versions({ project, languages }: { project: Project; languages: LanguageInfo[] }) {
  const { t, lang } = useI18n();
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.versions(project.id).then(setVersions).catch(() => setVersions([]));
  }, [project.id, project.language]);

  const name = (code: string) => {
    const info = languages.find((l) => l.code === code);
    if (!info) return code;
    return lang === "zh" && info.name_zh !== info.name ? `${info.name}（${info.name_zh}）` : info.name;
  };
  const taken = new Set(versions.map((v) => v.language));
  const available = languages.filter((l) => !taken.has(l.code) && l.code !== project.language);
  const chosen = available.some((l) => l.code === target) ? target : available[0]?.code ?? "";

  async function create() {
    if (!chosen) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.createVersion(project.id, chosen);
      window.location.hash = `#/p/${created.id}`;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title={t("versions.title")}>
      <ul className="versions">
        {versions.map((v) => (
          <li key={v.id}>
            {v.id === project.id ? (
              <span className="version current">
                <span className="tag">{v.language}</span> {name(v.language)} · {t("versions.current")}
              </span>
            ) : (
              <a className="version" href={`#/p/${v.id}`}>
                <span className="tag">{v.language}</span> {name(v.language)} · {v.title}
              </a>
            )}
          </li>
        ))}
      </ul>
      {available.length === 0 ? (
        <p className="muted small">{t("versions.none")}</p>
      ) : (
        <div className="field">
          <label htmlFor="version-language">{t("versions.make")}</label>
          <div className="row">
            <select
              id="version-language"
              className="input"
              value={chosen}
              disabled={busy}
              onChange={(e) => setTarget(e.target.value)}
              aria-label={t("versions.pick")}
            >
              {available.map((l) => (
                <option key={l.code} value={l.code}>
                  {name(l.code)}
                </option>
              ))}
            </select>
            <button type="button" className="btn" disabled={busy || !chosen} onClick={create}>
              {busy && <span className="spinner" aria-hidden="true" />}
              {busy ? t("versions.creating") : t("versions.create")}
            </button>
          </div>
          <p className="muted small">{t("versions.hint")}</p>
        </div>
      )}
      {error && <p className="hint error">{error}</p>}
    </Section>
  );
}
