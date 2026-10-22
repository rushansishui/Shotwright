import { useCallback, useEffect, useRef, useState } from "react";
import { api, assetUrl } from "../api";
import type { Aspect, LanguageInfo, Project, Providers, SubtitleStyle, VisualMode, VisualPatch, VoiceSettings } from "../types";
import { Section, Segmented, Slider } from "./Controls";
import { providerName, useI18n } from "../i18n";
import { RenderPanel } from "./RenderPanel";
import { Versions } from "./Versions";
import { ShotCard } from "./ShotCard";

export function Editor({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState("");
  // 正在检索 / 生成 / 上传的镜头 → 开始时间（毫秒）。可以同时有多个镜头在生成。
  const [busy, setBusy] = useState<Record<string, number>>({});
  const [now, setNow] = useState(Date.now());
  const inflight = useRef(new Set<string>());
  const [activeIndex, setActiveIndex] = useState(0);
  const [voices, setVoices] = useState<{ id: string; name: string }[]>([]);
  const [languages, setLanguages] = useState<LanguageInfo[]>([]);
  const { t, lang } = useI18n();
  const [providers, setProviders] = useState<Providers | null>(null);
  const [title, setTitle] = useState("");
  // 滑块拖动时只更新本地显示，松手后再保存。
  const [draft, setDraft] = useState<{ rate?: number; volume?: number; bgm?: number; size?: number }>({});
  const bgmInput = useRef<HTMLInputElement>(null);
  const [bgmBusy, setBgmBusy] = useState(false);

  const load = useCallback(() => {
    api
      .getProject(projectId)
      .then((p) => {
        setProject(p);
        setTitle(p.title);
      })
      .catch((e) => setError((e as Error).message));
  }, [projectId]);

  const markBusy = useCallback((shotId: string, on: boolean, since = Date.now()) => {
    setBusy((prev) => {
      if (on === shotId in prev) return prev;
      const next = { ...prev };
      if (on) next[shotId] = since;
      else delete next[shotId];
      return next;
    });
  }, []);

  // 刷新页面后，从服务器恢复仍在生成的镜头；生成期间每 2 秒同步一次，完成后自动刷新项目。
  const busyCount = Object.keys(busy).length;
  useEffect(() => {
    let stopped = false;
    const sync = async () => {
      try {
        const remote = await api.inProgress(projectId);
        if (stopped) return;
        let finished = false;
        setBusy((prev) => {
          const next = { ...prev };
          for (const [shotId, info] of Object.entries(remote)) {
            if (!(shotId in next)) next[shotId] = Date.now() - info.seconds * 1000;
          }
          for (const shotId of Object.keys(next)) {
            if (!(shotId in remote) && !inflight.current.has(shotId)) {
              delete next[shotId];
              finished = true;
            }
          }
          return next;
        });
        if (finished) load();
      } catch {
        /* 网络抖动时下次再试 */
      }
    };
    sync();
    if (busyCount === 0) return () => void (stopped = true);
    const poll = window.setInterval(sync, 2000);
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      stopped = true;
      window.clearInterval(poll);
      window.clearInterval(tick);
    };
  }, [projectId, busyCount, load]);

  useEffect(() => {
    load();
    api.providers().then(setProviders).catch(() => setProviders(null));
    api.languages().then(setLanguages).catch(() => setLanguages([]));
  }, [load]);

  // 声音下拉框：Edge 按语言列，其他配音服务列自己的声音。
  const projectLanguage = project?.language;
  const voiceProvider = project?.voice.provider;
  useEffect(() => {
    if (!projectLanguage) return;
    api
      .voices(projectLanguage, voiceProvider)
      .then(setVoices)
      .catch(() => setVoices([]));
  }, [projectLanguage, voiceProvider]);

  async function run(action: () => Promise<Project>, shotId?: string) {
    setError("");
    if (shotId) {
      inflight.current.add(shotId);
      markBusy(shotId, true);
    }
    try {
      const next = await action();
      setProject(next);
      return next;
    } catch (e) {
      setError((e as Error).message);
      return null;
    } finally {
      if (shotId) {
        inflight.current.delete(shotId);
        markBusy(shotId, false);
      }
    }
  }

  if (!project) {
    return <main className="page">{error ? <p className="hint error">{error}</p> : <p className="muted">{t("common.loading")}</p>}</main>;
  }

  const id = project.id;
  const ttsProviders = providers?.tts ?? [];
  const defaultProvider = (kind: string) => {
    const found = (providers?.[kind] ?? []).find((p) => p.default);
    return found?.configured ? found : null;
  };
  // 素材的 source（服务 id）→ 它属于哪种画面来源，用于按来源筛选候选素材。
  const sourceModes: Record<string, VisualMode> = { upload: "upload" };
  for (const [kind, mode] of [
    ["stock", "stock"],
    ["video_gen", "ai"],
    ["image_gen", "image"],
  ] as const) {
    for (const p of providers?.[kind] ?? []) sourceModes[p.id] ??= mode;
  }
  const aiVideo = defaultProvider("video_gen");
  const aiImage = defaultProvider("image_gen");
  const updateVoice = (patch: Partial<VoiceSettings>) => run(() => api.updateProject(id, { voice: { ...project.voice, ...patch } }));
  const updateSubtitle = (patch: Partial<SubtitleStyle>) =>
    run(() => api.updateProject(id, { subtitle: { ...project.subtitle, ...patch } }));
  const bgmAsset = project.bgm.asset_id ? project.assets[project.bgm.asset_id] : undefined;
  /** 自动检索到的背景音乐候选（上传的素材不在其中）。 */
  const bgmCandidates = (project.bgm.candidates ?? [])
    .map((assetId) => project.assets[assetId])
    .filter((asset): asset is NonNullable<typeof asset> => asset?.kind === "audio");

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

  async function uploadBgm(file: File) {
    try {
      const asset = await api.upload(id, file);
      if (asset.kind !== "audio") throw new Error(t("editor.bgmNotAudio"));
      await run(() => api.updateProject(id, { bgm: { ...project!.bgm, asset_id: asset.id } }));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function searchBgm() {
    setBgmBusy(true);
    setError("");
    try {
      const next = await api.bgmCandidates(id, project!.bgm.query || "");
      setProject(next);
      // 检索到之后自动选用第一首，省一次点击。
      const first = next.bgm.candidates?.[0];
      if (first && !next.bgm.asset_id) {
        await run(() => api.updateProject(id, { bgm: { ...next.bgm, asset_id: first } }));
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBgmBusy(false);
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
            <button type="button" className="btn" onClick={() => run(() => api.addShot(id))}>
              {t("editor.addShot")}
            </button>
          }
        >
          {project.shots.length === 0 && <p className="muted">{t("editor.noShots")}</p>}
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
                onCandidates={(confirmCharge, prompt) =>
                  run(async () => {
                    // 还没失焦保存的提示词先存好，再生成，避免用旧提示词花钱。
                    if (prompt !== undefined) await api.updateShot(id, shot.id, { visual: { prompt } });
                    return api.candidates(id, shot.id, confirmCharge);
                  }, shot.id)
                }
                onUpload={(file) => uploadForShot(shot.id, file)}
                onMove={(delta) => moveShot(index, delta)}
                onInsert={() => run(() => api.addShot(id, shot.id))}
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

        <Section step={2} title={t("editor.audio")}>
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
          <div className="grid-2">
            <div className="field">
              <span className="label">{t("editor.bgm")}</span>
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
                  {bgmAsset ? t("editor.bgmReplace") : t("editor.bgmUpload")}
                </button>
                <button type="button" className="btn" disabled={bgmBusy} onClick={searchBgm}>
                  {bgmBusy ? t("editor.bgmSearching") : t("editor.bgmSearch")}
                </button>
                {bgmAsset && (
                  <>
                    <span className="muted small ellipsis">{bgmAsset.meta.title ?? bgmAsset.meta.filename ?? t("editor.bgmUploaded")}</span>
                    <button type="button" className="btn ghost" onClick={() => run(() => api.updateProject(id, { bgm: { ...project.bgm, asset_id: null } }))}>
                      {t("editor.bgmRemove")}
                    </button>
                  </>
                )}
              </div>
              {project.bgm.query && <p className="hint">{t("editor.bgmQuery", { query: project.bgm.query })}</p>}
              {bgmCandidates.length > 0 && (
                <div className="bgm-candidates" role="listbox" aria-label={t("editor.bgmCandidates")}>
                  {bgmCandidates.map((asset) => (
                    <button
                      type="button"
                      key={asset.id}
                      role="option"
                      aria-selected={project.bgm.asset_id === asset.id}
                      className={`bgm-candidate ${project.bgm.asset_id === asset.id ? "on" : ""}`}
                      onClick={() => run(() => api.updateProject(id, { bgm: { ...project.bgm, asset_id: asset.id } }))}
                      title={asset.meta.author ? t("shot.author", { name: asset.meta.author }) : asset.meta.title}
                    >
                      <span className="bgm-candidate-title">{asset.meta.title ?? asset.id}</span>
                      <span className="muted small">
                        {asset.duration ? t("editor.bgmDuration", { n: Math.round(asset.duration) }) : ""}
                        {asset.meta.author ? ` · ${asset.meta.author}` : ""}
                        {asset.meta.license_kind === "unknown" ? ` · ${t("editor.bgmLicenseUnknown")}` : ""}
                      </span>
                      {asset.meta.license && (
                        <span className="muted small ellipsis">{t("editor.bgmLicense", { license: asset.meta.license })}</span>
                      )}
                      <audio src={assetUrl(id, asset.id)} controls preload="none" onClick={(e) => e.stopPropagation()} />
                    </button>
                  ))}
                </div>
              )}
            </div>
            <Slider
              id="bgm-volume"
              label={t("editor.bgmVolume")}
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

        <Versions project={project} languages={languages} />
      </div>

      <RenderPanel project={project} paidAvailable={Boolean(aiVideo || aiImage)} shotIndex={activeIndex} onShotIndex={setActiveIndex} onRendered={load} />
    </main>
  );
}
