"""宣传片的分镜：每个镜头的口播、屏幕上的小标题，以及画面怎么来。

画面类型（``kind``）：

* ``grid``：把演示项目中英文两个版本的成片并排拼成一屏；
* ``scene``：网页动画做的动态画面（痛点、片尾），``action`` 是 scenes.py 里的场景名；
* ``record``：自动操作界面并录屏，``action`` 是 recorder.py 里的动作名。

改口播只改这里，录屏会按新的配音时长重新对齐。口播里只写界面上真实存在的功能。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromoShot:
    kind: str
    """grid / scene / record。"""
    text: str
    """口播（也是字幕）。"""
    title: str = ""
    """镜头期间显示在画面上的小标题。"""
    action: str = ""
    """kind 为 record 时是录屏动作，为 scene 时是场景名（pain / outro）。"""


SHOTS: dict[str, list[PromoShot]] = {
    "zh": [
        PromoShot("grid", "这条广告的中文版和英文版，来自同一份脚本。", "一份脚本 → 两种语言"),
        PromoShot(
            "scene",
            "做 AI 广告，常常要在好几个工具之间来回切，生成翻车就重来，钱花了也不知道花在哪。",
            action="pain",
        ),
        PromoShot("record", "在 Shotwright 里，贴上脚本，它会自动拆成一个个镜头。", "脚本 → 分镜", "script"),
        PromoShot(
            "record",
            "每个镜头自己选画面：素材库、AI 视频、自己拍的都行，产品照参考图保持一致。",
            "每个镜头，自己选画面",
            "shots",
        ),
        PromoShot(
            "record",
            "花钱之前，先检查容易翻车的描述；先用便宜的图片定画面，满意了再生成视频。",
            "先检查，再花钱",
            "precheck",
        ),
        PromoShot(
            "record", "改一句话、换一个镜头，只重做这一处，不用从头再来。", "只重做改动的镜头", "rerender"
        ),
        PromoShot("record", "一次出多种语言，直接拿去投放测试。", "一键多语言版本", "versions"),
        PromoShot(
            "record",
            "每一笔花费都看得见，模型随你选，项目和素材都存在你自己电脑上。",
            "费用透明 · 模型任选 · 本地运行",
            "settings",
        ),
        PromoShot("scene", "Shotwright 镜匠，开源免费，在 GitHub 上下载。", action="outro"),
    ],
    "en": [
        PromoShot(
            "grid",
            "The Chinese and English versions of this ad came from one script.",
            "One script, two languages",
        ),
        PromoShot(
            "scene",
            "Making AI ads usually means juggling tools, re-rolling failed shots, and losing track of what you spent.",
            action="pain",
        ),
        PromoShot(
            "record",
            "In Shotwright, paste your script and it becomes a storyboard, shot by shot.",
            "Script to storyboard",
            "script",
        ),
        PromoShot(
            "record",
            "Each shot picks its own source: stock, AI video or your own footage, with your product kept on-model.",
            "Every shot, its own source",
            "shots",
        ),
        PromoShot(
            "record",
            "Before you pay, it flags prompts that tend to fail, and lets you lock the look with cheap stills first.",
            "Check first, then spend",
            "precheck",
        ),
        PromoShot(
            "record",
            "Change a line or a shot, and only that part re-renders.",
            "Only changed shots re-render",
            "rerender",
        ),
        PromoShot(
            "record",
            "Then ship it in every language you need, ready to test.",
            "Language versions in one click",
            "versions",
        ),
        PromoShot(
            "record",
            "Every cost is visible, you choose the models, and your projects stay on your own machine.",
            "Clear costs · Any model · Runs locally",
            "settings",
        ),
        PromoShot("scene", "Shotwright. Open source and free. Get it on GitHub.", action="outro"),
    ],
}

DEMO_SCRIPT = {
    "zh": "这几款儿童羽绒服也太好看了吧！浅粉色、米白色、蓝色、黑色四色可选。100到170全尺码覆盖，多大娃都合身。居家、上学、出去玩穿都保暖舒服。喜欢的快进店挑合适的款式下单呀。",
    "en": "These kids' down jackets are so cute! Light pink, off-white, blue and black. Sizes 100 to 170, so every kid fits. Warm and cozy at home, at school and at the park. Love them? Pick your favorite in our store.",
}
"""录屏「贴上脚本」那一段里粘贴的演示脚本。"""
