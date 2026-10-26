import { useEffect, useState } from "react";
import { api } from "../api";
import { BRIEF_TEMPLATES, VIDEO_TYPES, checkBrief, hasIssues } from "../briefs";
import { useI18n } from "../i18n";
import type { MessageKey } from "../messages";
import type { Aspect, LanguageInfo, ProjectSummary, Providers, VideoType } from "../types";
import { Segmented } from "./Controls";

/** 视频诉求的上限，和后端 schemas.MAX_SUBJECT_BYTES 一致。 */
const MAX_SUBJECT_BYTES = 4096;

/** 按 UTF-8 计算字节数：中文一个字 3 字节，emoji 4 字节。 */
function utf8Bytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

export function Home() {
  const [mode, setMode] = useState<"llm" | "split">("llm");
  const [subject, setSubject] = useState("");
  const [videoType, setVideoType] = useState<VideoType>("product_ad");
  /** 诉求检查发现问题时先提示；用户点「仍然生成」后才真正提交。 */
  const [warned, setWarned] = useState(false);
  const [script, setScript] = useState("");
  const [aspect, setAspect] = useState<Aspect>("9:16");
  const [duration, setDuration] = useState(30);
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
  const subjectBytes = utf8Bytes(subject);
  const tooLong = subjectBytes > MAX_SUBJECT_BYTES;
  const canSubmit = mode === "llm" ? subject.trim().length > 0 && !tooLong : script.trim().length > 0;
  const templates = BRIEF_TEMPLATES[lang === "zh" ? "zh" : "en"];
  const issues = checkBrief(subject, videoType);

  function useTemplate() {
    if (subject.trim() && !window.confirm(t("home.templateReplace"))) return;
    setSubject(templates[videoType].template);
    setWarned(false);
  }

  function submit() {
    if (mode === "llm" && !warned && hasIssues(issues)) {
      setWarned(true);
      return;
    }
    void create();
  }

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
        video_type: videoType,
        // 镜头数交给大模型按诉求和时长决定。
        shots: null,
        duration: mode === "llm" ? duration : null,
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
            <span className="label">{t("home.videoType")}</span>
            <Segmented
              label={t("home.videoType")}
              value={videoType}
              onChange={(value) => {
                setVideoType(value);
                setWarned(false);
              }}
              options={VIDEO_TYPES.map((value) => ({ value, label: t(`videoType.${value}` as MessageKey) }))}
            />
            {videoType === "footage" && <p className="hint">{t("home.footageHint")}</p>}
          </div>
        ) : null}
        {mode === "llm" ? (
          <div className="field">
            <div className="field-row">
              <label htmlFor="subject">{t("home.subject")}</label>
              <span className={`muted small ${tooLong ? "bad" : ""}`}>
                {subjectBytes} / {MAX_SUBJECT_BYTES} {t("home.subjectBytes")}
              </span>
            </div>
            <textarea
              id="subject"
              className="input"
              rows={9}
              placeholder={t("home.subjectExample", { example: templates[videoType].example })}
              value={subject}
              onChange={(e) => {
                setSubject(e.target.value);
                setWarned(false);
              }}
              aria-invalid={tooLong}
            />
            <div className="row">
              <button type="button" className="btn small" onClick={useTemplate}>
                {t("home.useTemplate")}
              </button>
              <button type="button" className="btn small ghost" onClick={() => {
                  setSubject(templates[videoType].example);
                  setWarned(false);
                }}>
                {t("home.useExample")}
              </button>
            </div>
            <details className="brief-tips">
              <summary>{t("home.tips")}</summary>
              <ul>
                {["purpose", "audience", "message", "appearance", "style", "include", "noText"].map((key) => (
                  <li key={key}>{t(`home.tip.${key}` as MessageKey)}</li>
                ))}
              </ul>
            </details>
            {tooLong && <p className="hint error">{t("home.subjectTooLong")}</p>}
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
                <label htmlFor="duration">{t("home.duration")}</label>
                <span className="muted">{t("home.durationValue", { n: duration })}</span>
              </div>
              <input
                id="duration"
                type="range"
                min={10}
                max={180}
                step={5}
                value={duration}
                onChange={(e) => setDuration(Number(e.target.value))}
              />
              <p className="hint">{t("home.durationHint")}</p>
            </div>
          )}
        </div>
        {error && <p className="hint error">{error}</p>}
        {warned && mode === "llm" && hasIssues(issues) && (
          <div className="hint warn brief-check" role="alert">
            <p>{t("home.checkTitle")}</p>
            <ul>
              {issues.unfilled > 0 && <li>{t("home.checkUnfilled", { n: issues.unfilled })}</li>}
              {issues.tooShort && <li>{t("home.checkShort")}</li>}
              {issues.noAppearance && <li>{t("home.checkAppearance")}</li>}
            </ul>
          </div>
        )}
        <button type="button" className="btn primary big" disabled={!canSubmit || busy} onClick={submit}>
          {busy
            ? mode === "llm"
              ? t("home.writing")
              : t("home.creating")
            : warned && mode === "llm" && hasIssues(issues)
              ? t("home.submitAnyway")
              : t("home.submit")}
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
              <li key={p.id} className="project-row">
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
                <button
                  type="button"
                  className="btn small ghost"
                  title={t("editor.duplicateHint")}
                  onClick={async () => {
                    try {
                      const copy = await api.duplicateProject(p.id);
                      window.location.hash = `#/p/${copy.id}`;
                    } catch (e) {
                      setError((e as Error).message);
                    }
                  }}
                >
                  {t("editor.duplicate")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
