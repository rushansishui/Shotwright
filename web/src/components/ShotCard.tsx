import { useEffect, useRef, useState } from "react";
import { assetUrl, estimateSeconds } from "../api";
import type { Project, Shot, ShotVisual, VisualMode } from "../types";
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
  { value: "ai", label: "AI 生成" },
  { value: "upload", label: "上传" },
  { value: "color", label: "纯色" },
];

export function ShotCard(props: {
  project: Project;
  shot: Shot;
  index: number;
  active: boolean;
  busy: boolean;
  aiAvailable: boolean;
  onFocus: () => void;
  onUpdate: (body: { text?: string; visual?: Partial<ShotVisual> }) => void;
  onCandidates: () => void;
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
        <Thumb project={project} assetId={visual.selected} color={visual.color} className={`shot-thumb a${project.canvas.aspect.replace(":", "-")}`} />
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
            value={visual.mode === "image" ? "upload" : visual.mode}
            options={MODES}
            disabled={visual.locked}
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
            <button type="button" className="btn" disabled={props.busy || visual.locked} onClick={props.onCandidates}>
              <Icon name="refresh" />
              {props.busy ? "检索中…" : visual.candidates.length ? "换一批" : "检索素材"}
            </button>
          </div>
        )}

        {visual.mode === "ai" && (
          <div className="inline-field">
            <label htmlFor={`prompt-${shot.id}`}>提示词</label>
            <input
              id={`prompt-${shot.id}`}
              className="input"
              value={prompt}
              placeholder="英文画面描述：主体、动作、镜头运动、光线"
              disabled={visual.locked}
              onChange={(e) => setPrompt(e.target.value)}
              onBlur={() => prompt !== visual.prompt && props.onUpdate({ visual: { prompt } })}
            />
            <button
              type="button"
              className="btn"
              disabled={props.busy || visual.locked || !props.aiAvailable}
              title={props.aiAvailable ? "" : "还没有配置 AI 视频服务"}
              onClick={props.onCandidates}
            >
              {props.busy ? "生成中…" : "生成"}
            </button>
          </div>
        )}

        {(visual.mode === "upload" || visual.mode === "image") && (
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
