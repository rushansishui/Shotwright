import { useEffect, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import type { Aspect, LanguageInfo, ProjectSummary, Providers } from "../types";
import { Segmented } from "./Controls";

export function Home() {
  const [mode, setMode] = useState<"llm" | "split">("llm");
  const [subject, setSubject] = useState("");
  const [script, setScript] = useState("");
  const [aspect, setAspect] = useState<Aspect>("9:16");
  const [shots, setShots] = useState(6);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [providers, setProviders] = useState<Providers | null>(null);
  const [languages, setLanguages] = useState<LanguageInfo[]>([]);
  const { t, lang } = useI18n();
  const [language, setLanguage] = useState(lang === "zh" ? "zh-CN" : "en-US");

  useEffect(() => {
    api.listProjects().then(setProjects).catch(() => setProjects([]));
    api.providers().then(setProviders).catch(() => setProviders(null));
    api.languages().then(setLanguages).catch(() => setLanguages([]));
  }, []);

  const llmReady = providers?.llm?.some((p) => p.default && p.configured) ?? false;
  const canSubmit = mode === "llm" ? subject.trim().length > 0 : script.trim().length > 0;

  async function create() {
    setBusy(true);
    setError("");
    try {
      const project = await api.createProject({
        mode,
        subject: subject.trim(),
        script: mode === "split" ? script : "",
        canvas: { aspect },
        language,
        shots,
      });
      window.location.hash = `#/p/${project.id}`;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="page narrow">
      <section className="card">
        <div className="card-head">
          <h1>{t("home.newVideo")}</h1>
        </div>
        <Segmented
          label={t("home.createMode")}
          value={mode}
          onChange={setMode}
          options={[
            { value: "llm", label: t("home.mode.llm") },
            { value: "split", label: t("home.mode.split") },
          ]}
        />
        {mode === "llm" ? (
          <div className="field">
            <label htmlFor="subject">{t("home.subject")}</label>
            <input
              id="subject"
              className="input"
              placeholder={t("home.subjectPlaceholder")}
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && canSubmit && !busy && create()}
            />
            {providers && !llmReady && (
              <p className="hint warn">{t("home.llmMissing")}</p>
            )}
          </div>
        ) : (
          <div className="field">
            <label htmlFor="script">{t("home.script")}</label>
            <textarea
              id="script"
              className="input"
              rows={7}
              placeholder={t("home.scriptPlaceholder")}
              value={script}
              onChange={(e) => setScript(e.target.value)}
            />
          </div>
        )}
        <div className="field">
          <div className="field-row">
            <label htmlFor="language">{t("home.language")}</label>
            <span className="muted small">{t("home.languageHint")}</span>
          </div>
          <select id="language" className="input" value={language} onChange={(e) => setLanguage(e.target.value)}>
            {languages.map((l) => (
              <option key={l.code} value={l.code}>
                {lang === "zh" && l.name_zh !== l.name ? `${l.name}（${l.name_zh}）` : l.name}
              </option>
            ))}
          </select>
        </div>
        <div className="grid-2">
          <div className="field">
            <span className="label">{t("home.aspect")}</span>
            <Segmented
              label={t("home.aspect")}
              value={aspect}
              onChange={setAspect}
              options={[
                { value: "9:16", label: t("home.aspect.portrait") },
                { value: "16:9", label: t("home.aspect.landscape") },
                { value: "1:1", label: t("home.aspect.square") },
              ]}
            />
          </div>
          {mode === "llm" && (
            <div className="field">
              <div className="field-row">
                <label htmlFor="shots">{t("home.shots")}</label>
                <span className="muted">{t("home.shotsValue", { n: shots })}</span>
              </div>
              <input id="shots" type="range" min={3} max={15} value={shots} onChange={(e) => setShots(Number(e.target.value))} />
            </div>
          )}
        </div>
        {error && <p className="hint error">{error}</p>}
        <button type="button" className="btn primary big" disabled={!canSubmit || busy} onClick={create}>
          {busy ? (mode === "llm" ? t("home.writing") : t("home.creating")) : t("home.submit")}
        </button>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>{t("home.recent")}</h2>
        </div>
        {projects.length === 0 ? (
          <p className="muted">{t("home.empty")}</p>
        ) : (
          <ul className="project-list">
            {projects.map((p) => (
              <li key={p.id}>
                <a href={`#/p/${p.id}`}>
                  <span className="project-title">{p.title}</span>
                  <span className="muted">
                    <span className="tag">{p.language}</span>{" "}
                    {t("home.projectMeta", {
                      aspect: p.aspect,
                      shots: p.shots,
                      time: new Date(p.updated_at * 1000).toLocaleString(lang === "zh" ? "zh-CN" : "en"),
                    })}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
