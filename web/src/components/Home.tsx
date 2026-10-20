import { useEffect, useState } from "react";
import { api } from "../api";
import type { Aspect, ProjectSummary, Providers } from "../types";
import { Segmented } from "./Controls";

export function Home() {
  const [mode, setMode] = useState<"llm" | "split">("llm");
  const [subject, setSubject] = useState("");
  const [script, setScript] = useState("");
  const [aspect, setAspect] = useState<Aspect>("9:16");
  const [shots, setShots] = useState(6);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [providers, setProviders] = useState<Providers | null>(null);

  useEffect(() => {
    api.listProjects().then(setProjects).catch(() => setProjects([]));
    api.providers().then(setProviders).catch(() => setProviders(null));
  }, []);

  const llmReady = providers?.llm?.some((p) => p.default && p.configured) ?? false;
  const canSubmit = mode === "llm" ? subject.trim().length > 0 : script.trim().length > 0;

  async function create() {
    setBusy(true);
    setError("");
    try {
      const project = await api.createProject({
        mode,
        subject: subject.trim(),
        script: mode === "split" ? script : "",
        canvas: { aspect },
        shots,
      });
      window.location.hash = `#/p/${project.id}`;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="page narrow">
      <section className="card">
        <div className="card-head">
          <h1>新建视频</h1>
        </div>
        <Segmented
          label="创建方式"
          value={mode}
          onChange={setMode}
          options={[
            { value: "llm", label: "从主题开始（AI 写文案）" },
            { value: "split", label: "粘贴现成文案" },
          ]}
        />
        {mode === "llm" ? (
          <div className="field">
            <label htmlFor="subject">视频主题</label>
            <input
              id="subject"
              className="input"
              placeholder="例如：人工智能如何改变日常生活"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && canSubmit && !busy && create()}
            />
            {providers && !llmReady && (
              <p className="hint warn">
                还没有配置大模型。请在 config.toml 中填写 [providers.llm.openai]，或者改用"粘贴现成文案"。
              </p>
            )}
          </div>
        ) : (
          <div className="field">
            <label htmlFor="script">视频文案</label>
            <textarea
              id="script"
              className="input"
              rows={7}
              placeholder="粘贴文案，按句号、问号等自动拆成镜头。"
              value={script}
              onChange={(e) => setScript(e.target.value)}
            />
          </div>
        )}
        <div className="grid-2">
          <div className="field">
            <span className="label">画面比例</span>
            <Segmented
              label="画面比例"
              value={aspect}
              onChange={setAspect}
              options={[
                { value: "9:16", label: "竖屏 9:16" },
                { value: "16:9", label: "横屏 16:9" },
                { value: "1:1", label: "方形 1:1" },
              ]}
            />
          </div>
          {mode === "llm" && (
            <div className="field">
              <div className="field-row">
                <label htmlFor="shots">镜头数量</label>
                <span className="muted">约 {shots} 个</span>
              </div>
              <input id="shots" type="range" min={3} max={15} value={shots} onChange={(e) => setShots(Number(e.target.value))} />
            </div>
          )}
        </div>
        {error && <p className="hint error">{error}</p>}
        <button type="button" className="btn primary big" disabled={!canSubmit || busy} onClick={create}>
          {busy ? (mode === "llm" ? "AI 正在写分镜…" : "创建中…") : "生成分镜"}
        </button>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>最近的项目</h2>
        </div>
        {projects.length === 0 ? (
          <p className="muted">还没有项目。</p>
        ) : (
          <ul className="project-list">
            {projects.map((p) => (
              <li key={p.id}>
                <a href={`#/p/${p.id}`}>
                  <span className="project-title">{p.title}</span>
                  <span className="muted">
                    {p.aspect} · {p.shots} 个镜头 · {new Date(p.updated_at * 1000).toLocaleString()}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
