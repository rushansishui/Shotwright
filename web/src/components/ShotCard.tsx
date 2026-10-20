import { useEffect, useRef, useState } from "react";
import { assetUrl, estimateSeconds } from "../api";
import type { Project, ProviderInfo, Shot, ShotVisual, VisualMode } from "../types";
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

const MODES: { value: VisualMode; label: string }[] = [
  { value: "stock", label: "素材检索" },
  { value: "ai", label: "AI 视频" },
  { value: "image", label: "AI 图片" },
  { value: "upload", label: "上传" },
  { value: "color", label: "纯色" },
];

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
  onFocus: () => void;
  onUpdate: (body: { text?: string; visual?: Partial<ShotVisual> }) => void;
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

  useEffect(() => setText(shot.text), [shot.text]);
  useEffect(() => setTerms(visual.terms.join(", ")), [visual.terms]);
  useEffect(() => setPrompt(visual.prompt), [visual.prompt]);

  const generator = visual.mode === "ai" ? props.aiVideo : visual.mode === "image" ? props.aiImage : null;
  const selectedAsset = visual.selected ? project.assets[visual.selected] : undefined;
  const firstFrame = visual.mode === "ai" && selectedAsset?.kind === "image";
  const generate = async () => {
    if (!generator) return;
    const what = visual.mode === "ai" ? (firstFrame ? "以选中的图片为首帧生成一段视频" : "生成一段视频") : "生成一张图片";
    const cost = generator.paid ? `\n${generator.name} 按次收费，提交后即使失败也可能扣费。` : "";
    if (window.confirm(`为镜头 ${index + 1} ${what}？${cost}`)) props.onCandidates(true, prompt !== visual.prompt ? prompt : undefined);
  };

  const generating = visual.mode === "ai" || visual.mode === "image";
  const busyVerb = generating ? "生成中" : visual.mode === "stock" ? "检索中" : "处理中";
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
          <Thumb project={project} assetId={visual.selected} color={visual.color} className={`shot-thumb a${project.canvas.aspect.replace(":", "-")}`} />
          {props.busy && (
            <div className="shot-busy" role="status" aria-live="polite">
              <span className="spinner" aria-hidden="true" />
              <span>{busyLabel}</span>
            </div>
          )}
        </div>
        <span className="muted small">约 {seconds} 秒</span>
      </div>

      <div className="shot-main">
        <label className="sr-only" htmlFor={`text-${shot.id}`}>
          镜头 {index + 1} 文案
        </label>
        <textarea
          id={`text-${shot.id}`}
          className="input shot-text"
          rows={2}
          value={text}
          placeholder="这个镜头的口播文案"
          onChange={(e) => setText(e.target.value)}
          onBlur={() => text !== shot.text && props.onUpdate({ text })}
        />

        <div className="shot-visual-row">
          <Segmented
            label="画面来源"
            value={visual.mode}
            options={MODES.map((m) => {
              const provider = m.value === "ai" ? props.aiVideo : m.value === "image" ? props.aiImage : undefined;
              if (provider === null && visual.mode !== m.value) {
                return { ...m, disabled: true, title: `还没有配置${m.label}服务，见 config.toml` };
              }
              return provider?.paid ? { ...m, title: `${provider.name}（收费）` } : m;
            })}
            disabled={visual.locked || props.busy}
            onChange={(mode) => props.onUpdate({ visual: { mode } })}
          />
          <button
            type="button"
            className={`btn icon ${visual.locked ? "on" : ""}`}
            aria-pressed={visual.locked}
            aria-label={visual.locked ? "解锁画面" : "锁定画面"}
            title={visual.locked ? "已锁定：重新生成时不会改动这个镜头的画面" : "锁定画面"}
            onClick={() => props.onUpdate({ visual: { locked: !visual.locked } })}
          >
            <Icon name={visual.locked ? "lock" : "unlock"} />
          </button>
        </div>

        {visual.mode === "stock" && (
          <div className="inline-field">
            <label htmlFor={`terms-${shot.id}`}>检索词</label>
            <input
              id={`terms-${shot.id}`}
              className="input"
              value={terms}
              placeholder="英文，逗号分隔，例如 city night, traffic"
              disabled={visual.locked}
              onChange={(e) => setTerms(e.target.value)}
              onBlur={commitTerms}
              onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            />
            <button type="button" className="btn" disabled={props.busy || visual.locked} onClick={() => props.onCandidates()}>
              <Icon name="refresh" />
              {props.busy ? `${busyLabel}…` : visual.candidates.length ? "换一批" : "检索素材"}
            </button>
          </div>
        )}

        {(visual.mode === "ai" || visual.mode === "image") && (
          <div className="inline-field">
            <label htmlFor={`prompt-${shot.id}`}>提示词</label>
            <input
              id={`prompt-${shot.id}`}
              className="input"
              value={prompt}
              placeholder={
                visual.mode === "ai" ? "画面描述：主体、动作、镜头运动、光线" : "画面描述：主体、场景、风格、光线"
              }
              disabled={visual.locked}
              onChange={(e) => setPrompt(e.target.value)}
              onBlur={() => prompt !== visual.prompt && props.onUpdate({ visual: { prompt } })}
            />
            <button
              type="button"
              className="btn"
              disabled={props.busy || visual.locked || !generator || !prompt.trim()}
              title={generator ? `${generator.name}${generator.paid ? "（收费）" : ""}` : "还没有配置生成服务"}
              onClick={generate}
            >
              {props.busy ? (
                <>
                  <span className="spinner" aria-hidden="true" />
                  {busyLabel}
                </>
              ) : generator?.paid ? (
                "生成（收费）"
              ) : (
                "生成"
              )}
            </button>
          </div>
        )}
        {firstFrame && !visual.locked && (
          <p className="muted small">将以选中的图片作为首帧；想纯文字生成，先选中一段视频或清空选择。</p>
        )}

        {visual.mode === "upload" && (
          <div className="inline-field">
            <span className="label">素材</span>
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
              {props.busy ? "上传中…" : "上传图片或视频"}
            </button>
          </div>
        )}

        {visual.mode === "color" && (
          <div className="inline-field">
            <label htmlFor={`color-${shot.id}`}>背景色</label>
            <input
              id={`color-${shot.id}`}
              type="color"
              value={visual.color.toLowerCase()}
              disabled={visual.locked}
              onChange={(e) => props.onUpdate({ visual: { color: e.target.value.toUpperCase() } })}
            />
          </div>
        )}

        {visual.mode !== "color" && visual.candidates.length > 0 && (
          <div className="candidates" role="listbox" aria-label="候选素材">
            {visual.candidates.map((id) => (
              <button
                type="button"
                key={id}
                role="option"
                aria-selected={visual.selected === id}
                className={`candidate ${visual.selected === id ? "on" : ""}`}
                disabled={visual.locked}
                onClick={() => props.onUpdate({ visual: { selected: id } })}
                title={project.assets[id]?.meta.author ? `作者：${project.assets[id].meta.author}` : ""}
              >
                <Thumb project={project} assetId={id} color={visual.color} className="candidate-media" />
              </button>
            ))}
          </div>
        )}
        {shot.error && <p className="hint error">{shot.error}</p>}
      </div>

      <div className="shot-actions">
        <button type="button" className="btn icon ghost" aria-label="上移" disabled={index === 0} onClick={() => props.onMove(-1)}>
          <Icon name="up" />
        </button>
        <button type="button" className="btn icon ghost" aria-label="下移" disabled={isLast} onClick={() => props.onMove(1)}>
          <Icon name="down" />
        </button>
        <button type="button" className="btn icon ghost" aria-label="在后面插入镜头" onClick={props.onInsert}>
          <Icon name="plus" />
        </button>
        <button type="button" className="btn icon ghost danger" aria-label="删除镜头" onClick={props.onDelete}>
          <Icon name="trash" />
        </button>
      </div>
    </article>
  );
}
