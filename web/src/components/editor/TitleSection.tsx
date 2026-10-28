import { useEffect, useState } from "react";
import { api } from "../../api";
import { useI18n } from "../../i18n";
import { TEXT_STYLE_DEFAULTS } from "../../types";
import type { OverlayPosition, Project, TextStyle, TitleSettings } from "../../types";
import { Section, Segmented, Slider } from "../Controls";
import { TextStyleEditor } from "./TextStyleEditor";

const DEFAULT_TITLES: TitleSettings = {
  headline: { ...TEXT_STYLE_DEFAULTS, outline: 5, enabled: false, text: "", size: 76, margin: 160 },
  intro: { ...TEXT_STYLE_DEFAULTS, outline: 6, animation: "fade", enabled: false, text: "", subtitle: "", duration: 2.5, size: 110, dim: 0.55 },
  shot: { ...TEXT_STYLE_DEFAULTS, color: "#FFE45C", outline: 4, animation: "fade", size: 56, position: "top", margin: 330 },
  candidates: [],
};

interface TitleSectionProps {
  project: Project;
  step?: number;
  canSuggest: boolean;
  run: (action: () => Promise<Project>, shotId?: string) => Promise<Project | null>;
  onError?: (message: string) => void;
}

/** 标题：画面顶部常驻大标题、片头标题卡、镜头小标题样式，以及 AI 生成标题。 */
export function TitleSection({ project, step, canSuggest, run, onError }: TitleSectionProps) {
  const { t } = useI18n();
  const saved: TitleSettings = {
    ...DEFAULT_TITLES,
    ...(project.titles ?? {}),
  };
  const [draft, setDraft] = useState<TitleSettings>(saved);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft({ ...DEFAULT_TITLES, ...(project.titles ?? {}) }), [project.titles]);

  const save = (next: TitleSettings = draft) => {
    if (JSON.stringify(next) !== JSON.stringify(saved)) run(() => api.updateProject(project.id, { titles: next }));
  };
  const setHeadline = (patch: Partial<TitleSettings["headline"]>, commit = false) => {
    const next = { ...draft, headline: { ...draft.headline, ...patch } };
    setDraft(next);
    if (commit) save(next);
  };
  const setIntro = (patch: Partial<TitleSettings["intro"]>, commit = false) => {
    const next = { ...draft, intro: { ...draft.intro, ...patch } };
    setDraft(next);
    if (commit) save(next);
  };
  const setShot = (patch: Partial<TitleSettings["shot"]>, commit = false) => {
    const next = { ...draft, shot: { ...draft.shot, ...patch } };
    setDraft(next);
    if (commit) save(next);
  };

  async function suggest() {
    setBusy(true);
    try {
      await run(() => api.suggestTitles(project.id, { shot_titles: true, apply_headline: !draft.headline.text.trim() }));
    } finally {
      setBusy(false);
    }
  }

  const { headline, intro, shot } = draft;
  return (
    <Section
      step={step}
      title={t("titles.title")}
      extra={
        canSuggest ? (
          <button type="button" className="btn" disabled={busy} onClick={suggest} title={t("titles.suggestHint")}>
            {busy ? t("titles.suggesting") : t("titles.suggest")}
          </button>
        ) : undefined
      }
    >
      <div className="field">
        <label className="check">
          <input type="checkbox" checked={headline.enabled} onChange={(e) => setHeadline({ enabled: e.target.checked }, true)} />
          {t("titles.headline")}
        </label>
        <textarea
          className="input"
          rows={2}
          aria-label={t("titles.headlineText")}
          placeholder={t("titles.headlinePlaceholder")}
          value={headline.text}
          onChange={(e) => setHeadline({ text: e.target.value })}
          onBlur={() => save()}
        />
        {draft.candidates.length > 0 && (
          <div className="title-candidates">
            <span className="muted small">{t("titles.candidates")}</span>
            {draft.candidates.map((text) => (
              <button
                type="button"
                key={text}
                className={`chip ${headline.text === text ? "on" : ""}`}
                onClick={() => setHeadline({ text, enabled: true }, true)}
              >
                {text}
              </button>
            ))}
          </div>
        )}
        {headline.enabled && (
          <div className="grid-2">
            <Slider
              id="headline-size"
              label={t("titles.size")}
              value={headline.size}
              min={32}
              max={140}
              step={2}
              format={(v) => `${v}px`}
              onInput={(size) => setHeadline({ size })}
              onCommit={(size) => setHeadline({ size }, true)}
            />
            <div className="span-2">
              <TextStyleEditor
                id="headline-style"
                value={headline}
                inheritFont={project.subtitle.font}
                onError={onError}
                onChange={(patch: Partial<TextStyle>, commit) => setHeadline(patch, commit)}
              />
            </div>
          </div>
        )}
      </div>

      <div className="divider" />
      <div className="field">
        <label className="check">
          <input type="checkbox" checked={intro.enabled} onChange={(e) => setIntro({ enabled: e.target.checked }, true)} />
          {t("titles.intro")}
        </label>
        <p className="muted small">{t("titles.introHint")}</p>
        {intro.enabled && (
          <>
            <input
              className="input"
              aria-label={t("titles.introText")}
              placeholder={headline.text || project.title || t("titles.introText")}
              value={intro.text}
              onChange={(e) => setIntro({ text: e.target.value })}
              onBlur={() => save()}
            />
            <input
              className="input"
              aria-label={t("titles.introSubtitle")}
              placeholder={t("titles.introSubtitle")}
              value={intro.subtitle}
              onChange={(e) => setIntro({ subtitle: e.target.value })}
              onBlur={() => save()}
            />
            <div className="grid-2">
              <Slider
                id="intro-duration"
                label={t("titles.introDuration")}
                value={intro.duration}
                min={1}
                max={8}
                step={0.5}
                format={(v) => t("common.seconds", { n: v })}
                onInput={(duration) => setIntro({ duration })}
                onCommit={(duration) => setIntro({ duration }, true)}
              />
              <Slider
                id="intro-dim"
                label={t("titles.introDim")}
                value={intro.dim}
                min={0}
                max={0.9}
                step={0.05}
                format={(v) => `${Math.round(v * 100)}%`}
                onInput={(dim) => setIntro({ dim })}
                onCommit={(dim) => setIntro({ dim }, true)}
              />
              <Slider
                id="intro-size"
                label={t("titles.size")}
                value={intro.size}
                min={48}
                max={200}
                step={2}
                format={(v) => `${v}px`}
                onInput={(size) => setIntro({ size })}
                onCommit={(size) => setIntro({ size }, true)}
              />
            </div>
            <TextStyleEditor
              id="intro-style"
              value={intro}
              inheritFont={project.subtitle.font}
              showAlign={false}
              onError={onError}
              onChange={(patch: Partial<TextStyle>, commit) => setIntro(patch, commit)}
            />
          </>
        )}
      </div>

      <div className="divider" />
      <details className="look-advanced" open={project.shots.some((s) => (s.title ?? "").trim())}>
        <summary>{t("titles.shotStyle")}</summary>
      <div className="field">
        <p className="muted small">{t("titles.shotHint")}</p>
        <Segmented
          label={t("titles.shotPosition")}
          value={shot.position}
          onChange={(position: OverlayPosition) => setShot({ position }, true)}
          options={(["top", "middle", "bottom"] as OverlayPosition[]).map((value) => ({
            value,
            label: t(`overlay.pos.${value}`),
          }))}
        />
        <div className="grid-2">
          <Slider
            id="shot-title-size"
            label={t("titles.size")}
            value={shot.size}
            min={24}
            max={120}
            step={2}
            format={(v) => `${v}px`}
            onInput={(size) => setShot({ size })}
            onCommit={(size) => setShot({ size }, true)}
          />
          <div className="span-2">
            <TextStyleEditor
              id="shot-title-style"
              value={shot}
              inheritFont={project.subtitle.font}
              onError={onError}
              onChange={(patch: Partial<TextStyle>, commit) => setShot(patch, commit)}
            />
          </div>
        </div>
      </div>
      </details>
    </Section>
  );
}
