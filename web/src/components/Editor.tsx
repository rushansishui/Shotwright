import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Aspect, Project, Providers, ShotVisual, SubtitleStyle, VoiceSettings } from "../types";
import { Section, Segmented, Slider } from "./Controls";
import { RenderPanel } from "./RenderPanel";
import { ShotCard } from "./ShotCard";

export function Editor({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState("");
  const [busyShot, setBusyShot] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [voices, setVoices] = useState<{ id: string; name: string }[]>([]);
  const [providers, setProviders] = useState<Providers | null>(null);
  const [title, setTitle] = useState("");
  // 滑块拖动时只更新本地显示，松手后再保存。
  const [draft, setDraft] = useState<{ rate?: number; volume?: number; bgm?: number; size?: number }>({});
  const bgmInput = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api
      .getProject(projectId)
      .then((p) => {
        setProject(p);
        setTitle(p.title);
      })
      .catch((e) => setError((e as Error).message));
  }, [projectId]);

  useEffect(() => {
    load();
    api.voices().then(setVoices).catch(() => setVoices([]));
    api.providers().then(setProviders).catch(() => setProviders(null));
  }, [load]);

  async function run(action: () => Promise<Project>, shotId?: string) {
    setError("");
    if (shotId) setBusyShot(shotId);
    try {
      const next = await action();
      setProject(next);
      return next;
    } catch (e) {
      setError((e as Error).message);
      return null;
    } finally {
      if (shotId) setBusyShot(null);
    }
  }

  if (!project) {
    return <main className="page">{error ? <p className="hint error">{error}</p> : <p className="muted">加载中…</p>}</main>;
  }

  const id = project.id;
  const ttsProviders = providers?.tts ?? [];
  const defaultProvider = (kind: string) => {
    const found = (providers?.[kind] ?? []).find((p) => p.default);
    return found?.configured ? found : null;
  };
  const aiVideo = defaultProvider("video_gen");
  const aiImage = defaultProvider("image_gen");
  const updateVoice = (patch: Partial<VoiceSettings>) => run(() => api.updateProject(id, { voice: { ...project.voice, ...patch } }));
  const updateSubtitle = (patch: Partial<SubtitleStyle>) =>
    run(() => api.updateProject(id, { subtitle: { ...project.subtitle, ...patch } }));
  const bgmAsset = project.bgm.asset_id ? project.assets[project.bgm.asset_id] : undefined;

  async function moveShot(index: number, delta: number) {
    const order = project!.shots.map((s) => s.id);
    const [moved] = order.splice(index, 1);
    order.splice(index + delta, 0, moved);
    await run(() => api.reorder(id, order));
    setActiveIndex(index + delta);
  }

  async function uploadForShot(shotId: string, file: File) {
    setBusyShot(shotId);
    try {
      const asset = await api.upload(id, file);
      await run(() => api.updateShot(id, shotId, { visual: { mode: "upload", selected: asset.id } }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyShot(null);
    }
  }

  async function uploadBgm(file: File) {
    try {
      const asset = await api.upload(id, file);
      if (asset.kind !== "audio") throw new Error("请上传音频文件（mp3、m4a、wav 等）");
      await run(() => api.updateProject(id, { bgm: { ...project!.bgm, asset_id: asset.id } }));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <main className="page editor">
      <div className="editor-main">
        <div className="titlebar">
          <a className="btn ghost" href="#/">
            ← 返回
          </a>
          <label className="sr-only" htmlFor="title">
            项目标题
          </label>
          <input
            id="title"
            className="title-input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => title !== project.title && run(() => api.updateProject(id, { title }))}
          />
          <Segmented
            label="画面比例"
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
          title="分镜"
          extra={
            <button type="button" className="btn" onClick={() => run(() => api.addShot(id))}>
              添加镜头
            </button>
          }
        >
          {project.shots.length === 0 && <p className="muted">还没有镜头，点右上角"添加镜头"。</p>}
          <div className="shots">
            {project.shots.map((shot, index) => (
              <ShotCard
                key={shot.id}
                project={project}
                shot={shot}
                index={index}
                active={index === activeIndex}
                busy={busyShot === shot.id}
                aiVideo={aiVideo}
                aiImage={aiImage}
                onFocus={() => setActiveIndex(index)}
                onUpdate={(body: { text?: string; visual?: Partial<ShotVisual> }) => run(() => api.updateShot(id, shot.id, body))}
                onCandidates={(confirmCharge) => run(() => api.candidates(id, shot.id, confirmCharge), shot.id)}
                onUpload={(file) => uploadForShot(shot.id, file)}
                onMove={(delta) => moveShot(index, delta)}
                onInsert={() => run(() => api.addShot(id, shot.id))}
                onDelete={() => {
                  if (window.confirm(`删除镜头 ${index + 1}？`)) {
                    run(() => api.deleteShot(id, shot.id));
                    setActiveIndex(Math.max(0, Math.min(activeIndex, project.shots.length - 2)));
                  }
                }}
              />
            ))}
          </div>
        </Section>

        <Section step={2} title="声音">
          <div className="grid-2">
            <div className="field">
              <label htmlFor="tts">配音服务</label>
              <select id="tts" className="input" value={project.voice.provider} onChange={(e) => updateVoice({ provider: e.target.value })}>
                {ttsProviders.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="voice">声音</label>
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
              label="语速"
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
              label="配音音量"
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
          <div className="grid-2">
            <div className="field">
              <span className="label">背景音乐</span>
              <input
                ref={bgmInput}
                type="file"
                accept="audio/*"
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) uploadBgm(file);
                  e.target.value = "";
                }}
              />
              <div className="row">
                <button type="button" className="btn" onClick={() => bgmInput.current?.click()}>
                  {bgmAsset ? "更换" : "上传音乐"}
                </button>
                {bgmAsset && (
                  <>
                    <span className="muted small ellipsis">{bgmAsset.meta.filename ?? "已上传"}</span>
                    <button type="button" className="btn ghost" onClick={() => run(() => api.updateProject(id, { bgm: { ...project.bgm, asset_id: null } }))}>
                      移除
                    </button>
                  </>
                )}
              </div>
            </div>
            <Slider
              id="bgm-volume"
              label="背景音乐音量"
              value={draft.bgm ?? project.bgm.volume}
              min={0}
              max={1}
              step={0.05}
              format={(v) => `${Math.round(v * 100)}%`}
              onInput={(bgm) => setDraft({ ...draft, bgm })}
              onCommit={(volume) =>
                run(() => api.updateProject(id, { bgm: { ...project.bgm, volume } })).then(() => setDraft({ ...draft, bgm: undefined }))
              }
            />
          </div>
        </Section>

        <Section
          step={3}
          title="字幕"
          extra={
            <label className="switch">
              <input type="checkbox" checked={project.subtitle.enabled} onChange={(e) => updateSubtitle({ enabled: e.target.checked })} />
              启用字幕
            </label>
          }
        >
          {project.subtitle.enabled && (
            <div className="grid-2">
              <div className="field">
                <span className="label">位置</span>
                <Segmented
                  label="字幕位置"
                  value={project.subtitle.position}
                  onChange={(position) => updateSubtitle({ position })}
                  options={[
                    { value: "top", label: "顶部" },
                    { value: "middle", label: "中部" },
                    { value: "bottom", label: "底部" },
                  ]}
                />
              </div>
              <Slider
                id="sub-size"
                label="字号"
                value={draft.size ?? project.subtitle.size}
                min={24}
                max={120}
                step={2}
                format={(v) => `${v}`}
                onInput={(size) => setDraft({ ...draft, size })}
                onCommit={(size) => updateSubtitle({ size }).then(() => setDraft({ ...draft, size: undefined }))}
              />
              <div className="field">
                <span className="label">颜色</span>
                <div className="row">
                  <label className="color-field">
                    <input type="color" value={project.subtitle.color.toLowerCase()} onChange={(e) => updateSubtitle({ color: e.target.value.toUpperCase() })} />
                    文字
                  </label>
                  <label className="color-field">
                    <input
                      type="color"
                      value={project.subtitle.outline_color.toLowerCase()}
                      onChange={(e) => updateSubtitle({ outline_color: e.target.value.toUpperCase() })}
                    />
                    描边
                  </label>
                </div>
              </div>
            </div>
          )}
        </Section>
      </div>

      <RenderPanel project={project} paidAvailable={Boolean(aiVideo || aiImage)} shotIndex={activeIndex} onShotIndex={setActiveIndex} onRendered={load} />
    </main>
  );
}
