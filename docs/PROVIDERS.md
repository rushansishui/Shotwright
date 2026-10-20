# 新增一个 Provider

以新增一个素材库为例，其他类别（`llm`、`tts`、`video_gen`）步骤相同，只是实现的协议不同（见 `shotwright/providers/base.py`）。

## 1. 新建文件

`shotwright/providers/stock/pixabay.py`：

```python
from pathlib import Path

from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderError, ProviderSpec, StockCandidate


class PixabayStock:
    def __init__(self, settings: dict[str, str]):
        self.api_key = settings["api_key"]   # 必填字段已由注册表校验
        self.proxy = settings.get("proxy")   # 全局代理会自动传入

    def search(self, query: str, *, aspect: str, min_duration: float, limit: int) -> list[StockCandidate]:
        ...  # 调用接口，失败时抛 ProviderError（消息展示给用户，不能包含密钥）

    def download(self, candidate: StockCandidate, output: Path) -> Path:
        ...  # 下载到 output（可改扩展名），返回实际路径


registry.register(
    ProviderSpec(
        kind="stock",
        id="pixabay",
        name="Pixabay",
        fields=(ConfigField("api_key", "API Key", required=True, secret=True),),
        docs_url="https://pixabay.com/api/docs/",
    ),
    PixabayStock,
)
```

## 2. 登记模块

在 `shotwright/providers/registry.py` 的 `_BUILTIN_MODULES` 中加一行：

```python
"shotwright.providers.stock.pixabay",
```

## 3. 配置

```toml
[providers.stock.pixabay]
api_key = "..."
```

如需设为默认，在 `[defaults]` 中写 `stock = "pixabay"`；也可以只在某个镜头的 `visual.provider` 中指定。

## 4. 测试

参考 `tests/conftest.py` 中的 `FakeStock`：用离线的假实现测试上层逻辑，真实接口的解析逻辑用录制好的响应 JSON 做单元测试，不要在测试里访问网络。

## 约定

- 网络请求使用 `httpx`，设置超时，支持 `proxy`。
- 下载要限制文件大小。
- 收费服务设置 `paid=True`。
- 只把可公开的信息（作者、来源页面、授权）放进 `StockCandidate.meta`。
