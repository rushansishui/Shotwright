import { useEffect, useMemo, useRef, useState } from "react";
import { api, assetUrl } from "../api";
import { formatClock, groupSentences, guessAspect, needsSpace, planRanges, type WordTuple } from "../cut";
import { providerName, useI18n } from "../i18n";
import type { MessageKey } from "../messages";
import type { LanguageInfo, Project, ProjectSummary, ProviderInfo, TranscriptData } from "../types";
import { ProjectList } from "./ProjectList";

interface CutProps {
  /** 已经上传并开始识别的项目和原片；为空时显示上传页。 */
  projectId?: string;
  assetId?: string;
}

/**
 * 剪辑：上传已有的视频 → 识别说的话 → 在文稿里删掉不要的词（视频跟着剪）→ 生成原声分镜，进入编辑器。
 */
export function Cut({ projectId, assetId }: CutProps) {
  if (projectId && assetId) return <CutEditor key={`${projectId}/${assetId}`} projectId={projectId} assetId={assetId} />;
  return <CutUpload />;
}

function readVideoSize(file: File): Promise<{ width: number; height: number; duration: number }> {
  return new Promise((resolve) => {
    const video = document.createElement("video");
    const url = URL.createObjectURL(file);
    const done = (value: { width: number; height: number; duration: number }) => {
      URL.revokeObjectURL(url);
      resolve(value);
    };
    video.preload = "metadata";
    video.onloadedmetadata = () => done({ width: video.videoWidth, height: video.videoHeight, duration: video.duration });
    video.onerror = () => done({ width: 0, height: 0, duration: 0 });
    video.src = url;
  });
}

type CutMode = "speech" | "scenes";

function CutUpload() {
  const { t, lang } = useI18n();
  const [files, setFiles] = useState<File[]>([]);
  const [mode, setMode] = useState<CutMode>("speech");
  const [languages, setLanguages] = useState<LanguageInfo[]>([]);
  const [language, setLanguage] = useState(lang === "zh" ? "zh-CN" : "auto");
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [provider, setProvider] = useState("");
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const loadProjects = () =>
    api
      .listProjects()
      .then((all) => setProjects(all.filter((p) => p.origin === "cut")))
      .catch(() => setProjects([]));

  useEffect(() => {
    api.languages().then(setLanguages).catch(() => undefined);
    loadProjects();
    api
      .providers()
      .then((all) => {
        const list = all.asr ?? [];
        setProviders(list);
        const chosen = list.find((p) => p.default && p.configured) ?? list.find((p) => p.configured);
        setProvider(chosen?.id ?? "");
        if (!chosen) setMode("scenes");
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  const usable = (providers ?? []).filter((p) => p.configured);
  const file = files[0] ?? null;
  const ready = files.length > 0 && !busy && (mode === "scenes" || usable.length > 0);

  async function start() {
    if (!file) return;
    setError("");
    try {
      setBusy(t("cut.uploading"));
      const size = await readVideoSize(file);
      const title = file.name.replace(/\.[^.]+$/, "").slice(0, 60);
      const project = await api.createProject({
        mode: "empty",
        title,
        language: language === "auto" ? undefined : language,
        canvas: { aspect: guessAspect(size.width, size.height) },
        video_type: "footage",
        origin: "cut",
      });
      if (mode === "scenes") {
        // 不识别：按画面变化切镜头，几段视频依次接在后面，直接进编辑器。
        for (let i = 0; i < files.length; i++) {
          setBusy(t("cut.importing", { n: i + 1, total: files.length }));
          await api.importFootage(project.id, files[i], i === 0);
        }
        window.location.hash = `#/p/${project.id}`;
        return;
      }
      const asset = await api.upload(project.id, file);
      await api.startTranscript(project.id, asset.id, { provider: provider || null, language });
      window.location.hash = `#/cut/${project.id}/${asset.id}`;
    } catch (e) {
      setError((e as Error).message);
      setBusy("");
    }
  }

  return (
    <main className="page home-layout cut-home">
      <section className="card cut-upload">
        <div className="card-head">
          <h1>{t("cut.title")}</h1>
        </div>
        <p className="muted">{t("cut.intro")}</p>

        <div className="cut-modes" role="radiogroup" aria-label={t("cut.modeLabel")}>
          {(["speech", "scenes"] as CutMode[]).map((value) => (
            <label key={value} className={`cut-mode ${mode === value ? "on" : ""}`}>
              <input
                type="radio"
                name="cut-mode"
                checked={mode === value}
                disabled={!!busy}
                onChange={() => {
                  setMode(value);
                  if (value === "speech") setFiles((f) => f.slice(0, 1));
                  if (value === "scenes" && language === "auto") setLanguage(lang === "zh" ? "zh-CN" : "en-US");
                }}
              />
              <strong>{t(`cut.mode.${value}` as MessageKey)}</strong>
              <span className="muted small">{t(`cut.mode.${value}Hint` as MessageKey)}</span>
            </label>
          ))}
        </div>

        <label className={`cut-drop ${files.length ? "has-file" : ""}`}>
          <input
            type="file"
            accept="video/*,audio/*"
            multiple={mode === "scenes"}
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            disabled={!!busy}
          />
          <strong>
            {files.length === 0
              ? mode === "scenes"
                ? t("cut.pickMany")
                : t("cut.pick")
              : files.length === 1
                ? file!.name
                : t("cut.picked", { n: files.length })}
          </strong>
          <span className="muted small">
            {files.length
              ? `${(files.reduce((sum, f) => sum + f.size, 0) / 1024 / 1024).toFixed(1)} MB`
              : t("cut.pickHint")}
          </span>
        </label>

        <div className="cut-options">
          <label className="field">
            <span className="label">{mode === "speech" ? t("cut.language") : t("cut.videoLanguage")}</span>
            <select className="input" value={language} onChange={(e) => setLanguage(e.target.value)} disabled={!!busy}>
              {mode === "speech" && <option value="auto">{t("cut.autoLanguage")}</option>}
              {languages.map((l) => (
                <option key={l.code} value={l.code}>
                  {lang === "zh" ? l.name_zh : l.name}
                </option>
              ))}
            </select>
          </label>
          {mode === "speech" && (
            <label className="field">
              <span className="label">{t("cut.provider")}</span>
              <select className="input" value={provider} onChange={(e) => setProvider(e.target.value)} disabled={!!busy}>
                {usable.map((p) => (
                  <option key={p.id} value={p.id}>
                    {providerName(p, lang)}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        {mode === "speech" && providers && !usable.length && (
          <p className="hint warn">
            {t("cut.providerMissing")} <a href="#/settings">{t("nav.status")} →</a>
          </p>
        )}
        {error && <p className="hint error">{error}</p>}
        <div className="cut-foot">
          <button type="button" className="btn primary" disabled={!ready} onClick={start}>
            {busy || (mode === "scenes" ? t("cut.startScenes") : t("cut.start"))}
          </button>
        </div>
      </section>

      <ProjectList
        className="home-projects"
        compact
        title={t("cut.recent")}
        empty={t("cut.recentEmpty")}
        projects={projects ?? []}
        loading={projects === null}
        onError={setError}
        onChanged={loadProjects}
      />
    </main>
  );
}

interface LegacyDraft {
  removed: number[];
  texts: Record<string, string>;
  pauses: boolean;
  maxPause: number;
  scenes: boolean;
}

/** 旧版把剪辑进度存在浏览器里；第一次打开时搬到服务端。 */
function legacyDraft(key: string): LegacyDraft | null {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as LegacyDraft) : null;
  } catch {
    return null;
  }
}

function CutEditor({ projectId, assetId }: { projectId: string; assetId: string }) {
  const { t } = useI18n();
  const draftKey = `shotwright.cut.${projectId}.${assetId}`;
  const [project, setProject] = useState<Project | null>(null);
  const [data, setData] = useState<TranscriptData | null>(null);
  const [error, setError] = useState("");
  const [removed, setRemoved] = useState<Set<number>>(() => new Set());
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [pauses, setPauses] = useState(false);
  const [maxPause, setMaxPause] = useState(0.8);
  const [scenes, setScenes] = useState(true);
  /** 剪辑进度已经从服务端读进来了；读进来之前不保存，免得用空白覆盖。 */
  const [draftReady, setDraftReady] = useState(false);
  const [saveState, setSaveState] = useState<"" | "saving" | "saved" | "failed">("");
  const [skip, setSkip] = useState(true);
  const [selection, setSelection] = useState<[number, number] | null>(null);
  const [anchor, setAnchor] = useState<number | null>(null);
  const [current, setCurrent] = useState(-1);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    api.getProject(projectId).then(setProject).catch((e) => setError((e as Error).message));
  }, [projectId]);

  const [tick, setTick] = useState(0);
  useEffect(() => {
    let stopped = false;
    let timer = 0;
    api
      .transcript(projectId, assetId)
      .then((next) => {
        if (stopped) return;
        setData(next);
        if (next.state === "running") timer = window.setTimeout(() => setTick((n) => n + 1), 2000);
      })
      .catch((e) => !stopped && setError((e as Error).message));
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [projectId, assetId, tick]);

  // 识别完成后读入剪辑进度：服务端有就用服务端的，没有时把旧版存在浏览器里的搬过来。
  useEffect(() => {
    if (draftReady || data?.state !== "done") return;
    const server = data.draft;
    const local = server ? null : legacyDraft(draftKey);
    const draft = server
      ? { removed: server.removed, texts: server.texts, pauses: server.pauses, maxPause: server.max_pause ?? 0.8, scenes: server.scenes }
      : local;
    if (draft) {
      setRemoved(new Set(draft.removed ?? []));
      setTexts(draft.texts ?? {});
      setPauses(!!draft.pauses);
      setMaxPause(draft.maxPause ?? 0.8);
      setScenes(draft.scenes ?? true);
    }
    setDraftReady(true);
  }, [data, draftReady, draftKey]);

  // 每次改动后停 0.8 秒自动保存到服务端，换浏览器、换电脑打开还在。
  useEffect(() => {
    if (!draftReady) return;
    const timer = window.setTimeout(() => {
      setSaveState("saving");
      api
        .saveCutDraft(projectId, assetId, {
          removed: [...removed].sort((a, b) => a - b),
          texts,
          pauses,
          max_pause: pauses ? maxPause : null,
          scenes,
        })
        .then(() => {
          setSaveState("saved");
          try {
            window.localStorage.removeItem(draftKey);
          } catch {
            /* 无所谓 */
          }
        })
        .catch(() => setSaveState("failed"));
    }, 800);
    return () => window.clearTimeout(timer);
  }, [draftReady, projectId, assetId, draftKey, removed, texts, pauses, maxPause, scenes]);

  const words: WordTuple[] = useMemo(
    () => (data?.words ?? []).map((w, i) => [texts[i] ?? w[0], w[1], w[2]] as WordTuple),
    [data?.words, texts],
  );
  const groups = useMemo(() => groupSentences(words, data?.sentences ?? []), [words, data?.sentences]);
  const asset = project?.assets[assetId];
  const total = asset?.duration ?? data?.duration ?? (words.length ? words[words.length - 1][2] : 0);
  const ranges = useMemo(
    () => planRanges(words, total, removed, pauses ? maxPause : null),
    [words, total, removed, pauses, maxPause],
  );
  const kept = ranges.reduce((sum, [a, b]) => sum + b - a, 0);

  function seek(seconds: number) {
    const video = videoRef.current;
    if (video) video.currentTime = Math.max(0, seconds);
  }

  function onTimeUpdate() {
    const video = videoRef.current;
    if (!video) return;
    const now = video.currentTime;
    if (skip && !video.paused && ranges.length) {
      const inside = ranges.some(([a, b]) => now >= a - 0.02 && now < b);
      if (!inside) {
        const next = ranges.find(([a]) => a > now);
        if (next) video.currentTime = next[0];
        else video.pause();
        return;
      }
    }
    let index = -1;
    for (let i = 0; i < words.length; i++) {
      if (words[i][1] <= now + 0.05) index = i;
      else break;
    }
    setCurrent(index);
  }

  function setRange(indexes: number[], value: boolean) {
    setRemoved((prev) => {
      const next = new Set(prev);
      for (const i of indexes) {
        if (value) next.add(i);
        else next.delete(i);
      }
      return next;
    });
  }

  function selectedIndexes(): number[] {
    if (!selection) return [];
    const [a, b] = selection;
    return Array.from({ length: b - a + 1 }, (_, i) => a + i);
  }

  function clickWord(index: number, shift: boolean) {
    if (shift && anchor !== null) {
      setSelection([Math.min(anchor, index), Math.max(anchor, index)]);
      return;
    }
    setAnchor(index);
    setSelection([index, index]);
    seek(words[index][1]);
  }

  function editWord(index: number) {
    const value = window.prompt(t("cut.editWord"), words[index][0]);
    if (value === null) return;
    const trimmed = value.trim();
    setTexts((prev) => {
      const next = { ...prev };
      if (!trimmed || trimmed === data?.words?.[index]?.[0]) delete next[index];
      else next[index] = trimmed;
      return next;
    });
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (target.closest("input, textarea, select")) return;
      if ((event.key === "Delete" || event.key === "Backspace") && selection) {
        event.preventDefault();
        const indexes = selectedIndexes();
        setRange(indexes, !indexes.every((i) => removed.has(i)));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  async function retry() {
    setError("");
    try {
      setData(await api.startTranscript(projectId, assetId, {}));
      setTick((n) => n + 1);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function aiRetakes() {
    setBusy("retakes");
    setNotice("");
    setError("");
    try {
      const { remove } = await api.suggestRetakes(projectId, assetId);
      setRange(remove, true);
      setNotice(remove.length ? t("cut.retakesDone", { n: groupsTouched(remove) }) : t("cut.retakesNone"));
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy("");
  }

  function groupsTouched(indexes: number[]): number {
    const set = new Set(indexes);
    return groups.filter((g) => g.some((i) => set.has(i))).length;
  }

  async function generate() {
    setBusy("generate");
    setError("");
    try {
      await api.cut(projectId, {
        asset_id: assetId,
        removed: [...removed].sort((a, b) => a - b),
        max_pause: pauses ? maxPause : null,
        split_scenes: scenes,
        texts,
      });
      window.location.hash = `#/p/${projectId}`;
    } catch (e) {
      setError((e as Error).message);
      setBusy("");
    }
  }

  const fillers = data?.fillers ?? [];
  const fillersOn = fillers.length > 0 && fillers.every((i) => removed.has(i));
  const selected = new Set(selectedIndexes());
  const selectionRemoved = selected.size > 0 && [...selected].every((i) => removed.has(i));

  return (
    <main className="page cut-page">
      <div className="cut-header">
        <a href="#/cut" className="btn ghost">
          {t("cut.back")}
        </a>
        <h1>{project?.title || t("cut.title")}</h1>
      </div>
      {error && <p className="hint error">{error}</p>}

      {(!data || data.state === "running") && (
        <section className="card cut-waiting">
          <strong>
            {t(`cut.stage.${data?.stage ?? "extract"}` as MessageKey)}
            {(data?.stage === "download" || data?.stage === "recognize") && data.progress
              ? ` ${Math.round(data.progress * 100)}%`
              : ""}
          </strong>
          <div className="progress">
            <div
              className="progress-bar"
              style={{ width: `${Math.round(((data?.stage === "download" || data?.stage === "recognize") ? data.progress ?? 0 : 0) * 100)}%` }}
            />
          </div>
          <p className="muted small">
            {data?.elapsed ? t("cut.elapsed", { time: formatClock(data.elapsed) }) + " · " : ""}
            {data?.stage === "download" || data?.stage === "model" ? t("cut.downloadHint") : t("cut.recognizingHint")}
          </p>
        </section>
      )}

      {data && (data.state === "failed" || data.state === "none") && (
        <section className="card cut-waiting">
          {data.error && <p className="hint error">{t("cut.failed", { error: data.error })}</p>}
          <button type="button" className="btn primary" onClick={retry}>
            {t("cut.retry")}
          </button>
        </section>
      )}

      {data?.state === "done" && (
        <div className="cut-layout">
          <aside className="cut-side card">
            {asset && (
              <video
                ref={videoRef}
                className="cut-video"
                src={assetUrl(projectId, assetId)}
                controls
                playsInline
                onTimeUpdate={onTimeUpdate}
                onSeeked={onTimeUpdate}
              />
            )}
            <p className="cut-stats">
              {t("cut.stats", { source: formatClock(total), kept: formatClock(kept) })}
              {removed.size > 0 && <span className="muted small"> · {t("cut.removedCount", { n: removed.size })}</span>}
              {saveState && (
                <span className={`muted small cut-save ${saveState}`}> · {t(`cut.save.${saveState}` as MessageKey)}</span>
              )}
            </p>
            <label className="check small">
              <input type="checkbox" checked={skip} onChange={(e) => setSkip(e.target.checked)} />
              {t("cut.skipPreview")}
            </label>
            <div className="cut-tools">
              <button
                type="button"
                className="btn small"
                disabled={!fillers.length}
                onClick={() => setRange(fillers, !fillersOn)}
              >
                {fillers.length ? t("cut.fillers", { n: fillers.length }) : t("cut.fillersNone")}
              </button>
              <button type="button" className="btn small" disabled={busy === "retakes" || !words.length} onClick={aiRetakes}>
                {busy === "retakes" ? t("cut.retakesBusy") : t("cut.retakes")}
              </button>
              <button
                type="button"
                className="btn small ghost"
                disabled={!removed.size && !Object.keys(texts).length}
                onClick={() => {
                  setRemoved(new Set());
                  setTexts({});
                }}
              >
                {t("cut.restoreAll")}
              </button>
            </div>
            {notice && <p className="hint small">{notice}</p>}
            <label className="check small cut-pause" title={t("cut.pausesHint")}>
              <input type="checkbox" checked={pauses} onChange={(e) => setPauses(e.target.checked)} />
              {t("cut.pauses")}
              <input
                type="number"
                className="input"
                min={0.3}
                max={5}
                step={0.1}
                value={maxPause}
                disabled={!pauses}
                onChange={(e) => setMaxPause(Math.min(5, Math.max(0.3, Number(e.target.value) || 0.8)))}
              />
              {t("cut.pausesUnit")}
            </label>
            <p className="muted small">{t("cut.pausesHint")}</p>
            <label className="check small">
              <input type="checkbox" checked={scenes} onChange={(e) => setScenes(e.target.checked)} />
              {t("cut.scenes")}
            </label>
            <div className="cut-foot">
              {project && project.shots.length > 0 && (
                <p className="muted small">{t("cut.replaceWarn", { n: project.shots.length })}</p>
              )}
              <button
                type="button"
                className="btn primary"
                disabled={busy === "generate" || !ranges.length}
                onClick={generate}
              >
                {busy === "generate" ? t("cut.generating") : t("cut.generate")}
              </button>
            </div>
          </aside>

          <section className="card cut-transcript">
            <div className="cut-transcript-head">
              <p className="muted small">{t("cut.help")}</p>
              {selection && (
                <button type="button" className="btn small" onClick={() => setRange(selectedIndexes(), !selectionRemoved)}>
                  {selectionRemoved ? t("cut.restoreSelected") : t("cut.removeSelected")}
                </button>
              )}
            </div>
            {!words.length && <p className="hint">{t("cut.noSpeech")}</p>}
            {groups.map((group, n) => {
              const allRemoved = group.every((i) => removed.has(i));
              return (
                <p key={n} className={`cut-sentence ${allRemoved ? "removed" : ""}`}>
                  <span className="cut-time" onClick={() => seek(words[group[0]][1])}>
                    {formatClock(words[group[0]][1])}
                  </span>
                  {group.map((i, k) => (
                    <span key={i}>
                      {k > 0 && needsSpace(words[group[k - 1]][0], words[i][0]) ? " " : ""}
                      <span
                        className={[
                          "cut-word",
                          removed.has(i) ? "removed" : "",
                          selected.has(i) ? "selected" : "",
                          i === current ? "playing" : "",
                          texts[i] !== undefined ? "edited" : "",
                          fillers.includes(i) ? "filler" : "",
                        ]
                          .filter(Boolean)
                          .join(" ")}
                        onClick={(e) => clickWord(i, e.shiftKey)}
                        onDoubleClick={() => editWord(i)}
                      >
                        {words[i][0]}
                      </span>
                    </span>
                  ))}
                  <button
                    type="button"
                    className="cut-sentence-x"
                    title={allRemoved ? t("cut.restoreSentence") : t("cut.deleteSentence")}
                    aria-label={allRemoved ? t("cut.restoreSentence") : t("cut.deleteSentence")}
                    onClick={() => setRange(group, !allRemoved)}
                  >
                    {allRemoved ? "↺" : "×"}
                  </button>
                </p>
              );
            })}
          </section>
        </div>
      )}
    </main>
  );
}
