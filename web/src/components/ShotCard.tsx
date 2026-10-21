import { useEffect, useRef, useState } from "react";
import { assetUrl, estimateSeconds } from "../api";
import { useI18n } from "../i18n";
import type { Asset, Project, ProviderInfo, Shot, VisualMode, VisualPatch } from "../types";
import { Icon, Segmented } from "./Controls";

export function Thumb(props: { project: Project; assetId: string | null; color: string; className?: string }) {
  const asset = props.assetId ? props.project.assets[props.assetId] : undefined;
  if (asset?.kind === "image") {
    return <img className={props.className} src={assetUrl(props.project.id, asset.id)} alt="" loading="lazy" />;
  }
  if (asset?.kind === "video") {
    return (
      <video
        className={props.className}
        src={`${assetUrl(props.project.id, asset.id)}#t=0.5`}
        muted
        playsInline
        preload="metadata"
      />
    );
  }
  return <div className={props.className} style={{ background: props.color }} />;
}

const MODES: VisualMode[] = ["stock", "ai", "image", "upload", "color"];

export function ShotCard(props: {
  project: Project;
  shot: Shot;
  index: number;
  active: boolean;
  busy: boolean;
  /** 已经等了多少秒（生成 AI 画面可能要几分钟）。 */
  busySeconds?: number;
  aiVideo: ProviderInfo | null;
  aiImage: ProviderInfo | null;
  /** 服务 id → 画面来源，用来按当前来源筛选候选素材。 */
  sourceModes: Record<string, VisualMode>;
  onFocus: () => void;
  onUpdate: (body: { text?: string; visual?: VisualPatch }) => void;
  onCandidates: (confirmCharge?: boolean, prompt?: string) => void;
  onUpload: (file: File) => void;
  onMove: (delta: number) => void;
  onInsert: () => void;
  onDelete: () => void;
}) {
  const { project, shot, index } = props;
  const visual = shot.visual;
  const [text, setText] = useState(shot.text);
  const [terms, setTerms] = useState(visual.terms.join(", "));
  const [prompt, setPrompt] = useState(visual.prompt);
  const fileInput = useRef<HTMLInputElement>(null);
  const { t } = useI18n();

  useEffect(() => setText(shot.text), [shot.text]);
  useEffect(() => setTerms(visual.terms.join(", ")), [visual.terms]);
  useEffect(() => setPrompt(visual.prompt), [visual.prompt]);

  const generator = visual.mode === "ai" ? props.aiVideo : visual.mode === "image" ? props.aiImage : null;
  const modeOf = (asset: Asset | undefined): VisualMode | undefined => {
    if (!asset) return undefined;
    const mode = props.sourceModes[asset.source];
    // 视频服务只产出视频、图片服务只产出图片，同名服务用素材类型区分。
    if (mode === "ai" && asset.kind !== "video") return undefined;
    if (mode === "image" && asset.kind !== "image") return undefined;
    return mode;
  };
  const shown = visual.mode === "color" ? [] : visual.candidates.filter((id) => modeOf(project.assets[id]) === visual.mode);
  /** 属于其他画面来源的候选素材：切过去就能看到，这里给出数量提示。 */
  const elsewhere: { mode: VisualMode; count: number }[] = ["stock", "ai", "image", "upload"]
    .filter((mode) => mode !== visual.mode)
    .map((mode) => ({
      mode: mode as VisualMode,
      count: visual.candidates.filter((id) => modeOf(project.assets[id]) === mode).length,
    }))
    .filter((entry) => entry.count > 0);
  // AI 视频的首帧可以从这个镜头的任意图片里选（AI 图片或上传的图片）。
  const frameChoices = visual.mode === "ai" ? visual.candidates.filter((id) => project.assets[id]?.kind === "image") : [];
  const firstFrame = visual.mode === "ai" && !!visual.first_frame && !!project.assets[visual.first_frame];
  const emptyHint: Partial<Record<VisualMode, string>> = {
    stock: t("shot.empty.stock"),
    ai: t("shot.empty.ai"),
    image: t("shot.empty.image"),
    upload: t("shot.empty.upload"),
  };
  const generate = async () => {
    if (!generator) return;
    const what =
      visual.mode === "ai"
        ? t(firstFrame ? "shot.confirm.videoFirstFrame" : "shot.confirm.video")
        : t("shot.confirm.image");
    const cost = generator.paid ? `\n${t("shot.confirm.cost", { name: generator.name })}` : "";
    if (window.confirm(t("shot.confirm.question", { n: index + 1, what }) + cost)) {
      props.onCandidates(true, prompt !== visual.prompt ? prompt : undefined);
    }
  };

  const generating = visual.mode === "ai" || visual.mode === "image";
  const busyVerb = t(generating ? "shot.busy.generating" : visual.mode === "stock" ? "shot.busy.searching" : "shot.busy.working");
  const busyLabel = props.busySeconds ? `${busyVerb} ${props.busySeconds}s` : busyVerb;

  const seconds = estimateSeconds({ ...shot, text }, (shot.voice ?? project.voice).rate);
  const isLast = index === project.shots.length - 1;
  const commitTerms = () => {
    const next = terms
      .split(/[,，]/)
      .map((t) => t.trim())
      .filter(Boolean);
    if (next.join("|") !== visual.terms.join("|")) props.onUpdate({ visual: { terms: next } });
  };

  return (
    <article className={`shot ${props.active ? "active" : ""}`} onFocusCapture={props.onFocus} onClick={props.onFocus}>
      <div className="shot-side">
        <span className="shot-index">{index + 1}</span>
        <div className="shot-thumb-wrap">
          <Thumb project={project} assetId={visual.mode === "color" ? null : visual.selected} color={visual.color} className={`shot-thumb a${project.canvas.aspect.replace(":", "-")}`} />
          {props.busy && (
            <div className="shot-busy" role="status" aria-live="polite">
              <span className="spinner" aria-hidden="true" />
              <span>{busyLabel}</span>
            </div>
          )}
        </div>
        <span className="muted small">{t("shot.duration", { n: seconds })}</span>
      </div>

      <div className="shot-main">
        <label className="sr-only" htmlFor={`text-${shot.id}`}>
          {t("shot.textLabel", { n: index + 1 })}
        </label>
        <textarea
          id={`text-${shot.id}`}
          className="input shot-text"
          rows={2}
          value={text}
          placeholder={t("shot.textPlaceholder")}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => text !== shot.text && props.onUpdate({ text })}
        />

        <div className="shot-visual-row">
          <Segmented
            label={t("shot.source")}
            value={visual.mode}
            options={MODES.map((value) => {
              const m = { value, label: t(`shot.mode.${value}`) };
              const provider = value === "ai" ? props.aiVideo : value === "image" ? props.aiImage : undefined;
              if (provider === null && visual.mode !== value) {
                return { ...m, disabled: true, title: t("shot.notConfigured", { name: m.label }) };
              }
              return provider?.paid ? { ...m, title: `${provider.name}${t("common.paidSuffix")}` } : m;
            })}
            disabled={visual.locked || props.busy}
            onChange={(mode) => props.onUpdate({ visual: { mode } })}
          />
          <button
            type="button"
            className={`btn icon ${visual.locked ? "on" : ""}`}
            aria-pressed={visual.locked}
            aria-label={visual.locked ? t("shot.unlock") : t("shot.lock")}
            title={visual.locked ? t("shot.lockedTip") : t("shot.lock")}
            onClick={() => props.onUpdate({ visual: { locked: !visual.locked } })}
          >
            <Icon name={visual.locked ? "lock" : "unlock"} />
          </button>
        </div>

        {visual.mode === "stock" && (
          <div className="inline-field">
            <label htmlFor={`terms-${shot.id}`}>{t("shot.terms")}</label>
            <input
              id={`terms-${shot.id}`}
              className="input"
              value={terms}
              placeholder={t("shot.termsPlaceholder")}
              disabled={visual.locked}
              onChange={(e) => setTerms(e.target.value)}
              onBlur={commitTerms}
              onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            />
            <button type="button" className="btn" disabled={props.busy || visual.locked} onClick={() => props.onCandidates()}>
              <Icon name="refresh" />
              {props.busy ? `${busyLabel}…` : shown.length ? t("shot.more") : t("shot.search")}
            </button>
          </div>
        )}

        {(visual.mode === "ai" || visual.mode === "image") && (
          <div className="inline-field">
            <label htmlFor={`prompt-${shot.id}`}>{t("shot.prompt")}</label>
            <input
              id={`prompt-${shot.id}`}
              className="input"
              value={prompt}
              placeholder={
                visual.mode === "ai" ? t("shot.promptVideo") : t("shot.promptImage")
              }
              disabled={visual.locked}
              onChange={(e) => setPrompt(e.target.value)}
              onBlur={() => prompt !== visual.prompt && props.onUpdate({ visual: { prompt } })}
            />
            <button
              type="button"
              className="btn"
              disabled={props.busy || visual.locked || !generator || !prompt.trim()}
              title={generator ? `${generator.name}${generator.paid ? t("common.paidSuffix") : ""}` : t("shot.noGenerator")}
              onClick={generate}
            >
              {props.busy ? (
                <>
                  <span className="spinner" aria-hidden="true" />
                  {busyLabel}
                </>
              ) : generator?.paid ? (
                t("shot.generatePaid")
              ) : (
                t("shot.generate")
              )}
            </button>
          </div>
        )}
        {frameChoices.length > 0 && (
          <div className="first-frame">
            <span className="label">{t("shot.firstFrame")}</span>
            <div className="candidates small" role="listbox" aria-label={t("shot.firstFrameLabel")}>
              <button
                type="button"
                role="option"
                aria-selected={!firstFrame}
                className={`candidate none ${!firstFrame ? "on" : ""}`}
                disabled={visual.locked || props.busy}
                onClick={() => props.onUpdate({ visual: { clear_first_frame: true } })}
              >
                {t("shot.firstFrameNone")}
              </button>
              {frameChoices.map((id) => (
                <button
                  type="button"
                  key={id}
                  role="option"
                  aria-selected={visual.first_frame === id}
                  className={`candidate ${visual.first_frame === id ? "on" : ""}`}
                  disabled={visual.locked || props.busy}
                  onClick={() => props.onUpdate({ visual: { first_frame: id } })}
                  title={t("shot.firstFrameTip")}
                >
                  <Thumb project={project} assetId={id} color={visual.color} className="candidate-media" />
                </button>
              ))}
            </div>
            <span className="muted small">{firstFrame ? t("shot.firstFrameOn") : t("shot.firstFrameOff")}</span>
          </div>
        )}

        {visual.mode === "upload" && (
          <div className="inline-field">
            <span className="label">{t("shot.material")}</span>
            <input
              ref={fileInput}
              type="file"
              accept="video/*,image/*"
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) props.onUpload(file);
                e.target.value = "";
              }}
            />
            <button type="button" className="btn" disabled={props.busy || visual.locked} onClick={() => fileInput.current?.click()}>
              <Icon name="upload" />
              {props.busy ? t("shot.uploading") : t("shot.upload")}
            </button>
          </div>
        )}

        {visual.mode === "color" && (
          <div className="inline-field">
            <label htmlFor={`color-${shot.id}`}>{t("shot.background")}</label>
            <input
              id={`color-${shot.id}`}
              type="color"
              value={visual.color.toLowerCase()}
              disabled={visual.locked}
              onChange={(e) => props.onUpdate({ visual: { color: e.target.value.toUpperCase() } })}
            />
          </div>
        )}

        {visual.mode !== "color" && shown.length === 0 && !props.busy && (
          <p className="muted small">{emptyHint[visual.mode]}</p>
        )}
        {shown.length > 0 && (
          <div className="candidates" role="listbox" aria-label={t("shot.candidates")}>
            {shown.map((id) => (
              <button
                type="button"
                key={id}
                role="option"
                aria-selected={visual.selected === id}
                className={`candidate ${visual.selected === id ? "on" : ""}`}
                disabled={visual.locked}
                onClick={() => props.onUpdate({ visual: { selected: id } })}
                title={project.assets[id]?.meta.author ? t("shot.author", { name: project.assets[id].meta.author }) : ""}
              >
                <Thumb project={project} assetId={id} color={visual.color} className="candidate-media" />
              </button>
            ))}
          </div>
        )}
        {elsewhere.length > 0 && (
          <p className="muted small elsewhere">
            {t("shot.elsewhere")}{" "}
            {elsewhere.map((entry, position) => (
              <span key={entry.mode}>
                {position > 0 && "、"}
                <button
                  type="button"
                  className="linkish"
                  disabled={visual.locked}
                  onClick={() => props.onUpdate({ visual: { mode: entry.mode } })}
                >
                  {t(`shot.mode.${entry.mode}`)} {t("shot.elsewhereCount", { n: entry.count })}
                </button>
              </span>
            ))}
            {t("shot.elsewhereHint")}
          </p>
        )}
        {shot.error && <p className="hint error">{shot.error}</p>}
      </div>

      <div className="shot-actions">
        <button type="button" className="btn icon ghost" aria-label={t("shot.moveUp")} disabled={index === 0} onClick={() => props.onMove(-1)}>
          <Icon name="up" />
        </button>
        <button type="button" className="btn icon ghost" aria-label={t("shot.moveDown")} disabled={isLast} onClick={() => props.onMove(1)}>
          <Icon name="down" />
        </button>
        <button type="button" className="btn icon ghost" aria-label={t("shot.insert")} onClick={props.onInsert}>
          <Icon name="plus" />
        </button>
        <button type="button" className="btn icon ghost danger" aria-label={t("shot.delete")} onClick={props.onDelete}>
          <Icon name="trash" />
        </button>
      </div>
    </article>
  );
}
