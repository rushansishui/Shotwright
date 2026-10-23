import { useRef, useState } from "react";
import { api } from "../api";
import type { Aspect, SubtitleStyle, VisualMode, VisualPatch } from "../types";
import { Section, Segmented, Slider } from "./Controls";
import { providerName, useI18n } from "../i18n";
import { RenderPanel } from "./RenderPanel";
import { Versions } from "./Versions";
import { ShotCard } from "./ShotCard";
import { useEditor } from "./editor/useEditor";
import { BgmSection } from "./editor/BgmSection";
import { LookSection } from "./editor/LookSection";
import { projectNeedsExpansion } from "../prompts";
import { ReferencesSection } from "./editor/ReferencesSection";
import { OverlaySection } from "./editor/OverlaySection";

export function Editor({ projectId }: { projectId: string }) {
  const {
    project,
    error,
    setError,
    busy,
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

  if (!project) {
    return <main className="page">{error ? <p className="hint error">{error}</p> : <p className="muted">{t("common.loading")}</p>}</main>;
  }

  const id = project.id;
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
          <a className="btn ghost" href="#/">
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

        <Section
          step={1}
          title={t("editor.shots")}
          extra={
            <div className="row">
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
            </div>
          }
        >
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
                busy={shot.id in busy}
                busySeconds={shot.id in busy ? Math.max(0, Math.round((now - busy[shot.id]) / 1000)) : 0}
                aiVideo={aiVideo}
                aiImage={aiImage}
                sourceModes={sourceModes}
                onFocus={() => setActiveIndex(index)}
                onUpdate={(body: { text?: string; visual?: VisualPatch }) => run(() => api.updateShot(id, shot.id, body))}
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
                onMove={(delta) => moveShot(index, delta)}
                onInsert={() => run(() => api.addShot(id, shot.id))}
                onSplit={(at) => run(() => api.splitShot(id, shot.id, at), shot.id)}
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
        </Section>

        <Section step={2} title={t("look.section")}>
          <LookSection project={project} run={run} />
          <div className="divider" />
          <ReferencesSection project={project} run={run} setError={setError} />
        </Section>

        <Section step={3} title={t("editor.audio")}>
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
          <div className="divider" />
          <BgmSection
            project={project}
            run={run}
            setError={setError}
            draft={draft}
            setDraft={setDraft}
          />
        </Section>

        <Section
          step={4}
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
              <div className="field">
                <span className="label">{t("editor.color")}</span>
                <div className="row">
                  <label className="color-field">
                    <input type="color" value={project.subtitle.color.toLowerCase()} onChange={(e) => updateSubtitle({ color: e.target.value.toUpperCase() })} />
                    {t("editor.textColor")}
                  </label>
                  <label className="color-field">
                    <input
                      type="color"
                      value={project.subtitle.outline_color.toLowerCase()}
                      onChange={(e) => updateSubtitle({ outline_color: e.target.value.toUpperCase() })}
                    />
                    {t("editor.outlineColor")}
                  </label>
                </div>
              </div>
            </div>
          )}
        </Section>

        <OverlaySection project={project} run={run} />

        <Versions project={project} languages={languages} />
      </div>

      <RenderPanel project={project} paidAvailable={Boolean(aiVideo || aiImage)} shotIndex={activeIndex} onShotIndex={setActiveIndex} onRendered={load} />
    </main>
  );
}
