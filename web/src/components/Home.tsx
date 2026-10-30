import { useEffect, useState } from "react";
import { api } from "../api";
import { ProjectList } from "./ProjectList";
import { BRIEF_TEMPLATES, VIDEO_TYPES, checkBrief, hasIssues } from "../briefs";
import { useI18n } from "../i18n";
import type { MessageKey } from "../messages";
import type { Aspect, LanguageInfo, ProjectSummary, Providers, VideoType } from "../types";
import { Icon, Segmented } from "./Controls";
import { MaterialsPicker, type PendingMaterial } from "./MaterialsPicker";
import { SetupCard } from "./SetupCard";
import { DRAFT_KEY, loadDraft, saveDraft, type Step } from "../draft";

/** 视频诉求的上限，和后端 schemas.MAX_SUBJECT_BYTES 一致。 */
const MAX_SUBJECT_BYTES = 4096;

/** 按 UTF-8 计算字节数：中文一个字 3 字节，emoji 4 字节。 */
function utf8Bytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** 估算朗读秒数（和后端 silent.estimate_seconds 一致：中文每秒约 4.5 字）。 */
function speakSeconds(text: string): number {
  const cjk = (text.match(/[぀-ヿ㐀-鿿가-힯]/g) ?? []).length;
  const words = (text.replace(/[぀-ヿ㐀-鿿가-힯]/g, " ").match(/[A-Za-z0-9]+/g) ?? []).length;
  return Math.max(1.5, cjk / 4.5 + words / 2.6);
}

export function Home() {
  const [initial] = useState(loadDraft);
  const [step, setStep] = useState<Step>(initial.step ?? 1);
  const [idea, setIdea] = useState(initial.idea ?? "");
  const [subject, setSubject] = useState(initial.subject ?? "");
  const [title, setTitle] = useState(initial.title ?? "");
  const [lines, setLines] = useState<string[]>(initial.lines ?? []);
  const [videoType, setVideoType] = useState<VideoType>(initial.videoType ?? "product_ad");
  const [materials, setMaterials] = useState<PendingMaterial[]>([]);
  /** 诉求检查发现问题时先提示；用户点「仍然继续」后才往下走。 */
  const [warned, setWarned] = useState<"script" | "board" | null>(null);
  const [aspect, setAspect] = useState<Aspect>(initial.aspect ?? "9:16");
  const [duration, setDuration] = useState(initial.duration ?? 30);
  const [busy, setBusy] = useState<"normalize" | "script" | "create" | null>(null);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  /** null 表示还在加载，避免先闪一下「还没有项目」。 */
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [providers, setProviders] = useState<Providers | null>(null);
  const [languages, setLanguages] = useState<LanguageInfo[]>([]);
  const { t, lang } = useI18n();
  const [language, setLanguage] = useState(initial.language ?? (lang === "zh" ? "zh-CN" : "en-US"));

  useEffect(() => {
    api.listProjects().then(setProjects).catch(() => setProjects([]));
    api.providers().then(setProviders).catch(() => setProviders(null));
    api.languages().then(setLanguages).catch(() => setLanguages([]));
  }, []);

  useEffect(
    () => saveDraft({ step, idea, subject, title, lines, videoType, aspect, duration, language }),
    [step, idea, subject, title, lines, videoType, aspect, duration, language],
  );

  const llmReady = providers?.llm?.some((p) => p.default && p.configured) ?? false;
  const subjectBytes = utf8Bytes(subject);
  const tooLong = subjectBytes > MAX_SUBJECT_BYTES;
  const templates = BRIEF_TEMPLATES[lang === "zh" ? "zh" : "en"];
  const issues = checkBrief(subject, videoType);
  const cleanLines = lines.map((line) => line.trim()).filter(Boolean);
  const scriptSeconds = Math.round(cleanLines.reduce((sum, line) => sum + speakSeconds(line), 0));
  const notes = materials.map((item) => ({ kind: item.kind, role: item.role, name: item.file.name, note: item.note }));

  function useTemplate() {
    if (subject.trim() && !window.confirm(t("home.templateReplace"))) return;
    setSubject(templates[videoType].template);
    setWarned(null);
  }

  async function normalize() {
    setBusy("normalize");
    setError("");
    try {
      const result = await api.normalizeBrief({
        idea: idea.trim(),
        template: templates[videoType].template,
        video_type: videoType,
        language,
        aspect,
        duration,
        materials: notes,
      });
      if (subject.trim() && subject.trim() !== result.brief.trim() && !window.confirm(t("flow.replaceBrief"))) return;
      setSubject(result.brief);
      setWarned(null);
      setStep(2);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function writeScript(force = false) {
    if (!force && hasIssues(issues)) {
      setWarned("script");
      return;
    }
    if (cleanLines.length && !window.confirm(t("flow.replaceScript"))) return;
    setBusy("script");
    setError("");
    try {
      const result = await api.writeScript({ brief: subject.trim(), video_type: videoType, language, aspect, duration, materials: notes });
      setLines(result.lines);
      setTitle(result.title);
      setWarned(null);
      setStep(3);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  /** 先建空项目、上传素材，再写分镜（素材要先有 id 才能放进镜头和主体）。失败时删掉这个空项目。 */
  async function createFromBrief(useScript: boolean, force = false) {
    if (!useScript && !force && hasIssues(issues)) {
      setWarned("board");
      return;
    }
    setBusy("create");
    setError("");
    let projectId: string | null = null;
    try {
      const project = await api.createProject({
        mode: "empty",
        title: useScript ? title.trim() : "",
        canvas: { aspect },
        language,
        video_type: videoType,
        duration,
      });
      projectId = project.id;
      const uploaded = await uploadMaterials(project.id);
      setProgress(t("flow.writingBoard"));
      await api.storyboard(project.id, {
        subject: subject.trim(),
        idea: idea.trim(),
        script: useScript ? cleanLines : null,
        duration,
        materials: uploaded,
      });
      localStorage.removeItem(DRAFT_KEY);
      window.location.hash = `#/p/${project.id}`;
    } catch (e) {
      setError((e as Error).message);
      if (projectId) await api.deleteProject(projectId).catch(() => undefined);
    } finally {
      setBusy(null);
      setProgress("");
    }
  }

  async function uploadMaterials(projectId: string) {
    const uploaded = [];
    for (const [index, item] of materials.entries()) {
      setProgress(t("flow.uploading", { n: index + 1, total: materials.length }));
      const asset = await api.upload(projectId, item.file, item.kind === "audio");
      uploaded.push({ kind: item.kind, role: item.role, name: item.file.name, note: item.note, asset_id: asset.id });
    }
    return uploaded;
  }

  /** 不用大模型：自己写口播脚本。想法里已经写了几句话时，按句拆成初稿。 */
  function writeOwn() {
    if (!cleanLines.length) {
      const drafted = idea
        .split(/(?<=[。！？!?；;\n])/)
        .map((part) => part.trim())
        .filter(Boolean)
        .slice(0, 60);
      setLines(drafted.length ? drafted : ["", "", ""]);
    }
    setWarned(null);
    setStep(3);
  }

  function setLine(index: number, value: string) {
    setLines(lines.map((line, i) => (i === index ? value : line)));
  }

  function moveLine(index: number, delta: number) {
    const next = [...lines];
    const [item] = next.splice(index, 1);
    next.splice(index + delta, 0, item);
    setLines(next);
  }

  const steps: { n: Step; label: MessageKey }[] = [
    { n: 1, label: "flow.step1" },
    { n: 2, label: "flow.step2" },
    { n: 3, label: "flow.step3" },
  ];
  const reachable = (n: Step) => n === 1 || (n === 2 && (subject.trim() || step >= 2)) || (n === 3 && lines.length > 0);
  const working = busy !== null;

  /** 视频设置：类型、语言、比例、时长。只出现一次，任何一步都能改。 */
  const settings = (
    <div className="video-settings">
      <div className="field">
        <span className="label">{t("home.videoType")}</span>
        <Segmented
          label={t("home.videoType")}
          value={videoType}
          onChange={(value) => {
            setVideoType(value);
            setWarned(null);
          }}
          options={VIDEO_TYPES.map((value) => ({ value, label: t(`videoType.${value}` as MessageKey) }))}
        />
        {videoType === "footage" && <p className="hint small">{t("home.footageHint")}</p>}
      </div>
      <div className="settings-row three">
        <div className="field">
          <label htmlFor="language" title={t("home.languageHint")}>
            {t("home.language")}
          </label>
          <select id="language" className="input" value={language} onChange={(e) => setLanguage(e.target.value)}>
            {languages.map((l) => (
              <option key={l.code} value={l.code}>
                {lang === "zh" && l.name_zh !== l.name ? `${l.name}（${l.name_zh}）` : l.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <span className="label">{t("home.aspect")}</span>
          <Segmented
            label={t("home.aspect")}
            value={aspect}
            onChange={setAspect}
            options={[
              { value: "9:16", label: t("home.aspect.portraitShort") },
              { value: "16:9", label: t("home.aspect.landscapeShort") },
              { value: "1:1", label: t("home.aspect.squareShort") },
            ]}
          />
        </div>
        <div className="field">
          <div className="field-row">
            <label htmlFor="duration" title={t("home.durationHint")}>
              {t("home.duration")}
            </label>
            <span className="muted small">{t("home.durationValue", { n: duration })}</span>
          </div>
          <input id="duration" type="range" min={10} max={180} step={5} value={duration} onChange={(e) => setDuration(Number(e.target.value))} />
        </div>
      </div>
    </div>
  );

  return (
    <main className="page home-layout">
      <section className="card new-video home-create">
        <div className="card-head new-video-head">
          <h1>{t("home.newVideo")}</h1>
        </div>

        {providers && !llmReady && (
          <SetupCard
            providers={providers}
            onConfigured={() => api.providers().then(setProviders).catch(() => undefined)}
            onWriteOwn={writeOwn}
          />
        )}

        <ol className="flow-steps" aria-label={t("flow.steps")}>
          {steps.map((item) => (
            <li key={item.n} className={step === item.n ? "on" : step > item.n ? "done" : ""}>
              <button type="button" disabled={working || !reachable(item.n)} aria-current={step === item.n ? "step" : undefined} onClick={() => setStep(item.n)}>
                <span className="flow-num">{step > item.n ? "✓" : item.n}</span>
                {t(item.label)}
              </button>
            </li>
          ))}
        </ol>

        {settings}

        {step === 1 && (
          <div className="flow-body">
            <div className="field grow">
              <div className="field-row">
                <label htmlFor="idea">{t("flow.idea")}</label>
                <span className="muted small">{t("flow.ideaHint")}</span>
              </div>
              <textarea
                id="idea"
                className="input"
                rows={5}
                maxLength={4000}
                placeholder={t(`flow.ideaExample.${videoType}` as MessageKey)}
                value={idea}
                onChange={(e) => setIdea(e.target.value)}
              />
            </div>
            <MaterialsPicker items={materials} onChange={setMaterials} videoType={videoType} disabled={working} />
            {error && <p className="hint error">{error}</p>}
            <div className="flow-actions">
              {llmReady || !providers ? (
                <button type="button" className="btn primary big" disabled={!idea.trim() || working} onClick={normalize}>
                  {busy === "normalize" ? t("flow.normalizing") : t("flow.normalize")}
                </button>
              ) : (
                <button type="button" className="btn primary big" disabled={working} onClick={writeOwn}>
                  {t("setup.writeOwn")}
                </button>
              )}
              <button
                type="button"
                className="btn ghost"
                disabled={working}
                onClick={() => {
                  if (!subject.trim()) setSubject(templates[videoType].template);
                  setStep(2);
                }}
              >
                {t("flow.writeMyself")}
              </button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="flow-body">
            <div className="field grow">
              <div className="field-row">
                <label htmlFor="subject">{t("home.subject")}</label>
                <span className={`muted small ${tooLong ? "bad" : ""}`}>
                  {subjectBytes} / {MAX_SUBJECT_BYTES} {t("home.subjectBytes")}
                </span>
              </div>
              <p className="muted small">{t("flow.briefHint")}</p>
              <textarea
                id="subject"
                className="input"
                rows={12}
                placeholder={t("home.subjectExample", { example: templates[videoType].example })}
                value={subject}
                onChange={(e) => {
                  setSubject(e.target.value);
                  setWarned(null);
                }}
                aria-invalid={tooLong}
              />
              <div className="row">
                <button type="button" className="btn small" onClick={useTemplate}>
                  {t("home.useTemplate")}
                </button>
                <button
                  type="button"
                  className="btn small ghost"
                  onClick={() => {
                    setSubject(templates[videoType].example);
                    setWarned(null);
                  }}
                >
                  {t("home.useExample")}
                </button>
                {idea.trim() && llmReady && (
                  <button type="button" className="btn small ghost" disabled={working} onClick={normalize}>
                    {busy === "normalize" ? t("flow.normalizing") : t("flow.renormalize")}
                  </button>
                )}
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
            </div>
            {materials.length > 0 && <p className="muted small">{t("flow.materialsCount", { n: materials.length })}</p>}
            {error && <p className="hint error">{error}</p>}
            {warned && hasIssues(issues) && (
              <div className="hint warn brief-check" role="alert">
                <p>{t("home.checkTitle")}</p>
                <ul>
                  {issues.unfilled > 0 && <li>{t("home.checkUnfilled", { n: issues.unfilled })}</li>}
                  {issues.tooShort && <li>{t("home.checkShort")}</li>}
                  {issues.noAppearance && <li>{t("home.checkAppearance")}</li>}
                </ul>
              </div>
            )}
            <div className="flow-actions">
              {llmReady || !providers ? (
                <>
                  <button
                    type="button"
                    className="btn primary big"
                    disabled={!subject.trim() || tooLong || working}
                    onClick={() => writeScript(warned === "script")}
                  >
                    {busy === "script" ? t("flow.scripting") : warned === "script" && hasIssues(issues) ? t("flow.scriptAnyway") : t("flow.writeScript")}
                  </button>
                  <button
                    type="button"
                    className="btn"
                    disabled={!subject.trim() || tooLong || working}
                    onClick={() => createFromBrief(false, warned === "board")}
                  >
                    {busy === "create" ? progress || t("home.writing") : warned === "board" && hasIssues(issues) ? t("home.submitAnyway") : t("flow.skipScript")}
                  </button>
                </>
              ) : (
                <button type="button" className="btn primary big" disabled={working} onClick={writeOwn}>
                  {t("setup.writeOwn")}
                </button>
              )}
              <button type="button" className="btn ghost" disabled={working} onClick={() => setStep(1)}>
                {t("flow.back")}
              </button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="flow-body">
            <div className="field grow">
              <label htmlFor="title">{t("flow.title")}</label>
              <input id="title" className="input" maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} />
            </div>
            <div className="field grow script-field">
              <div className="field-row">
                <span className="label">{t("flow.script")}</span>
                <span className={`muted small ${Math.abs(scriptSeconds - duration) > Math.max(5, duration * 0.3) ? "bad" : ""}`}>
                  {t("flow.scriptStats", { n: cleanLines.length, seconds: scriptSeconds, target: duration })}
                </span>
              </div>
              <p className="muted small">{t("flow.scriptHint")}</p>
              <ol className="script-lines">
                {lines.map((line, index) => (
                  <li key={index} className="script-line">
                    <span className="script-num">{index + 1}</span>
                    <textarea
                      className="input"
                      rows={2}
                      aria-label={t("flow.lineLabel", { n: index + 1 })}
                      value={line}
                      onChange={(e) => setLine(index, e.target.value)}
                    />
                    <span className="muted small script-sec">{line.trim() ? `${Math.round(speakSeconds(line))}s` : ""}</span>
                    <div className="script-tools">
                      <button type="button" className="btn icon ghost" aria-label={t("flow.lineUp")} title={t("flow.lineUp")} disabled={index === 0} onClick={() => moveLine(index, -1)}>
                        <Icon name="up" />
                      </button>
                      <button
                        type="button"
                        className="btn icon ghost"
                        aria-label={t("flow.lineDown")}
                        title={t("flow.lineDown")}
                        disabled={index === lines.length - 1}
                        onClick={() => moveLine(index, 1)}
                      >
                        <Icon name="down" />
                      </button>
                      <button
                        type="button"
                        className="btn icon ghost danger"
                        aria-label={t("flow.lineRemove")}
                        title={t("flow.lineRemove")}
                        onClick={() => setLines(lines.filter((_, i) => i !== index))}
                      >
                        <Icon name="trash" />
                      </button>
                    </div>
                  </li>
                ))}
              </ol>
              <button type="button" className="btn small" onClick={() => setLines([...lines, ""])} disabled={lines.length >= 60}>
                <Icon name="plus" /> {t("flow.lineAdd")}
              </button>
            </div>
            {materials.length > 0 && <p className="muted small">{t("flow.materialsCount", { n: materials.length })}</p>}
            {error && <p className="hint error">{error}</p>}
            <div className="flow-actions">
              <button type="button" className="btn primary big" disabled={!cleanLines.length || working} onClick={() => createFromBrief(true)}>
                {busy === "create" ? progress || t("home.writing") : t("flow.createFromScript")}
              </button>
              {(llmReady || !providers) && (
                <button type="button" className="btn" disabled={working || !subject.trim()} onClick={() => writeScript(true)}>
                  {busy === "script" ? t("flow.scripting") : t("flow.rewriteScript")}
                </button>
              )}
              <button type="button" className="btn ghost" disabled={working} onClick={() => setStep(2)}>
                {t("flow.back")}
              </button>
            </div>
          </div>
        )}

      </section>

      <ProjectList
        className="home-projects"
        compact
        projects={(projects ?? []).filter((p) => p.origin !== "cut")}
        loading={projects === null}
        onError={setError}
        onChanged={() => api.listProjects().then(setProjects).catch(() => setProjects([]))}
      />
    </main>
  );
}
