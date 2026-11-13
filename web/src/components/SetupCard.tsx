import { useEffect, useState } from "react";
import { api } from "../api";
import { providerName, useI18n } from "../i18n";
import type { Providers } from "../types";

/**
 * 首次启动向导：还没配置大模型时显示在首页最上面。
 * 两条路：选一家大模型服务填 Key，或者先免费试试（自己写口播脚本、剪辑已有视频）。
 * 选服务时把不花钱的两种放在最前面：智谱的免费模型（注册拿 Key）和本机的 Ollama（不要 Key）。
 * 保存后立刻测试连接，成功就刷新服务状态，卡片自动消失。
 */
export function SetupCard({
  providers,
  onConfigured,
}: {
  providers: Providers;
  onConfigured: () => void;
}) {
  const { t, lang } = useI18n();
  const llms = providers.llm ?? [];
  /** 不预选任何一家，由用户自己挑。 */
  const [chosen, setChosen] = useState("");
  const [key, setKey] = useState("");
  const [state, setState] = useState<"" | "saving" | "testing">("");
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [editable, setEditable] = useState<boolean | null>(null);
  const provider = llms.find((p) => p.id === chosen);
  const guide = provider ? (lang === "zh" ? provider.setup : provider.setup_en) : "";
  /** 除了 Key 还有必填、又没有默认值的字段（例如自定义接口的地址）：到服务设置页去填。 */
  const needsMore = !!provider?.fields.some((f) => f.required && f.key !== "api_key" && !f.default);
  /** 不花钱的大模型：智谱免费模型、本机 Ollama。 */
  const freeLlms = llms.filter((p) => p.free);
  /** 本机 Ollama 的检测结果：null 表示还没检测完。 */
  const [local, setLocal] = useState<{ running: boolean; models: string[] } | null>(null);
  const [localModel, setLocalModel] = useState("");

  useEffect(() => {
    api
      .settings()
      .then((data) => setEditable(data.editable))
      .catch(() => setEditable(false));
  }, []);

  function detectLocal() {
    setLocal(null);
    api
      .localLlm()
      .then((found) => {
        setLocal(found);
        setLocalModel((current) => (found.models.includes(current) ? current : (found.models[0] ?? "")));
      })
      .catch(() => setLocal({ running: false, models: [] }));
  }

  // 打开首页就看一眼本机有没有在跑 Ollama：有就直接给「一键使用」。
  useEffect(detectLocal, []);

  function pick(id: string) {
    setChosen(id);
    setResult(null);
    if (id === "ollama") detectLocal();
  }

  /** 一键使用本机模型：填上模型名、设为默认，再测一次连接。 */
  async function useLocal() {
    if (!localModel) return;
    setResult(null);
    setState("saving");
    try {
      await api.saveProviderSettings("llm", "ollama", { model: localModel });
      await api.saveDefaultProvider("llm", "ollama");
      setState("testing");
      const tested = await api.testProvider("llm", "ollama");
      setResult(tested);
      if (tested.ok) onConfigured();
    } catch (e) {
      setResult({ ok: false, message: (e as Error).message });
    } finally {
      setState("");
    }
  }

  async function save() {
    if (!provider) return;
    setResult(null);
    setState("saving");
    try {
      await api.saveProviderSettings("llm", provider.id, { api_key: key.trim() });
      await api.saveDefaultProvider("llm", provider.id);
      setState("testing");
      const tested = await api.testProvider("llm", provider.id);
      setResult(tested);
      if (tested.ok) {
        setKey("");
        onConfigured();
      }
    } catch (e) {
      setResult({ ok: false, message: (e as Error).message });
    } finally {
      setState("");
    }
  }

  return (
    <section className="setup-card" aria-labelledby="setup-title">
      <h2 id="setup-title">{t("setup.title")}</h2>
      <p className="muted">{t("setup.intro")}</p>
      <div className="setup-options">
        <div className="setup-option">
          <h3>{t("setup.llm")}</h3>
          <p className="muted small">{t("setup.llmWhy")}</p>
          {freeLlms.length > 0 && (
            <div className="setup-free-llm">
              <span className="label">{t("setup.freeLlm")}</span>
              <div className="row">
                {freeLlms.map((p) => (
                  <button
                    type="button"
                    key={p.id}
                    className={`chip ${chosen === p.id ? "on" : ""}`}
                    aria-pressed={chosen === p.id}
                    onClick={() => pick(p.id)}
                  >
                    {providerName(p, lang)}
                  </button>
                ))}
              </div>
              {local?.running && local.models.length > 0 && chosen !== "ollama" && (
                <p className="hint small">
                  {t("setup.localFound", { n: local.models.length })}{" "}
                  <button type="button" className="linkish" onClick={() => pick("ollama")}>
                    {t("setup.localUse")}
                  </button>
                </p>
              )}
            </div>
          )}
          <div className="field">
            <label htmlFor="setup-llm">{t("setup.llmPick")}</label>
            <select
              id="setup-llm"
              className="input"
              value={chosen}
              onChange={(e) => pick(e.target.value)}
            >
              <option value="" disabled>
                {t("setup.llmChoose")}
              </option>
              {llms.map((p) => (
                <option key={p.id} value={p.id}>
                  {providerName(p, lang)}
                </option>
              ))}
            </select>
          </div>
          {guide && <p className="small setup-guide">{guide}</p>}
          {!provider ? null : provider.id === "ollama" && editable !== false ? (
            // 本机模型不要 Key：检测到 Ollama 就直接选模型一键使用，没检测到就说怎么装。
            <div className="setup-local">
              {local === null ? (
                <p className="muted small">{t("setup.localChecking")}</p>
              ) : !local.running ? (
                <p className="hint warn small">
                  {t("setup.localMissing")}{" "}
                  <button type="button" className="linkish" onClick={detectLocal}>
                    {t("setup.localRetry")}
                  </button>
                </p>
              ) : local.models.length === 0 ? (
                <p className="hint warn small">
                  {t("setup.localNoModel")}{" "}
                  <button type="button" className="linkish" onClick={detectLocal}>
                    {t("setup.localRetry")}
                  </button>
                </p>
              ) : (
                <div className="setup-key">
                  <label className="sr-only" htmlFor="setup-local-model">
                    {t("setup.localModel")}
                  </label>
                  <select
                    id="setup-local-model"
                    className="input"
                    value={localModel}
                    onChange={(e) => setLocalModel(e.target.value)}
                  >
                    {local.models.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                  <button type="button" className="btn primary" disabled={!localModel || state !== ""} onClick={useLocal}>
                    {state === "saving" ? t("setup.saving") : state === "testing" ? t("setup.testing") : t("setup.localUse")}
                  </button>
                </div>
              )}
            </div>
          ) : editable === false || needsMore ? (
            <p className="hint warn small">
              {editable === false ? t("setup.notEditable") : t("setup.needsMore")} <a href="#/settings">{t("nav.status")} →</a>
            </p>
          ) : (
            <div className="setup-key">
              <label className="sr-only" htmlFor="setup-key">
                {t("setup.keyLabel")}
              </label>
              <input
                id="setup-key"
                className="input"
                type="password"
                autoComplete="off"
                placeholder={t("setup.keyPlaceholder")}
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
              <button type="button" className="btn primary" disabled={!key.trim() || state !== ""} onClick={save}>
                {state === "saving" ? t("setup.saving") : state === "testing" ? t("setup.testing") : t("setup.save")}
              </button>
            </div>
          )}
          {result && <p className={`hint small ${result.ok ? "" : "error"}`}>{result.ok ? t("setup.ok") : result.message}</p>}
          <p className="small">
            {provider?.docs_url && (
              <>
                <a href={provider.docs_url} target="_blank" rel="noreferrer">
                  {t("settings.docs")}
                </a>
                {" · "}
              </>
            )}
            <a href="#/settings">{t("setup.allServices")}</a>
          </p>
        </div>
        <div className="setup-option">
          <h3>{t("setup.free")}</h3>
          <p className="muted small">{t("setup.freeWhy")}</p>
          <div className="setup-free-actions">
            <a className="btn" href="#/cut">
              {t("setup.cut")}
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
