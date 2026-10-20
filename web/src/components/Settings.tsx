import { useEffect, useState } from "react";
import { api } from "../api";
import type { Providers } from "../types";

const KIND_NAMES: Record<string, string> = {
  llm: "大模型",
  tts: "配音",
  stock: "素材库",
  video_gen: "AI 视频生成",
};

export function Settings() {
  const [providers, setProviders] = useState<Providers | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.providers().then(setProviders).catch((e) => setError((e as Error).message));
  }, []);

  return (
    <main className="page narrow">
      <section className="card">
        <div className="card-head">
          <h1>服务状态</h1>
        </div>
        <p className="muted">
          密钥只保存在服务器的 config.toml 或环境变量中，不会在这里显示，也不会写进项目文件。修改配置后需要重启服务。
        </p>
        {error && <p className="hint error">{error}</p>}
        {providers &&
          Object.entries(providers).map(([kind, list]) => (
            <div key={kind} className="provider-group">
              <h2>{KIND_NAMES[kind] ?? kind}</h2>
              <table className="table">
                <thead>
                  <tr>
                    <th>服务</th>
                    <th>状态</th>
                    <th>需要的配置</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((p) => (
                    <tr key={p.id}>
                      <td>
                        {p.docs_url ? (
                          <a href={p.docs_url} target="_blank" rel="noreferrer">
                            {p.name}
                          </a>
                        ) : (
                          p.name
                        )}
                        {p.default && <span className="tag">默认</span>}
                        {p.paid && <span className="tag warn">收费</span>}
                      </td>
                      <td>{p.configured ? <span className="ok">可用</span> : <span className="bad">未配置</span>}</td>
                      <td className="muted">
                        {p.fields.length === 0
                          ? "无"
                          : p.fields.map((f) => `${f.label}${f.required ? "（必填）" : ""}`).join("、")}
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
