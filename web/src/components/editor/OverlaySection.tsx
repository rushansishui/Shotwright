import { api } from "../../api";
import type { Overlay, OverlayPosition, Project, TransitionKind } from "../../types";
import { Section, Segmented, Slider } from "../Controls";
import { useI18n } from "../../i18n";

const POSITIONS: OverlayPosition[] = ["top", "middle", "bottom"];
const TRANSITIONS: TransitionKind[] = ["none", "fade", "slide", "wipe"];

function newOverlay(): Overlay {
  return {
    id: `ov${Date.now().toString(36)}`,
    text: "",
    image: null,
    start: 0,
    duration: null,
    position: "top",
    size: 52,
    color: "#FFFFFF",
    outline_color: "#000000",
    margin: 120,
    enabled: true,
  };
}

interface OverlaySectionProps {
  project: Project;
  run: (action: () => Promise<Project>, shotId?: string) => Promise<Project | null>;
}

export function OverlaySection({ project, run }: OverlaySectionProps) {
  const { t } = useI18n();
  const overlays = project.overlays ?? [];
  // 可作为图形叠加的图片素材（Logo、产品图等）。
  const imageAssets = Object.values(project.assets).filter((asset) => asset.kind === "image");

  function save(next: Overlay[]) {
    return run(() => api.updateProject(project.id, { overlays: next }));
  }

  function patch(index: number, change: Partial<Overlay>) {
    return save(overlays.map((item, i) => (i === index ? { ...item, ...change } : item)));
  }

  return (
    <Section title={t("overlay.title")} extra={
      <button type="button" className="btn" onClick={() => save([...overlays, newOverlay()])}>
        {t("overlay.add")}
      </button>
    }>
      <p className="muted small">{t("overlay.hint")}</p>
      {overlays.length === 0 && <p className="muted">{t("overlay.empty")}</p>}

      {overlays.map((item, index) => (
        <div className="overlay-item" key={item.id}>
          <div className="row">
            <input
              className="input"
              value={item.text}
              placeholder={t("overlay.textPlaceholder")}
              onChange={(e) => patch(index, { text: e.target.value })}
            />
            <label className="switch">
              <input type="checkbox" checked={item.enabled} onChange={(e) => patch(index, { enabled: e.target.checked })} />
              {t("overlay.enabled")}
            </label>
            <button
              type="button"
              className="btn ghost"
              onClick={() => save(overlays.filter((_, i) => i !== index))}
            >
              {t("overlay.remove")}
            </button>
          </div>
          <div className="grid-2">
            <div className="field">
              <label htmlFor={`ov-img-${item.id}`}>{t("overlay.image")}</label>
              <select
                id={`ov-img-${item.id}`}
                className="input"
                value={item.image ?? ""}
                onChange={(e) => patch(index, { image: e.target.value || null, text: e.target.value ? "" : item.text })}
              >
                <option value="">{t("overlay.imageNone")}</option>
                {imageAssets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.meta.filename ?? asset.meta.title ?? asset.id}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <span className="label">{t("overlay.position")}</span>
              <Segmented
                label={t("overlay.position")}
                value={item.position}
                options={POSITIONS.map((value) => ({ value, label: t(`overlay.pos.${value}`) }))}
                onChange={(position) => patch(index, { position })}
              />
            </div>
          </div>
          <div className="grid-2">
            <Slider
              id={`ov-size-${item.id}`}
              label={t("overlay.size")}
              value={item.size}
              min={16}
              max={160}
              step={2}
              format={(v) => `${v}`}
              onCommit={(size) => patch(index, { size })}
            />
          </div>
          <div className="grid-2">
            <div className="field">
              <div className="field-row">
                <label htmlFor={`ov-start-${item.id}`}>{t("overlay.start")}</label>
                <span className="muted">{item.start.toFixed(1)}s</span>
              </div>
              <input
                id={`ov-start-${item.id}`}
                type="range"
                min={0}
                max={Math.max(1, Math.round(project.shots.length * 3))}
                step={0.1}
                value={item.start}
                onChange={(e) => patch(index, { start: Number(e.target.value) })}
              />
            </div>
            <label className="check">
              <input
                type="checkbox"
                checked={item.duration !== null}
                onChange={(e) => patch(index, { duration: e.target.checked ? 3 : null })}
              />
              {t("overlay.limitedDuration")}
            </label>
          </div>
          {item.duration !== null && (
            <Slider
              id={`ov-dur-${item.id}`}
              label={t("overlay.duration")}
              value={item.duration}
              min={0.5}
              max={15}
              step={0.5}
              format={(v) => `${v.toFixed(1)}s`}
              onCommit={(duration) => patch(index, { duration })}
            />
          )}
        </div>
      ))}

      <div className="divider" />
      <label className="check">
        <input
          type="checkbox"
          checked={project.ai_badge}
          onChange={(e) => run(() => api.updateProject(project.id, { ai_badge: e.target.checked }))}
        />
        {t("overlay.aiBadge")}
      </label>
      <p className="muted small">{t("overlay.aiBadgeHint")}</p>
      <div className="divider" />
      <div className="field">
        <span className="label">{t("overlay.transition")}</span>
        <div className="row">
          <Segmented
            label={t("overlay.transition")}
            value={project.transition?.kind ?? "none"}
            options={TRANSITIONS.map((value) => ({ value, label: t(`overlay.trans.${value}`) }))}
            onChange={(kind) =>
              run(() =>
                api.updateProject(project.id, {
                  transition: { kind, duration: project.transition?.duration ?? 0.4 },
                }),
              )
            }
          />
          {(project.transition?.kind ?? "none") !== "none" && (
            <Slider
              id="transition-duration"
              label={t("overlay.transitionDuration")}
              value={project.transition?.duration ?? 0.4}
              min={0.1}
              max={2}
              step={0.1}
              format={(v) => `${v.toFixed(1)}s`}
              onCommit={(duration) =>
                run(() => api.updateProject(project.id, { transition: { kind: project.transition.kind, duration } }))
              }
            />
          )}
        </div>
        <p className="muted small">{t("overlay.transitionHint")}</p>
      </div>
    </Section>
  );
}
