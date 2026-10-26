import { useEffect, useState } from "react";
import { api } from "../../api";
import { useI18n } from "../../i18n";
import type { OverlayPosition, Project, TitleSettings } from "../../types";
import { Section, Segmented, Slider } from "../Controls";

const DEFAULT_TITLES: TitleSettings = {
  headline: {
    enabled: false,
    text: "",
    size: 76,
    color: "#FFFFFF",
    outline_color: "#000000",
    background: "#000000",
    background_opacity: 0,
    margin: 160,
  },
  intro: { enabled: false, text: "", subtitle: "", duration: 2.5, size: 110, color: "#FFFFFF", dim: 0.55 },
  shot: { size: 56, color: "#FFE45C", outline_color: "#000000", position: "top", margin: 330 },
  candidates: [],
};

interface TitleSectionProps {
  project: Project;
  step: number;
  canSuggest: boolean;
  run: (action: () => Promise<Project>, shotId?: string) => Promise<Project | null>;
}

/** 标题：画面顶部常驻大标题、片头标题卡、镜头小标题样式，以及 AI 生成标题。 */
export function TitleSection({ project, step, canSuggest, run }: TitleSectionProps) {
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
            <Slider
              id="headline-bg"
              label={t("titles.background")}
              value={headline.background_opacity}
              min={0}
              max={1}
              step={0.05}
              format={(v) => (v === 0 ? t("titles.noBackground") : `${Math.round(v * 100)}%`)}
              onInput={(background_opacity) => setHeadline({ background_opacity })}
              onCommit={(background_opacity) => setHeadline({ background_opacity }, true)}
            />
            <label className="inline-field">
              <span className="label">{t("titles.color")}</span>
              <input type="color" value={headline.color.toLowerCase()} onChange={(e) => setHeadline({ color: e.target.value.toUpperCase() }, true)} />
              <span className="label">{t("titles.backgroundColor")}</span>
              <input
                type="color"
                value={headline.background.toLowerCase()}
                onChange={(e) => setHeadline({ background: e.target.value.toUpperCase() }, true)}
              />
            </label>
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
            </div>
          </>
        )}
      </div>

      <div className="divider" />
      <div className="field">
        <span className="label">{t("titles.shotStyle")}</span>
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
          <label className="inline-field">
            <span className="label">{t("titles.color")}</span>
            <input type="color" value={shot.color.toLowerCase()} onChange={(e) => setShot({ color: e.target.value.toUpperCase() }, true)} />
          </label>
        </div>
      </div>
    </Section>
  );
}
