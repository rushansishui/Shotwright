import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { subtitleMargin } from "../types";
import type { Aspect, ShotUpdate, SubtitleStyle, VisualMode } from "../types";
import { Section, Segmented, Slider } from "./Controls";
import { providerName, useI18n } from "../i18n";
import { RenderPanel } from "./RenderPanel";
import { Versions } from "./Versions";
import { ShotCard } from "./ShotCard";
import { useEditor } from "./editor/useEditor";
import { BgmSection } from "./editor/BgmSection";
import { SfxSection } from "./editor/SfxSection";
import { LookSection } from "./editor/LookSection";
import { projectNeedsExpansion } from "../prompts";
import { ReferencesSection } from "./editor/ReferencesSection";
import { OverlaySection } from "./editor/OverlaySection";
import { TitleSection } from "./editor/TitleSection";
import { TextStyleEditor } from "./editor/TextStyleEditor";
import { KeywordField } from "./editor/KeywordField";
import { PronunciationSection } from "./editor/PronunciationSection";
import { OriginRecord } from "./editor/OriginRecord";
import { ReadinessCard } from "./editor/ReadinessCard";
import { GenerateAll } from "./editor/GenerateAll";
import { ReviewBoard } from "./editor/Review";
import { generationLocked, outdatedShots, preparedSubjects } from "../readiness";
import type { MessageKey } from "../messages";

type EditorStep = 1 | 2 | 3 | 4 | 5;
const STEPS: { n: EditorStep; label: MessageKey }[] = [
  { n: 1, label: "steps.materials" },
  { n: 2, label: "steps.visuals" },
  { n: 3, label: "steps.voice" },
  { n: 4, label: "steps.music" },
  { n: 5, label: "steps.text" },
];
const stepKey = (id: string) => `shotwright.editorStep.${id}`;

function loadStep(id: string): EditorStep | null {
  try {
    const value = Number(localStorage.getItem(stepKey(id)));
    return value >= 1 && value <= 5 ? (value as EditorStep) : null;
  } catch {
    return null;
  }
}

export function Editor({ projectId, onOrigin }: { projectId: string; onOrigin?: (origin: "create" | "cut") => void }) {
  const {
    project,
    error,
    setError,
    busy,
    queued,
    now,
    activeIndex,
    setActiveIndex,
    voices,
    languages,
    providers,
    title,
    setTitle,
    draft,
    setDraft,
    load,
    run,
    updateVoice,
  } = useEditor(projectId);
  const { t, lang } = useI18n();
  const footageInput = useRef<HTMLInputElement>(null);
  const [footageBusy, setFootageBusy] = useState(false);
  const [expanding, setExpanding] = useState(false);
  const [marginDraft, setMarginDraft] = useState<number | undefined>(undefined);
  const [styleDraft, setStyleDraft] = useState<Partial<SubtitleStyle>>({});
  const [step, setStepState] = useState<EditorStep | null>(() => loadStep(projectId));
  /** 画面一步里切到审片视图。 */
  const [reviewing, setReviewing] = useState(false);
  /** 一键生成正在处理的镜头，在镜头卡片上显示"生成中"。 */
  const [batchCurrent, setBatchCurrent] = useState<string | null>(null);
  /** 剪辑里导入的任务：返回剪辑页，顶部导航高亮「剪辑」。 */
  const isCut = !!project && (project.origin === "cut" || project.shots.some((s) => s.speech === "original"));
  useEffect(() => {
    if (project) onOrigin?.(isCut ? "cut" : "create");
  }, [project, isCut, onOrigin]);

  if (!project) {
    return <main className="page">{error ? <p className="hint error">{error}</p> : <p className="muted">{t("common.loading")}</p>}</main>;
  }

  const id = project.id;
  const locked = generationLocked(project);
  /** 所有镜头都保留原片人声（剪辑页生成），配音设置用不上。 */
  const allOriginal = project.shots.length > 0 && project.shots.every((s) => s.speech === "original");
  /** 剪辑页生成的项目：可以回到文稿重新剪。 */
  const cutSource = project.shots.find((s) => s.speech === "original" && s.visual.selected)?.visual.selected ?? null;
  // 没选过步骤时：素材还没确认就从「素材」开始，否则从「画面」开始。
  const current: EditorStep = step ?? (locked ? 1 : 2);
  const setStep = (next: EditorStep) => {
    setStepState(next);
    try {
      localStorage.setItem(stepKey(id), String(next));
    } catch {
      /* 存不了就只在这次打开时记住 */
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const outdated = outdatedShots(project);
  const filled = project.shots.filter((s) => s.visual.mode === "color" || (s.visual.selected && project.assets[s.visual.selected])).length;
  const stepState = (n: EditorStep): string => {
    if (n === 1) return locked ? "todo" : preparedSubjects(project).length && project.look?.revised_at ? "warn" : "done";
    if (n === 2) return project.shots.length && filled === project.shots.length ? (outdated.length ? "warn" : "done") : "todo";
    return "";
  };
  const next = (n: EditorStep) =>
    n < 5 ? (
      <div className="step-next">
        <button type="button" className="btn primary" onClick={() => setStep((n + 1) as EditorStep)}>
          {t("steps.next", { name: t(STEPS[n].label) })}
        </button>
      </div>
    ) : null;
  const ttsProviders = providers?.tts ?? [];
  const defaultProvider = (kind: string) => {
    const found = (providers?.[kind] ?? []).find((p) => p.default);
    return found?.configured ? found : null;
  };
  const sourceModes: Record<string, VisualMode> = { upload: "upload" };
  for (const [kind, mode] of [
    ["stock", "stock"],
    ["video_gen", "ai"],
    ["image_gen", "image"],
  ] as const) {
    for (const p of providers?.[kind] ?? []) sourceModes[p.id] ??= mode;
  }
  const llm = defaultProvider("llm");
  const expandCount = llm ? projectNeedsExpansion(project) : 0;
  const aiVideo = defaultProvider("video_gen");
  const videoProviders = (providers?.video_gen ?? []).filter((p) => p.configured);
  const aiImage = defaultProvider("image_gen");
  const updateSubtitle = (patch: Partial<SubtitleStyle>) =>
    run(() => api.updateProject(id, { subtitle: { ...project.subtitle, ...patch } }));

  async function moveShot(index: number, delta: number) {
    const order = project!.shots.map((s) => s.id);
    const [moved] = order.splice(index, 1);
    order.splice(index + delta, 0, moved);
    await run(() => api.reorder(id, order));
    setActiveIndex(index + delta);
  }

  async function uploadForShot(shotId: string, file: File) {
    await run(async () => {
      const asset = await api.upload(id, file);
      return api.updateShot(id, shotId, { visual: { mode: "upload", selected: asset.id } });
    }, shotId);
  }

  async function importFootage(file: File) {
    setFootageBusy(true);
    setError("");
    try {
      const replace = project!.shots.length > 0;
      const result = await api.importFootage(id, file, replace);
      // 切镜在后端完成，重新拉一次项目拿到新镜头；定位到第一个新镜头。
      load();
      setActiveIndex(0);
      if (result.segments === 0) setError(t("footage.empty"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setFootageBusy(false);
    }
  }

  return (
    <main className="page editor">
      <div className="editor-main">
        <div className="titlebar">
          <a className="btn ghost" href={isCut ? "#/cut" : "#/"}>
            {t("editor.back")}
          </a>
          <label className="sr-only" htmlFor="title">
            {t("editor.titleLabel")}
          </label>
          <input
            id="title"
            className="title-input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => title !== project.title && run(() => api.updateProject(id, { title }))}
          />
          {cutSource && (
            <a className="btn" href={`#/cut/${id}/${cutSource}`}>
              {t("cut.recut")}
            </a>
          )}
          <button
            type="button"
            className="btn"
            title={t("editor.duplicateHint")}
            onClick={async () => {
              const copy = await run(() => api.duplicateProject(id));
              if (copy) window.location.hash = `#/p/${copy.id}`;
            }}
          >
            {t("editor.duplicate")}
          </button>
          <Segmented
            label={t("editor.aspect")}
            value={project.canvas.aspect}
            onChange={(aspect: Aspect) => run(() => api.updateProject(id, { canvas: { ...project.canvas, aspect } }))}
            options={[
              { value: "9:16", label: "9:16" },
              { value: "16:9", label: "16:9" },
              { value: "1:1", label: "1:1" },
            ]}
          />
        </div>

        {error && (
          <p className="hint error" role="alert">
            {error}
          </p>
        )}

        <nav className="editor-steps" aria-label={t("steps.label")}>
          {STEPS.map((item) => (
            <button
              key={item.n}
              type="button"
              className={`${current === item.n ? "on" : ""} ${stepState(item.n)}`}
              aria-current={current === item.n ? "step" : undefined}
              onClick={() => setStep(item.n)}
            >
              <span className="step-num">{stepState(item.n) === "done" ? "✓" : item.n}</span>
              <span className="step-label">{t(item.label)}</span>
              {item.n === 2 && project.shots.length > 0 && (
                <span className="step-count">
                  {filled}/{project.shots.length}
                </span>
              )}
            </button>
          ))}
        </nav>

        {current === 1 && (
          <>
            <OriginRecord project={project} run={(action) => run(action)} />
            <Section title={t("look.section")}>
              <ReadinessCard project={project} run={(action) => run(action)} onConfirmed={() => setStep(2)} />
              <div className="divider" />
              <LookSection project={project} run={run} setError={setError} />
              <div className="divider" />
              <ReferencesSection project={project} run={run} setError={setError} />
            </Section>
          </>
        )}

        {current === 2 && (
        <Section
          title={reviewing ? t("review.open") : t("editor.shots")}
          extra={
            <div className="row">
              {project.shots.length > 0 && (
                <button type="button" className="btn" onClick={() => setReviewing(!reviewing)}>
                  {reviewing ? t("review.close") : t("review.open")}
                </button>
              )}
              {!reviewing && (
              <>
              <input
                ref={footageInput}
                type="file"
                accept="video/*"
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) importFootage(file);
                  e.target.value = "";
                }}
              />
              <button type="button" className="btn" disabled={footageBusy} onClick={() => footageInput.current?.click()}>
                {footageBusy ? t("footage.importing") : t("footage.import")}
              </button>
              <button type="button" className="btn" onClick={() => run(() => api.addShot(id))}>
                {t("editor.addShot")}
              </button>
              </>
              )}
            </div>
          }
        >
          {locked && (
            <div className="hint warn lock-hint">
              <span>{t("steps.lockedHint")}</span>
              <button type="button" className="btn small" onClick={() => setStep(1)}>
                {t("steps.gotoMaterials")}
              </button>
            </div>
          )}
          {!locked && outdated.length > 0 && (
            <div className="hint warn lock-hint">
              <span>{t("ready.revisedShots", { list: outdated.map((i) => i + 1).join("、") })}</span>
              <button type="button" className="btn small" onClick={() => setStep(1)}>
                {t("steps.gotoMaterials")}
              </button>
            </div>
          )}
          <GenerateAll
            project={project}
            locked={locked}
            onlyRedo={reviewing}
            onChanged={load}
            onCurrent={setBatchCurrent}
            setError={setError}
          />
          {reviewing ? (
            <ReviewBoard project={project} run={run} busy={(shotId) => shotId in busy || shotId === batchCurrent} />
          ) : (
          <>
          {project.shots.length === 0 && <p className="muted">{t("editor.noShots")}</p>}
          {expandCount > 0 && (
            <div className="hint warn expand-hint">
              <span>{t("editor.expandHint")}</span>
              <button
                type="button"
                className="btn small"
                disabled={expanding}
                onClick={async () => {
                  setExpanding(true);
                  await run(() => api.expand(id));
                  setExpanding(false);
                }}
              >
                {expanding ? t("editor.expanding") : t("editor.expand")}
              </button>
            </div>
          )}
          <div className="shots">
            {project.shots.map((shot, index) => (
              <ShotCard
                key={shot.id}
                project={project}
                shot={shot}
                index={index}
                active={index === activeIndex}
                busy={shot.id in busy || shot.id === batchCurrent}
                busySeconds={shot.id in busy ? Math.max(0, Math.round((now - busy[shot.id]) / 1000)) : 0}
                queued={queued.has(shot.id)}
                aiVideo={aiVideo}
                aiLocked={locked}
                onGotoMaterials={() => setStep(1)}
                videoProviders={videoProviders}
                aiImage={aiImage}
                sourceModes={sourceModes}
                onFocus={() => setActiveIndex(index)}
                onUpdate={(body: ShotUpdate) => run(() => api.updateShot(id, shot.id, body))}
                onCandidates={(confirmCharge, patch, editSelected, options) =>
                  run(async () => {
                    // 还没失焦保存的提示词先存好，再生成，避免用旧提示词花钱。
                    if (patch !== undefined) await api.updateShot(id, shot.id, { visual: patch });
                    return api.candidates(id, shot.id, confirmCharge, editSelected, options);
                  }, shot.id)
                }
                canRewrite={!!llm}
                onRewrite={(instruction) => run(() => api.rewriteShot(id, shot.id, instruction), shot.id)}
                onUpload={(file) => uploadForShot(shot.id, file)}
                onRemoveCandidate={(assetId) => run(() => api.removeCandidate(id, shot.id, assetId), shot.id)}
                onMove={(delta) => moveShot(index, delta)}
                onInsert={() => run(() => api.addShot(id, shot.id))}
                onSplit={(at) => run(() => api.splitShot(id, shot.id, at), shot.id)}
                onSplitText={() => {
                  if (window.confirm(t("editor.splitText", { n: index + 1 }))) {
                    run(() => api.splitText(id, shot.id), shot.id);
                  }
                }}
                onMerge={() => {
                  if (window.confirm(t("editor.mergeShot", { n: index + 1, m: index + 2 }))) {
                    run(() => api.mergeShot(id, shot.id));
                  }
                }}
                onDelete={() => {
                  if (window.confirm(t("editor.deleteShot", { n: index + 1 }))) {
                    run(() => api.deleteShot(id, shot.id));
                    setActiveIndex(Math.max(0, Math.min(activeIndex, project.shots.length - 2)));
                  }
                }}
              />
            ))}
          </div>
          </>
          )}
          {next(2)}
        </Section>
        )}

        {current === 3 && (
        <>
        <Section title={t("editor.audio")}>
          <div className="field">
            <div className="field-row">
              <label htmlFor="language">{t("editor.language")}</label>
              <span className="muted small">{t("editor.languageHint")}</span>
            </div>
            <select
              id="language"
              className="input"
              value={project.language}
              onChange={(e) => run(() => api.updateProject(id, { language: e.target.value }))}
            >
              {!languages.some((l) => l.code === project.language) && <option value={project.language}>{project.language}</option>}
              {languages.map((l) => (
                <option key={l.code} value={l.code}>
                  {lang === "zh" && l.name_zh !== l.name ? `${l.name}（${l.name_zh}）` : l.name}
                </option>
              ))}
            </select>
          </div>
          {allOriginal && <p className="hint small">{t("voice.allOriginal")}</p>}
          {/* 全是原声镜头时配音设置收起来（仍可展开，给改成"用文案配音"的镜头用）。 */}
          <details className="voice-more" open={!allOriginal} key={allOriginal ? "original" : "tts"}>
          <summary className={allOriginal ? "" : "hidden-summary"}>{t("voice.showSettings")}</summary>
          <div className="grid-2">
            <div className="field">
              <label htmlFor="tts">{t("editor.tts")}</label>
              <select id="tts" className="input" value={project.voice.provider} onChange={(e) => updateVoice({ provider: e.target.value })}>
                {ttsProviders.map((p) => (
                  <option key={p.id} value={p.id}>
                    {providerName(p, lang)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="voice">{t("editor.voice")}</label>
              <select
                id="voice"
                className="input"
                value={project.voice.voice}
                disabled={project.voice.provider === "silent"}
                onChange={(e) => updateVoice({ voice: e.target.value })}
              >
                {!voices.some((v) => v.id === project.voice.voice) && <option value={project.voice.voice}>{project.voice.voice}</option>}
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </div>
            <Slider
              id="rate"
              label={t("editor.rate")}
              value={draft.rate ?? project.voice.rate}
              min={0.5}
              max={2}
              step={0.05}
              format={(v) => `${v.toFixed(2)}×`}
              onInput={(rate) => setDraft({ ...draft, rate })}
              onCommit={(rate) => updateVoice({ rate }).then(() => setDraft({ ...draft, rate: undefined }))}
            />
            <Slider
              id="volume"
              label={t("editor.volume")}
              value={draft.volume ?? project.voice.volume}
              min={0}
              max={2}
              step={0.05}
              format={(v) => `${Math.round(v * 100)}%`}
              onInput={(volume) => setDraft({ ...draft, volume })}
              onCommit={(volume) => updateVoice({ volume }).then(() => setDraft({ ...draft, volume: undefined }))}
            />
          </div>
          <PronunciationSection project={project} canSuggest={!!llm} run={(action) => run(action)} onError={setError} />
          </details>
        </Section>

        <Section
          title={t("editor.subtitles")}
          extra={
            <label className="switch">
              <input type="checkbox" checked={project.subtitle.enabled} onChange={(e) => updateSubtitle({ enabled: e.target.checked })} />
              {t("editor.subtitlesOn")}
            </label>
          }
        >
          {project.subtitle.enabled && (
            <div className="grid-2">
              <div className="field">
                <span className="label">{t("editor.position")}</span>
                <Segmented
                  label={t("editor.positionLabel")}
                  value={project.subtitle.position}
                  onChange={(position) => updateSubtitle({ position })}
                  options={[
                    { value: "top", label: t("editor.position.top") },
                    { value: "middle", label: t("editor.position.middle") },
                    { value: "bottom", label: t("editor.position.bottom") },
                  ]}
                />
              </div>
              {project.subtitle.position !== "middle" && (
                <div className="field">
                  <Slider
                    id="sub-margin"
                    label={t("editor.subtitleMargin")}
                    value={marginDraft ?? subtitleMargin(project.subtitle, project.canvas.aspect)}
                    min={0}
                    max={project.canvas.aspect === "9:16" ? 600 : 400}
                    step={10}
                    format={(v) => (project.subtitle.margin === null && marginDraft === undefined ? t("editor.subtitleMarginAuto", { n: v }) : `${v}`)}
                    onInput={setMarginDraft}
                    onCommit={(margin) => updateSubtitle({ margin }).then(() => setMarginDraft(undefined))}
                  />
                  {project.subtitle.margin !== null && (
                    <button type="button" className="linkish small" onClick={() => updateSubtitle({ margin: null })}>
                      {t("editor.subtitleMarginReset")}
                    </button>
                  )}
                </div>
              )}
              <Slider
                id="sub-size"
                label={t("editor.size")}
                value={draft.size ?? project.subtitle.size}
                min={24}
                max={120}
                step={2}
                format={(v) => `${v}`}
                onInput={(size) => setDraft({ ...draft, size })}
                onCommit={(size) => updateSubtitle({ size }).then(() => setDraft({ ...draft, size: undefined }))}
              />
              <div className="span-2">
                <TextStyleEditor
                  id="sub-style"
                  value={{ ...project.subtitle, ...styleDraft }}
                  allowKaraoke
                  onError={setError}
                  onChange={(change, commit) => {
                    // 字幕字体不会是空（没有「跟随」选项）。
                    const patch = change as Partial<SubtitleStyle>;
                    setStyleDraft((current) => ({ ...current, ...patch }));
                    if (commit) updateSubtitle(patch).then(() => setStyleDraft({}));
                  }}
                />
              </div>
              <div className="span-2">
                <KeywordField project={project} canSuggest={!!llm} update={updateSubtitle} run={(action) => run(action)} />
              </div>
            </div>
          )}
          {next(3)}
        </Section>
        </>
        )}

        {current === 4 && (
          <Section title={t("steps.music")}>
            <BgmSection project={project} run={run} setError={setError} draft={draft} setDraft={setDraft} />
            <div className="divider" />
            <SfxSection project={project} run={run} setError={setError} />
            {next(4)}
          </Section>
        )}

        {current === 5 && (
          <>
            <TitleSection project={project} canSuggest={!!llm} run={run} onError={setError} />
            <OverlaySection project={project} run={run} />
            <Versions project={project} languages={languages} />
          </>
        )}
      </div>

      <RenderPanel
        project={project}
        paidAvailable={Boolean(aiVideo || aiImage) && !locked}
        shotIndex={activeIndex}
        onShotIndex={setActiveIndex}
        onRendered={load}
        onBudget={(budget) => run(() => api.updateProject(id, { budget }))}
        onDelivery={(delivery) => run(() => api.updateProject(id, { delivery }))}
      />
    </main>
  );
}
