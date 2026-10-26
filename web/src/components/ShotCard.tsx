import { useEffect, useRef, useState } from "react";
import { assetUrl, estimateSeconds, type GenerateOptions } from "../api";
import { providerName, useI18n } from "../i18n";
import type { MessageKey } from "../messages";
import type { Asset, CameraMove, Project, ProviderInfo, Shot, ShotRole, Take, VisualMode, VisualPatch } from "../types";
import { Icon, Segmented } from "./Controls";
import { ShotEdit } from "./ShotEdit";

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

/** 候选缩略图上的版本号、检查得分和问题提示。 */
function TakeBadge({ takes, assetId, asset }: { takes: Take[]; assetId: string; asset?: Asset }) {
  const { t } = useI18n();
  const index = takes.findIndex((item) => item.asset_id === assetId);
  if (index < 0) {
    // 素材库候选没有版本记录，但可能有看图排序的得分和问题。
    const issue = asset?.meta.issue;
    const score = asset?.meta.score;
    if (!issue && !score) return null;
    return (
      <span className={`take-badge ${issue ? "warn" : ""}`} title={issue ?? ""}>
        {score ? Math.round(Number(score) * 100) : ""}
        {issue ? " !" : ""}
      </span>
    );
  }
  const take = takes[index];
  const score = take.score === null ? "" : ` · ${Math.round(take.score * 100)}`;
  return (
    <span className={`take-badge ${take.issue ? "warn" : ""}`} title={take.issue || t(`take.kind.${take.kind}` as MessageKey)}>
      {t("take.version", { n: index + 1 })}
      {score}
      {take.issue ? " !" : ""}
    </span>
  );
}

const MODES: VisualMode[] = ["stock", "ai", "image", "upload", "color"];
const ROLES: ShotRole[] = ["hook", "product", "demo", "scene", "people", "explain", "talking", "cta"];
const CAMERAS: CameraMove[] = ["auto", "static", "push_in", "pull_out", "pan", "truck", "orbit", "crane", "handheld"];

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
  onUpdate: (body: { text?: string; title?: string; role?: ShotRole; visual?: VisualPatch }) => void;
  /** ``patch`` 是还没失焦保存的提示词，先存好再生成。 */
  onCandidates: (confirmCharge?: boolean, patch?: VisualPatch, editSelected?: boolean, options?: GenerateOptions) => void;
  /** 配置了大模型时可以让 AI 改写这个镜头的画面。 */
  canRewrite?: boolean;
  onRewrite?: (instruction: string) => void;
  onUpload: (file: File) => void;
  onMove: (delta: number) => void;
  onInsert: () => void;
  onSplit: (at: number) => void;
  onMerge: () => void;
  onDelete: () => void;
}) {
  const { project, shot, index } = props;
  const visual = shot.visual;
  const [text, setText] = useState(shot.text);
  const [terms, setTerms] = useState(visual.terms.join(", "));
  const [prompt, setPrompt] = useState(visual.prompt);
  const [link, setLink] = useState(visual.link ?? "");
  const [shotTitle, setShotTitle] = useState(shot.title ?? "");
  useEffect(() => setShotTitle(shot.title ?? ""), [shot.title]);
  const [still, setStill] = useState(visual.image_prompt ?? "");
  const [count, setCount] = useState(1);
  const [instruction, setInstruction] = useState("");
  const [quality, setQuality] = useState<"draft" | "final">("final");
  const fileInput = useRef<HTMLInputElement>(null);
  const { t, lang } = useI18n();

  useEffect(() => setText(shot.text), [shot.text]);
  useEffect(() => setTerms(visual.terms.join(", ")), [visual.terms]);
  useEffect(() => setPrompt(visual.prompt), [visual.prompt]);
  useEffect(() => setLink(visual.link ?? ""), [visual.link]);
  useEffect(() => setStill(visual.image_prompt ?? ""), [visual.image_prompt]);
  /** AI 图片用单独的静态画面描述，为空时沿用视频的画面描述。 */
  const isImage = visual.mode === "image";
  const promptValue = isImage ? still : prompt;
  const setPromptValue = isImage ? setStill : setPrompt;
  const unsavedPrompt = (): VisualPatch | undefined =>
    isImage
      ? still !== (visual.image_prompt ?? "")
        ? { image_prompt: still }
        : undefined
      : prompt !== visual.prompt
        ? { prompt }
        : undefined;

  /** 镜头要播放的素材：选中的在前，多选的按顺序跟在后面。 */
  const chosen = visual.selected ? [visual.selected, ...(visual.sequence ?? []).filter((id) => id !== visual.selected)] : [];
  const toggleChosen = (id: string) => {
    if (!visual.selected) {
      props.onUpdate({ visual: { selected: id, sequence: [] } });
      return;
    }
    const rest = chosen.filter((item) => item !== id);
    if (rest.length === chosen.length) {
      props.onUpdate({ visual: { sequence: [...chosen.slice(1), id] } });
    } else if (id === visual.selected) {
      // 取消第一段：后面一段补上来成为选中的素材。
      props.onUpdate({ visual: { selected: rest[0], sequence: rest.slice(1) } });
    } else {
      props.onUpdate({ visual: { sequence: rest.slice(1) } });
    }
  };
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
    const cost = generator.paid ? `\n${t("shot.confirm.cost", { name: providerName(generator, lang) })}` : "";
    const times = visual.mode === "ai" && count > 1 ? `\n${t("shot.confirm.count", { n: count })}` : "";
    if (window.confirm(t("shot.confirm.question", { n: index + 1, what }) + times + cost)) {
      props.onCandidates(true, unsavedPrompt(), false, visual.mode === "ai" ? { count, quality } : {});
    }
  };

  /** 先出关键帧：用 AI 图片服务出几张静态画面，选一张作首帧再生成视频。 */
  const keyframes = () => {
    if (!props.aiImage) return;
    const cost = props.aiImage.paid ? `\n${t("shot.confirm.cost", { name: providerName(props.aiImage, lang) })}` : "";
    if (window.confirm(t("shot.confirm.keyframes", { n: index + 1, count: 3 }) + cost)) {
      props.onCandidates(true, unsavedPrompt(), false, { keyframes: 3 });
    }
  };

  const generating = visual.mode === "ai" || visual.mode === "image";
  const busyVerb = t(generating ? "shot.busy.generating" : visual.mode === "stock" ? "shot.busy.searching" : "shot.busy.working");
  const busyLabel = props.busySeconds ? `${busyVerb} ${props.busySeconds}s` : busyVerb;

  /**
   * 改画面：当前已选中的素材是视频、且生成服务支持视频转视频时才能用。
   * 结果作为新的候选素材，原素材保留。
   */
  const selectedAsset = visual.selected ? project.assets[visual.selected] : undefined;
  const editable =
    visual.mode !== "color" && (selectedAsset?.kind === "video" || selectedAsset?.kind === "image");
  const canModify =
    !!props.aiVideo &&
    props.aiVideo.video_to_video &&
    selectedAsset?.kind === "video" &&
    !visual.locked &&
    !props.busy;
  const modify = () => {
    if (!prompt.trim()) return;
    const name = props.aiVideo ? providerName(props.aiVideo, lang) : "";
    if (window.confirm(t("shot.confirm.modify", { n: index + 1, name }))) {
      props.onCandidates(true, prompt !== visual.prompt ? { prompt } : undefined, true);
    }
  };

  const seconds = estimateSeconds({ ...shot, text }, (shot.voice ?? project.voice).rate);
  // 生成服务的时长上限；超过时提示用户，避免生成被服务端截断。
  const maxDuration = generator?.max_duration ?? null;
  const exceedsMax = !!maxDuration && seconds > maxDuration;
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
        <span
          className={`muted small ${exceedsMax ? "warn-text" : ""}`}
          title={exceedsMax ? t("shot.overMaxDuration", { max: maxDuration }) : undefined}
        >
          {t("shot.duration", { n: seconds })}
          {exceedsMax ? ` · ${t("shot.overMaxDurationShort", { max: maxDuration })}` : ""}
        </span>
        <select
          className="role-select"
          aria-label={t("shot.role")}
          title={t("shot.roleHint")}
          value={shot.role ?? ""}
          onChange={(e) => e.target.value && props.onUpdate({ role: e.target.value as ShotRole })}
        >
          <option value="" disabled>
            {t("shot.role")}
          </option>
          {ROLES.map((role) => (
            <option key={role} value={role}>
              {t(`shot.role.${role}` as MessageKey)}
            </option>
          ))}
        </select>
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
        <input
          className="input shot-title"
          aria-label={t("shot.titleLabel")}
          placeholder={t("shot.titlePlaceholder")}
          value={shotTitle}
          maxLength={60}
          onChange={(e) => setShotTitle(e.target.value)}
          onBlur={() => shotTitle !== (shot.title ?? "") && props.onUpdate({ title: shotTitle })}
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
              return provider?.paid ? { ...m, title: `${providerName(provider, lang)}${t("common.paidSuffix")}` } : m;
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
            <textarea
              id={`prompt-${shot.id}`}
              className="input prompt-area"
              rows={3}
              value={promptValue}
              placeholder={visual.mode === "ai" ? t("shot.promptVideo") : visual.prompt || t("shot.promptImage")}
              disabled={visual.locked}
              onChange={(e) => setPromptValue(e.target.value)}
              onBlur={() => {
                const patch = unsavedPrompt();
                if (patch) props.onUpdate({ visual: patch });
              }}
            />
            <button
              type="button"
              className="btn"
              disabled={props.busy || visual.locked || !generator || !(promptValue.trim() || (isImage && visual.prompt.trim()))}
              title={generator ? `${providerName(generator, lang)}${generator.paid ? t("common.paidSuffix") : ""}` : t("shot.noGenerator")}
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
        {props.canRewrite && (visual.mode === "ai" || visual.mode === "image") && (
          <div className="inline-field">
            <label htmlFor={`rewrite-${shot.id}`}>{t("shot.rewrite")}</label>
            <input
              id={`rewrite-${shot.id}`}
              className="input"
              value={instruction}
              placeholder={t("shot.rewritePlaceholder")}
              disabled={visual.locked || props.busy}
              onChange={(e) => setInstruction(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && instruction.trim()) {
                  props.onRewrite?.(instruction.trim());
                  setInstruction("");
                }
              }}
            />
            <button
              type="button"
              className="btn small"
              disabled={visual.locked || props.busy || !instruction.trim()}
              title={t("shot.rewriteHint")}
              onClick={() => {
                props.onRewrite?.(instruction.trim());
                setInstruction("");
              }}
            >
              {t("shot.rewriteRun")}
            </button>
          </div>
        )}
        {visual.mode === "ai" && (
          <div className="inline-field gen-options">
            <span className="label">{t("shot.genOptions")}</span>
            <select
              className="input compact"
              aria-label={t("shot.count")}
              value={count}
              disabled={visual.locked}
              onChange={(e) => setCount(Number(e.target.value))}
            >
              {[1, 2, 3].map((n) => (
                <option key={n} value={n}>
                  {t("shot.countValue", { n })}
                </option>
              ))}
            </select>
            <select
              className="input compact"
              aria-label={t("shot.genQuality")}
              value={quality}
              disabled={visual.locked}
              onChange={(e) => setQuality(e.target.value as "draft" | "final")}
            >
              <option value="final">{t("shot.genQuality.final")}</option>
              <option value="draft">{t("shot.genQuality.draft")}</option>
            </select>
            <button
              type="button"
              className="btn small"
              disabled={props.busy || visual.locked || !props.aiImage || !(promptValue.trim() || still.trim())}
              title={props.aiImage ? t("shot.keyframesHint") : t("shot.noGenerator")}
              onClick={keyframes}
            >
              {t("shot.keyframes")}
            </button>
          </div>
        )}
        {visual.mode === "ai" && (
          <div className="inline-field shot-link">
            <label htmlFor={`camera-${shot.id}`}>{t("shot.camera")}</label>
            <select
              id={`camera-${shot.id}`}
              className="input camera"
              value={visual.camera ?? "auto"}
              disabled={visual.locked}
              onChange={(e) => props.onUpdate({ visual: { camera: e.target.value as CameraMove } })}
            >
              {CAMERAS.map((camera) => (
                <option key={camera} value={camera}>
                  {t(`shot.camera.${camera}` as MessageKey)}
                </option>
              ))}
            </select>
            <label htmlFor={`link-${shot.id}`} className="sr-only">
              {t("shot.link")}
            </label>
            <textarea
              id={`link-${shot.id}`}
              className="input"
              rows={1}
              value={link}
              placeholder={`${t("shot.link")}：${t("shot.linkPlaceholder")}`}
              disabled={visual.locked}
              onChange={(e) => setLink(e.target.value)}
              onBlur={() => link !== (visual.link ?? "") && props.onUpdate({ visual: { link } })}
            />
          </div>
        )}
        {canModify && (
          <div className="inline-field">
            <label htmlFor={`modify-${shot.id}`}>{t("shot.modify")}</label>
            <input
              id={`modify-${shot.id}`}
              className="input"
              value={prompt}
              placeholder={t("shot.modifyPlaceholder")}
              onChange={(e) => setPrompt(e.target.value)}
              onBlur={() => prompt !== visual.prompt && props.onUpdate({ visual: { prompt } })}
            />
            <button
              type="button"
              className="btn"
              disabled={!prompt.trim()}
              title={t("shot.modifyHint")}
              onClick={modify}
            >
              {t("shot.modifyRun")}
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
          <div className="candidates" role="listbox" aria-label={t("shot.candidates")} aria-multiselectable="true">
            {shown.map((id) => {
              const order = chosen.indexOf(id);
              return (
                <div className="candidate-wrap" key={id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={order >= 0}
                    className={`candidate ${visual.selected === id ? "on" : order > 0 ? "also" : ""}`}
                    disabled={visual.locked}
                    onClick={() => props.onUpdate({ visual: { selected: id } })}
                    title={project.assets[id]?.meta.author ? t("shot.author", { name: project.assets[id].meta.author }) : ""}
                  >
                    <Thumb project={project} assetId={id} color={visual.color} className="candidate-media" />
                    <TakeBadge takes={visual.takes ?? []} assetId={id} asset={project.assets[id]} />
                  </button>
                  <button
                    type="button"
                    className={`pick ${order >= 0 ? "on" : ""}`}
                    disabled={visual.locked || (order === 0 && chosen.length === 1)}
                    aria-label={order >= 0 ? t("shot.multiRemove") : t("shot.multiAdd")}
                    title={order >= 0 ? t("shot.multiRemove") : t("shot.multiAdd")}
                    onClick={() => toggleChosen(id)}
                  >
                    {order >= 0 ? order + 1 : "+"}
                  </button>
                </div>
              );
            })}
          </div>
        )}
        {shown.length > 1 && <p className="muted small">{chosen.length > 1 ? t("shot.multiOn", { n: chosen.length }) : t("shot.multiHint")}</p>}
        {(() => {
          const take = visual.takes?.find((item) => item.asset_id === visual.selected);
          return take?.issue ? (
            <p className="hint warn small take-issue">{t("take.issue", { issue: take.issue })}</p>
          ) : null;
        })()}
        {elsewhere.length > 0 && (
          <p className="muted small elsewhere">
            {t("shot.elsewhere")}{" "}
            {elsewhere.map((entry, position) => (
              <span key={entry.mode}>
                {position > 0 && t("shot.elsewhereSep")}
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
        {editable && selectedAsset && (
          <ShotEdit
            project={project}
            shot={shot}
            asset={selectedAsset}
            disabled={visual.locked || props.busy}
            onUpdate={(patch) => props.onUpdate({ visual: patch })}
            onSplit={props.onSplit}
          />
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
        <button
          type="button"
          className="btn icon ghost"
          aria-label={t("shot.merge")}
          title={t("shot.mergeHint")}
          disabled={isLast || props.busy}
          onClick={props.onMerge}
        >
          <Icon name="merge" />
        </button>
        <button type="button" className="btn icon ghost danger" aria-label={t("shot.delete")} onClick={props.onDelete}>
          <Icon name="trash" />
        </button>
      </div>
    </article>
  );
}
