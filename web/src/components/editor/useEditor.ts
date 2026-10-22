import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type { LanguageInfo, Project, Providers, VoiceSettings } from "../../types";
import { useI18n } from "../../i18n";

export type DraftSliders = { rate?: number; volume?: number; bgm?: number; size?: number };

export function useEditor(projectId: string) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<Record<string, number>>({});
  const [now, setNow] = useState(Date.now());
  const inflight = useRef(new Set<string>());
  const [activeIndex, setActiveIndex] = useState(0);
  const [voices, setVoices] = useState<{ id: string; name: string }[]>([]);
  const [languages, setLanguages] = useState<LanguageInfo[]>([]);
  useI18n();
  const [providers, setProviders] = useState<Providers | null>(null);
  const [title, setTitle] = useState("");
  const [draft, setDraft] = useState<DraftSliders>({});

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

  const updateVoice = (patch: Partial<VoiceSettings>) =>
    run(() => api.updateProject(project!.id, { voice: { ...project!.voice, ...patch } }));

  return {
    project,
    error,
    setError,
    busy,
    now,
    activeIndex,
    setActiveIndex,
    voices,
    languages,
    providers,
    title,
    setTitle,
    draft,
    setDraft,
    load,
    run,
    updateVoice,
  };
}
