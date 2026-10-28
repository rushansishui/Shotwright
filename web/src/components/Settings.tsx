import { useEffect, useState } from "react";
import { api } from "../api";
import { providerName, useI18n } from "../i18n";
import type { MessageKey } from "../messages";
import type { ProviderInfo, Providers, SettingsData } from "../types";

const KINDS = ["llm", "tts", "stock", "video_gen", "image_gen"];

/**
 * 服务设置：直接在页面里填写各服务的地址、模型和 Key，保存即写回 config.toml 并立即生效。
 * 密钥不会回传到页面，只显示是否已填和后 4 位；能测试的服务可以一键测试连接。
 */
export function Settings() {
  const [providers, setProviders] = useState<Providers | null>(null);
  const [data, setData] = useState<SettingsData | null>(null);
  const [error, setError] = useState("");
  const [proxy, setProxy] = useState("");
  const [proxyState, setProxyState] = useState<"" | "saving" | "saved">("");
  const { t, lang } = useI18n();
  const kindName = (kind: string) => (KINDS.includes(kind) ? t(`status.kind.${kind}` as MessageKey) : kind);

  function reload() {
    return Promise.all([api.providers(), api.settings()])
      .then(([p, s]) => {
        setProviders(p);
        setData(s);
        setProxy(s.proxy);
      })
      .catch((e) => setError((e as Error).message));
  }

  useEffect(() => {
    reload();
  }, []);

  async function applied(next: SettingsData) {
    setData(next);
    setProviders(await api.providers());
  }

  async function saveProxy() {
    setProxyState("saving");
    setError("");
    try {
      await applied(await api.saveAppSettings(proxy));
      setProxyState("saved");
    } catch (e) {
      setError((e as Error).message);
      setProxyState("");
    }
  }

  async function chooseDefault(kind: string, provider: string) {
    setError("");
    try {
      await applied(await api.saveDefaultProvider(kind, provider));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const editable = !!data?.editable;

  return (
    <main className="page narrow settings-page">
      <section className="card">
        <div className="card-head">
          <h1>{t("status.title")}</h1>
        </div>
        <p className="muted">{t("settings.intro")}</p>
        {data && (
          <p className="muted small">
            {editable ? t("settings.file", { path: data.config_path }) : t("settings.readonly")}
          </p>
        )}
        {error && <p className="hint error">{error}</p>}
        {data && (
          <div className="settings-proxy">
            <label className="field">
              <span className="label">{t("settings.proxy")}</span>
              <input
                className="input"
                value={proxy}
                disabled={!editable}
                placeholder={t("settings.proxyPlaceholder")}
                onChange={(e) => {
                  setProxy(e.target.value);
                  setProxyState("");
                }}
              />
            </label>
            <button
              type="button"
              className="btn"
              disabled={!editable || proxy === data.proxy || proxyState === "saving"}
              onClick={saveProxy}
            >
              {proxyState === "saving" ? t("settings.saving") : t("settings.save")}
            </button>
            {proxyState === "saved" && <span className="ok small">{t("settings.saved")}</span>}
          </div>
        )}
      </section>

      {providers &&
        data &&
        Object.entries(providers).map(([kind, list]) => {
          const choosable = list.filter((p) => p.fields.length > 0 || p.offline);
          return (
            <section key={kind} className="card provider-group">
              <div className="card-head">
                <h2>{kindName(kind)}</h2>
                <label className="settings-default">
                  <span className="muted small">{t("settings.defaultFor")}</span>
                  <select
                    className="input"
                    value={data.defaults[kind] ?? ""}
                    disabled={!editable}
                    onChange={(e) => chooseDefault(kind, e.target.value)}
                  >
                    {!data.defaults[kind] && <option value="">{t("settings.notChosen")}</option>}
                    {choosable.map((p) => (
                      <option key={p.id} value={p.id}>
                        {providerName(p, lang)}
                        {p.configured ? "" : ` · ${t("status.missing")}`}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {list.map((p) => (
                <ProviderCard
                  key={p.id}
                  kind={kind}
                  provider={p}
                  saved={data.providers[kind]?.[p.id]?.fields ?? {}}
                  editable={editable}
                  onSaved={applied}
                />
              ))}
            </section>
          );
        })}
    </main>
  );
}

interface ProviderCardProps {
  kind: string;
  provider: ProviderInfo;
  saved: Record<string, { set: boolean; value: string }>;
  editable: boolean;
  onSaved: (data: SettingsData) => Promise<void>;
}

function ProviderCard({ kind, provider, saved, editable, onSaved }: ProviderCardProps) {
  const { t, lang } = useI18n();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [cleared, setCleared] = useState<Record<string, boolean>>({});
  const [state, setState] = useState<"" | "saving" | "saved" | "testing">("");
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [error, setError] = useState("");

  const changes: Record<string, string> = {};
  for (const field of provider.fields) {
    if (cleared[field.key]) changes[field.key] = "";
    else if (field.key in draft) {
      const value = draft[field.key].trim();
      const current = field.secret ? "" : saved[field.key]?.value ?? "";
      if (field.secret ? value !== "" : value !== current) changes[field.key] = value;
    }
  }
  const dirty = Object.keys(changes).length > 0;

  function startEdit() {
    setDraft(Object.fromEntries(provider.fields.filter((f) => !f.secret).map((f) => [f.key, saved[f.key]?.value ?? ""])));
    setCleared({});
    setResult(null);
    setError("");
    setOpen(true);
  }

  async function save() {
    setState("saving");
    setError("");
    try {
      await onSaved(await api.saveProviderSettings(kind, provider.id, changes));
      setDraft((d) => Object.fromEntries(Object.entries(d).filter(([key]) => !provider.fields.find((f) => f.key === key)?.secret)));
      setCleared({});
      setState("saved");
    } catch (e) {
      setError((e as Error).message);
      setState("");
    }
  }

  async function test() {
    setState("testing");
    setResult(null);
    setError("");
    try {
      setResult(await api.testProvider(kind, provider.id));
    } catch (e) {
      setError((e as Error).message);
    }
    setState("");
  }

  const inputId = `setting-${kind}-${provider.id}`;
  const labelOf = (key: string) => provider.fields.find((f) => f.key === key)?.label ?? key;

  return (
    <div className={`provider-card ${open ? "open" : ""}`} data-provider={`${kind}.${provider.id}`}>
      <div className="provider-row">
        <span className="provider-name">
          {providerName(provider, lang)}
          {provider.paid && <span className="tag warn">{t("status.paid")}</span>}
          {provider.default && <span className="tag">{t("status.default")}</span>}
        </span>
        <span className="provider-state">
          {provider.configured ? <span className="ok">{t("status.ok")}</span> : <span className="bad">{t("status.missing")}</span>}
        </span>
        <span className="provider-actions">
          {provider.docs_url && (
            <a href={provider.docs_url} target="_blank" rel="noreferrer" className="small">
              {t("settings.docs")}
            </a>
          )}
          {provider.fields.length > 0 && (
            <button type="button" className="btn small" onClick={() => (open ? setOpen(false) : startEdit())}>
              {open ? t("settings.collapse") : t("settings.edit")}
            </button>
          )}
        </span>
      </div>

      {open && (
        <div className="provider-form">
          {provider.required_any && provider.required_any.length > 1 && (
            <p className="muted small">
              {t("settings.anyOf", {
                groups: provider.required_any.map((g) => g.map(labelOf).join(" + ")).join(lang === "zh" ? " 或 " : " or "),
              })}
            </p>
          )}
          {provider.fields.map((field) => {
            const current = saved[field.key];
            const placeholder =
              field.secret && current?.set
                ? t("settings.secretSet", { masked: current.value })
                : field.default
                  ? t("settings.defaultValue", { value: field.default })
                  : "";
            return (
              <div key={field.key} className="field">
                <label htmlFor={`${inputId}-${field.key}`}>
                  {field.label}
                  {field.required && <span className="muted">{t("status.required")}</span>}
                  <code className="muted small"> {field.key}</code>
                </label>
                <span className="provider-input">
                  <input
                    className="input"
                    id={`${inputId}-${field.key}`}
                    type={field.secret ? "password" : "text"}
                    autoComplete="off"
                    disabled={!editable || cleared[field.key]}
                    value={cleared[field.key] ? "" : draft[field.key] ?? ""}
                    placeholder={cleared[field.key] ? t("settings.cleared") : placeholder}
                    onChange={(e) => {
                      setDraft((d) => ({ ...d, [field.key]: e.target.value }));
                      setState("");
                    }}
                  />
                  {current?.set && editable && (
                    <label className="check small">
                      <input
                        type="checkbox"
                        checked={!!cleared[field.key]}
                        onChange={(e) => setCleared((c) => ({ ...c, [field.key]: e.target.checked }))}
                      />
                      {t("settings.clear")}
                    </label>
                  )}
                </span>
                {field.help && <small className="muted">{field.help}</small>}
              </div>
            );
          })}
          <div className="provider-foot">
            <button type="button" className="btn primary" disabled={!editable || !dirty || state === "saving"} onClick={save}>
              {state === "saving" ? t("settings.saving") : t("settings.save")}
            </button>
            {provider.testable && (
              <button type="button" className="btn" disabled={dirty || state === "testing"} onClick={test}>
                {state === "testing" ? t("settings.testing") : t("settings.test")}
              </button>
            )}
            {dirty && <span className="muted small">{t("settings.unsaved")}</span>}
            {state === "saved" && !dirty && <span className="ok small">{t("settings.saved")}</span>}
          </div>
          {result && <p className={`hint small ${result.ok ? "ok" : "error"}`}>{result.message}</p>}
          {error && <p className="hint error small">{error}</p>}
        </div>
      )}
    </div>
  );
}
