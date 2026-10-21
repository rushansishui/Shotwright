import { useEffect, useState } from "react";
import { api } from "../api";
import { providerName, useI18n } from "../i18n";
import type { MessageKey } from "../messages";
import type { Providers } from "../types";


export function Settings() {
  const [providers, setProviders] = useState<Providers | null>(null);
  const [error, setError] = useState("");
  const { t, lang } = useI18n();
  const kindName = (kind: string) =>
    ["llm", "tts", "stock", "video_gen", "image_gen"].includes(kind) ? t(`status.kind.${kind}` as MessageKey) : kind;

  useEffect(() => {
    api.providers().then(setProviders).catch((e) => setError((e as Error).message));
  }, []);

  return (
    <main className="page narrow">
      <section className="card">
        <div className="card-head">
          <h1>{t("status.title")}</h1>
        </div>
        <p className="muted">{t("status.intro")}</p>
        {error && <p className="hint error">{error}</p>}
        {providers &&
          Object.entries(providers).map(([kind, list]) => (
            <div key={kind} className="provider-group">
              <h2>{kindName(kind)}</h2>
              <table className="table">
                <thead>
                  <tr>
                    <th>{t("status.service")}</th>
                    <th>{t("status.state")}</th>
                    <th>{t("status.fields")}</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((p) => (
                    <tr key={p.id}>
                      <td>
                        {p.docs_url ? (
                          <a href={p.docs_url} target="_blank" rel="noreferrer">
                            {providerName(p, lang)}
                          </a>
                        ) : (
                          providerName(p, lang)
                        )}
                        {p.default && <span className="tag">{t("status.default")}</span>}
                        {p.paid && <span className="tag warn">{t("status.paid")}</span>}
                      </td>
                      <td>{p.configured ? <span className="ok">{t("status.ok")}</span> : <span className="bad">{t("status.missing")}</span>}</td>
                      <td className="muted">
                        {p.fields.length === 0
                          ? t("status.none")
                          : p.fields.map((f) => `${f.label}${f.required ? t("status.required") : ""}`).join(t("status.sep"))}
                        {p.fields.length > 0 && (
                          <div>
                            <code>[providers.{kind}.{p.id}]</code>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </section>
    </main>
  );
}
