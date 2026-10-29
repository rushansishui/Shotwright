import { useEffect, useRef, useState } from "react";
import { assetUrl } from "../api";
import { keptSeconds, nextPosition } from "../clip";
import { useI18n } from "../i18n";
import type { Asset, FillMode, FitMode, Project, Shot, VisualPatch } from "../types";
import { Segmented } from "./Controls";

const ASPECTS: Record<string, number> = { "9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1 };

const fmt = (seconds: number) => `${seconds.toFixed(1)}s`;

/**
 * 镜头内剪辑：入出点（拖动滑块时预览跟着跳到对应画面）、素材不够长时的补足方式、
 * 原声音量、取景（点画面选取景中心）、在当前位置拆分镜头。
 */
export function ShotEdit(props: {
  project: Project;
  shot: Shot;
  asset: Asset;
  disabled: boolean;
  onUpdate: (visual: VisualPatch) => void;
  onSplit: (at: number) => void;
}) {
  const { project, shot, asset } = props;
  const visual = shot.visual;
  const { t } = useI18n();
  const video = useRef<HTMLVideoElement>(null);
  const isVideo = asset.kind === "video";
  const own = visual.edit_asset === asset.id;
  const total = asset.duration ?? 0;
  const savedStart = own ? visual.trim_start : 0;
  const savedEnd = own && visual.trim_end !== null ? visual.trim_end : total;
  const [start, setStart] = useState(savedStart);
  const [end, setEnd] = useState(savedEnd);
  const [volume, setVolume] = useState(visual.source_volume);
  const [now, setNow] = useState(savedStart);

  // 视频元数据加载完时跳到入点；用 ref 读最新的入点，避免拿到首次渲染时的旧值。
  const startRef = useRef(start);
  startRef.current = start;
  useEffect(() => setStart(savedStart), [savedStart]);
  useEffect(() => setEnd(savedEnd), [savedEnd]);
  useEffect(() => setVolume(visual.source_volume), [visual.source_volume]);

  const seek = (seconds: number) => {
    if (video.current) video.current.currentTime = seconds;
    setNow(seconds);
  };
  const skips = own ? (visual.skips ?? []) : [];
  // 播放时跳过剪掉的部分，停在出点，便于反复确认选段。
  const onTime = () => {
    const el = video.current;
    if (!el) return;
    setNow(el.currentTime);
    if (el.paused) return;
    const next = nextPosition(el.currentTime, { start: 0, end, skips }, false);
    if (next !== null) {
      el.currentTime = next;
    } else if (el.currentTime >= end - 0.02) {
      el.pause();
      el.currentTime = start;
    }
  };

  const commitTrim = (nextStart: number, nextEnd: number) => {
    const patch: VisualPatch = {};
    if (Math.abs(nextStart - savedStart) > 0.001) patch.trim_start = Number(nextStart.toFixed(3));
    if (Math.abs(nextEnd - savedEnd) > 0.001) {
      if (nextEnd >= total - 0.01) patch.clear_trim_end = true;
      else patch.trim_end = Number(nextEnd.toFixed(3));
    }
    if (Object.keys(patch).length) props.onUpdate(patch);
  };

  const clipLength = keptSeconds({ start, end, skips });
  const shotSeconds = shot.duration;
  const short = isVideo && shotSeconds !== null && clipLength < shotSeconds - 0.05;
  const canSplit = isVideo && now > start + 0.2 && now < end - 0.2;

  // 取景框：素材画面里，按画布比例能保留下来的那一块。
  const assetRatio = asset.width && asset.height ? asset.width / asset.height : ASPECTS[project.canvas.aspect];
  const canvasRatio = ASPECTS[project.canvas.aspect] ?? 1;
  const fx = own ? visual.focus_x : 0.5;
  const fy = own ? visual.focus_y : 0.5;
  const boxW = canvasRatio < assetRatio ? canvasRatio / assetRatio : 1;
  const boxH = canvasRatio < assetRatio ? 1 : assetRatio / canvasRatio;
  const pickFocus = (event: React.MouseEvent<HTMLDivElement>) => {
    if (props.disabled || visual.fit !== "cover") return;
    const rect = event.currentTarget.getBoundingClientRect();
    const px = (event.clientX - rect.left) / rect.width;
    const py = (event.clientY - rect.top) / rect.height;
    // 点击位置作为取景框中心，换算成 0–1 的偏移比例。
    const toFocus = (p: number, size: number) => (size >= 0.999 ? 0.5 : Math.min(1, Math.max(0, (p - size / 2) / (1 - size))));
    props.onUpdate({ focus_x: Number(toFocus(px, boxW).toFixed(3)), focus_y: Number(toFocus(py, boxH).toFixed(3)) });
  };

  const fitOptions: { value: FitMode; label: string; title?: string }[] = [
    { value: "cover", label: t("edit.fit.cover"), title: t("edit.fit.coverHint") },
    { value: "contain", label: t("edit.fit.contain") },
    { value: "blur", label: t("edit.fit.blur"), title: t("edit.fit.blurHint") },
  ];
  const fillOptions: { value: FillMode; label: string; title?: string }[] = [
    { value: "slow_hold", label: t("edit.fill.slow_hold"), title: t("edit.fill.slowHint") },
    { value: "hold", label: t("edit.fill.hold") },
    { value: "loop", label: t("edit.fill.loop") },
  ];

  return (
    <details className="shot-edit">
      <summary>
        {t("edit.title")}
        {isVideo && own && (visual.trim_start > 0 || visual.trim_end !== null) && (
          <span className="muted small"> · {t("edit.range", { start: fmt(savedStart), end: fmt(savedEnd) })}</span>
        )}
        {isVideo && own && !!visual.skips?.length && (
          <span className="muted small"> · {t("edit.skips", { n: visual.skips.length })}</span>
        )}
      </summary>
      <div className="shot-edit-body">
        <div className="shot-edit-media">
          {isVideo ? (
            <video
              ref={video}
              className="edit-video"
              src={assetUrl(project.id, asset.id)}
              controls
              playsInline
              preload="metadata"
              onLoadedMetadata={() => seek(startRef.current)}
              onTimeUpdate={onTime}
            />
          ) : (
            <img className="edit-video" src={assetUrl(project.id, asset.id)} alt="" />
          )}
        </div>

        <div className="shot-edit-controls">
          {isVideo && total > 0 && (
            <>
              <div className="trim">
                <div className="field-row">
                  <span className="label">{t("edit.trim")}</span>
                  <span className="muted small">
                    {t("edit.range", { start: fmt(start), end: fmt(end) })} · {t("edit.length", { n: clipLength.toFixed(1) })}
                  </span>
                </div>
                <div className="trim-track" style={{ ["--a" as string]: `${(start / total) * 100}%`, ["--b" as string]: `${(end / total) * 100}%` }}>
                  <input
                    type="range"
                    aria-label={t("edit.in")}
                    min={0}
                    max={total}
                    step={0.05}
                    value={start}
                    disabled={props.disabled}
                    onChange={(e) => {
                      const value = Math.min(Number(e.target.value), end - 0.1);
                      setStart(value);
                      seek(value);
                    }}
                    onPointerUp={() => commitTrim(start, end)}
                    onKeyUp={() => commitTrim(start, end)}
                  />
                  <input
                    type="range"
                    aria-label={t("edit.out")}
                    min={0}
                    max={total}
                    step={0.05}
                    value={end}
                    disabled={props.disabled}
                    onChange={(e) => {
                      const value = Math.max(Number(e.target.value), start + 0.1);
                      setEnd(value);
                      seek(value);
                    }}
                    onPointerUp={() => commitTrim(start, end)}
                    onKeyUp={() => commitTrim(start, end)}
                  />
                </div>
                <div className="trim-buttons">
                  <button type="button" className="btn small" disabled={props.disabled || now >= end - 0.1} onClick={() => commitTrim(now, end)}>
                    {t("edit.setIn")}
                  </button>
                  <button type="button" className="btn small" disabled={props.disabled || now <= start + 0.1} onClick={() => commitTrim(start, now)}>
                    {t("edit.setOut")}
                  </button>
                  <button
                    type="button"
                    className="btn small"
                    disabled={props.disabled || !canSplit}
                    title={t("edit.splitHint")}
                    onClick={() => props.onSplit(Number(keptSeconds({ start, end: now, skips }).toFixed(3)))}
                  >
                    {t("edit.split", { at: fmt(now) })}
                  </button>
                  {own && (visual.trim_start > 0 || visual.trim_end !== null) && (
                    <button
                      type="button"
                      className="btn small ghost"
                      disabled={props.disabled}
                      onClick={() => props.onUpdate({ trim_start: 0, clear_trim_end: true })}
                    >
                      {t("edit.reset")}
                    </button>
                  )}
                </div>
              </div>

              <div className="field">
                <span className="label">{t("edit.fill")}</span>
                <Segmented
                  label={t("edit.fill")}
                  value={visual.fill}
                  options={fillOptions}
                  disabled={props.disabled}
                  onChange={(fill) => props.onUpdate({ fill })}
                />
                <span className={`muted small ${short ? "warn-text" : ""}`}>
                  {short ? t("edit.shortHint", { clip: clipLength.toFixed(1), shot: (shotSeconds ?? 0).toFixed(1) }) : t("edit.fillHint")}
                </span>
              </div>

              <div className="field">
                <div className="field-row">
                  <label htmlFor={`srcvol-${shot.id}`}>{t("edit.sourceVolume")}</label>
                  <span className="muted small">{volume > 0 ? `${Math.round(volume * 100)}%` : t("edit.sourceOff")}</span>
                </div>
                <input
                  id={`srcvol-${shot.id}`}
                  type="range"
                  min={0}
                  max={2}
                  step={0.05}
                  value={volume}
                  disabled={props.disabled}
                  onChange={(e) => setVolume(Number(e.target.value))}
                  onPointerUp={() => volume !== visual.source_volume && props.onUpdate({ source_volume: volume })}
                  onKeyUp={() => volume !== visual.source_volume && props.onUpdate({ source_volume: volume })}
                />
                <span className="muted small">{t("edit.sourceHint")}</span>
              </div>
            </>
          )}

          <div className="field">
            <span className="label">{t("edit.framing")}</span>
            <Segmented
              label={t("edit.framing")}
              value={visual.fit}
              options={fitOptions}
              disabled={props.disabled}
              onChange={(fit) => props.onUpdate({ fit })}
            />
            {visual.fit === "cover" && (
              <>
                <div
                  className="focus-picker"
                  style={{ aspectRatio: String(assetRatio) }}
                  onClick={pickFocus}
                  role="button"
                  tabIndex={-1}
                  aria-label={t("edit.focusHint")}
                  title={t("edit.focusHint")}
                >
                  {isVideo ? (
                    <video src={`${assetUrl(project.id, asset.id)}#t=${start.toFixed(2)}`} muted playsInline preload="metadata" />
                  ) : (
                    <img src={assetUrl(project.id, asset.id)} alt="" />
                  )}
                  <span
                    className="focus-box"
                    style={{
                      width: `${boxW * 100}%`,
                      height: `${boxH * 100}%`,
                      left: `${fx * (1 - boxW) * 100}%`,
                      top: `${fy * (1 - boxH) * 100}%`,
                    }}
                  />
                </div>
                <span className="muted small">{t("edit.focusHint")}</span>
              </>
            )}
          </div>
        </div>
      </div>
    </details>
  );
}
