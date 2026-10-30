import { useEffect, useState } from "react";
import { api } from "../api";
import { providerName, useI18n } from "../i18n";
import type { Providers } from "../types";

/**
 * 首次启动向导：还没配置大模型时显示在首页最上面。
 * 两条路：选一家大模型服务填 Key（不偏向任何一家），或者先免费试试（自己写口播脚本、剪辑已有视频）。
 * 保存后立刻测试连接，成功就刷新服务状态，卡片自动消失。
 */
export function SetupCard({
  providers,
  onConfigured,
  onWriteOwn,
}: {
  providers: Providers;
  onConfigured: () => void;
  onWriteOwn: () => void;
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

  useEffect(() => {
    api
      .settings()
      .then((data) => setEditable(data.editable))
      .catch(() => setEditable(false));
  }, []);

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
          <div className="field">
            <label htmlFor="setup-llm">{t("setup.llmPick")}</label>
            <select
              id="setup-llm"
              className="input"
              value={chosen}
              onChange={(e) => {
                setChosen(e.target.value);
                setResult(null);
              }}
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
          {!provider ? null : editable === false || needsMore ? (
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
            <button type="button" className="btn" onClick={onWriteOwn}>
              {t("setup.writeOwn")}
            </button>
            <a className="btn" href="#/cut">
              {t("setup.cut")}
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
