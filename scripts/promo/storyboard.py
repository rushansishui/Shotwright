"""宣传片的分镜：每个镜头的口播、屏幕上的小标题，以及画面怎么来。

有两个版本（``--edition``）：

* ``short``：1–2 分钟的宣传片，按卖点快速过一遍；
* ``long``：3–5 分钟的功能讲解，分章节演示，适合发 YouTube、B 站。

画面类型（``kind``）：

* ``grid``：把演示项目两种语言版本的成片并排拼成一屏；
* ``scene``：网页动画做的动态画面（开场介绍、痛点、章节卡、片尾），``action`` 是 scenes.py 里的场景名；
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
    """kind 为 record 时是录屏动作，为 scene 时是场景名（intro / pain / chapter / outro）。"""
    card: str = ""
    """章节卡上的大字（scene 为 chapter 时用），也作为视频简介里的章节名。"""


def chapter(number: int, card: str, text: str) -> PromoShot:
    return PromoShot("scene", text, action="chapter", card=f"{number:02d}|{card}")


# ---- 宣传片（short）------------------------------------------------------------

SHORT: dict[str, list[PromoShot]] = {
    "zh": [
        PromoShot("grid", "这条广告的中文版和英文版，来自同一份脚本。", "一份脚本 → 两种语言"),
        PromoShot(
            "scene",
            "做 AI 广告，常常要在好几个工具之间来回切，生成翻车就重来，钱花了也不知道花在哪。",
            action="pain",
        ),
        PromoShot("record", "在 Shotwright 里，贴上脚本，它会自动拆成一个个镜头。", "脚本 → 分镜", "script"),
        PromoShot("record", "分镜表一眼看清全片：画面、口播、运镜和花费都在一张表里。", "分镜表", "board"),
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
        PromoShot("record", "发给模型的完整提示词看得见，也能整段直接改。", "完整提示词", "prompts"),
        PromoShot(
            "record", "改一句话、换一个镜头，只重做这一处，不用从头再来。", "只重做改动的镜头", "rerender"
        ),
        PromoShot("record", "已有的口播视频也能剪：在文稿里删字，视频跟着剪。", "按说的话剪视频", "cut"),
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
            "The storyboard table shows the whole film at once: visuals, voiceover, camera and cost.",
            "Storyboard table",
            "board",
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
            "See the full prompt each model will get, and edit it directly if you want.",
            "Full prompts, editable",
            "prompts",
        ),
        PromoShot(
            "record",
            "Change a line or a shot, and only that part re-renders.",
            "Only changed shots re-render",
            "rerender",
        ),
        PromoShot(
            "record",
            "It edits talking videos too: delete words in the transcript and the video follows.",
            "Edit video by text",
            "cut",
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


# ---- 功能讲解（long）------------------------------------------------------------

LONG: dict[str, list[PromoShot]] = {
    "zh": [
        PromoShot(
            "grid", "这两条广告，一条中文、一条英文，来自同一份脚本，画面完全共用。", "一份脚本 → 两种语言"
        ),
        PromoShot(
            "scene",
            "Shotwright 镜匠，是一个开源、在你自己电脑上运行的 AI 短视频工具。它把视频拆成一个个镜头："
            "每个镜头单独选画面、单独重做，配音、字幕和音乐自动完成。",
            action="intro",
        ),
        PromoShot(
            "scene",
            "做 AI 广告，常常要在好几个工具之间来回切，生成翻车只能重抽，钱花出去了也说不清花在哪。"
            "下面用一条商品广告，从头走一遍。",
            action="pain",
        ),
        chapter(1, "从想法到分镜", "第一步，从想法到分镜。"),
        PromoShot(
            "record",
            "新建视频时，用几句话说想法。AI 先判断视频类型和时长，再按模板整理成完整诉求；"
            "不知道的事实标成待补充，留给你来填。",
            "说想法 → 完整诉求",
            "idea",
        ),
        PromoShot(
            "record",
            "接着写出逐段口播脚本，可以改字、调顺序、增删段落。定稿后生成分镜：文案一字不改，AI 只负责配画面。",
            "脚本定稿 → 分镜",
            "script",
        ),
        PromoShot(
            "record",
            "镜头多了就切到分镜表，一张表看清全片：画面、口播、画面描述、运镜、来源和已经花的钱，"
            "都能直接改，还能导出表格。",
            "分镜表：一眼看清全片",
            "board",
        ),
        chapter(2, "每个镜头，自己选画面", "第二步，每个镜头自己选画面。"),
        PromoShot(
            "record",
            "先在素材这一步备好主体：产品的几种颜色、人物的照片，都作为参考图。"
            "生成 AI 画面时带上它们，每个镜头里都是同一件衣服、同一个人。",
            "参考图：人物、产品前后一致",
            "look",
        ),
        PromoShot(
            "record",
            "画面来源每个镜头自己选：素材库检索、AI 视频、AI 图片、上传自己拍的，或者纯色。"
            "AI 视频可以选不同厂家，切换来源也不会丢掉已经生成的素材。",
            "素材库 · AI 视频 · AI 图片 · 自己拍的",
            "shots",
        ),
        chapter(3, "先检查，再花钱", "第三步，先检查，再花钱。"),
        PromoShot(
            "record",
            "生成之前，它会检查画面描述里容易翻车的写法，给出改法，也可以让 AI 按建议改写；"
            "还能先出三张便宜的关键帧，定好画面再生成视频。",
            "生成前检查",
            "precheck",
        ),
        PromoShot(
            "record",
            "每个镜头最终发给模型的完整提示词都能看到，参考图编号、主体外观、禁止项一目了然；"
            "需要的话，整段手动改，原样提交。",
            "完整提示词，可以直接改",
            "prompts",
        ),
        PromoShot(
            "record",
            "每笔花费都有记录，项目累计花了多少一目了然。可以设费用上限，也可以先出一版不花钱的整片样片，"
            "看节奏和文案。",
            "花费看得见",
            "cost",
        ),
        chapter(4, "改一处，只重做一处", "第四步，改一处，只重做一处。"),
        PromoShot(
            "record",
            "改一句文案，只有这个镜头重新配音、重新渲染，其余镜头直接复用，不用从头再来。",
            "只重做改动的镜头",
            "rerender",
        ),
        PromoShot(
            "record",
            "审片页把每个镜头的候选版本排在一起：点一下选用，逐个标记通过或重做，重做时写一句修改意见就行。",
            "审片：通过还是重做",
            "review",
        ),
        chapter(5, "配音、字幕、音乐", "第五步，配音、字幕和音乐。"),
        PromoShot(
            "record",
            "配音有多家可选，多音字可以纠正读音；字幕按真实字宽断行；背景音乐在说话时自动压低，"
            "整片响度统一；标题、卖点和价格文字也在这里加。",
            "配音 · 字幕 · 音乐 · 标题",
            "audio",
        ),
        chapter(6, "剪辑已有视频", "第六步，剪辑已有的视频。"),
        PromoShot(
            "record",
            "已有的口播视频，导入后在本机识别说的话，原片不出电脑。在文稿里删词，视频跟着剪；"
            "一键勾掉语气词，撤销重做、查找替换都有。",
            "按说的话剪视频",
            "cut",
        ),
        chapter(7, "多语言与交付", "最后，多语言和交付。"),
        PromoShot(
            "record",
            "一键翻译出其他语言版本，画面共用，只重新配音、换字幕，直接拿去不同市场投放测试。",
            "一键多语言版本",
            "versions",
        ),
        PromoShot(
            "record",
            "文字、图片、视频和配音模型都能换不同厂家，用你自己的 API Key；项目和素材都存在你自己的电脑上。",
            "模型任选 · 本地运行",
            "settings",
        ),
        PromoShot(
            "scene",
            "Shotwright 镜匠，开源免费，有 Windows 安装包。在 GitHub 上搜索 Shotwright 就能下载。",
            action="outro",
        ),
    ],
    "en": [
        PromoShot(
            "grid",
            "These two ads, one in Chinese and one in English, came from the same script and share every shot.",
            "One script, two languages",
        ),
        PromoShot(
            "scene",
            "Shotwright is an open source AI video tool that runs on your own computer. It breaks a video into shots: "
            "each shot gets its own visuals and can be redone on its own, while voice, subtitles and music are handled "
            "for you.",
            action="intro",
        ),
        PromoShot(
            "scene",
            "Making AI ads usually means juggling tools, re-rolling failed shots, and losing track of what you spent. "
            "Let's walk through one product ad from start to finish.",
            action="pain",
        ),
        chapter(1, "From idea to storyboard", "Part one: from idea to storyboard."),
        PromoShot(
            "record",
            "Start with a few sentences about your idea. The AI picks the video type and length, then turns it into "
            "a full brief, and marks any facts it doesn't know so you can fill them in.",
            "Idea to brief",
            "idea",
        ),
        PromoShot(
            "record",
            "Next it writes the voiceover line by line. Edit, reorder, add or remove lines. Then build the "
            "storyboard: your words stay exactly as written, and the AI only plans the visuals.",
            "Final script to storyboard",
            "script",
        ),
        PromoShot(
            "record",
            "With many shots, switch to the storyboard table and see the whole film at once: visuals, voiceover, "
            "descriptions, camera, source and spend. Edit in place, or export it.",
            "The whole film in one table",
            "board",
        ),
        chapter(2, "Every shot, its own source", "Part two: every shot picks its own visuals."),
        PromoShot(
            "record",
            "First, set up your subjects: each colour of the product and photos of the people become reference "
            "images. AI shots use them, so it's the same jacket and the same person in every shot.",
            "References keep it on-model",
            "look",
        ),
        PromoShot(
            "record",
            "Each shot picks its own source: stock search, AI video, AI image, your own footage, or a solid colour. "
            "Choose a different AI provider per shot, and switching never throws away what you've generated.",
            "Stock · AI video · AI image · Your footage",
            "shots",
        ),
        chapter(3, "Check first, then spend", "Part three: check first, then spend."),
        PromoShot(
            "record",
            "Before generating, it flags descriptions that tend to fail and suggests fixes, or lets the AI rewrite "
            "them. You can also make three cheap keyframes first, and only generate video once the look is right.",
            "Checks before you pay",
            "precheck",
        ),
        PromoShot(
            "record",
            "See the exact prompt each shot sends to the model, with reference numbers, subject looks and things to "
            "avoid. If you need to, edit the whole prompt and it's sent as is.",
            "Full prompts, editable",
            "prompts",
        ),
        PromoShot(
            "record",
            "Every charge is logged, so you always know what the project has cost. Set a budget, or make a free "
            "sample cut first to check pacing and wording.",
            "Costs you can see",
            "cost",
        ),
        chapter(4, "Change one thing, redo one thing", "Part four: change one thing, redo only that."),
        PromoShot(
            "record",
            "Change a line, and only that shot is re-voiced and re-rendered. Everything else is reused, so there's "
            "no starting over.",
            "Only changed shots re-render",
            "rerender",
        ),
        PromoShot(
            "record",
            "The review page lines up every shot's takes. Pick one, mark each shot approved or redo, and add a short "
            "note for anything to change.",
            "Review: approve or redo",
            "review",
        ),
        chapter(5, "Voice, subtitles, music", "Part five: voice, subtitles and music."),
        PromoShot(
            "record",
            "Choose from several voice providers and fix tricky pronunciations. Subtitles wrap by real text width, "
            "music ducks under speech, loudness is evened out, and titles, selling points and prices go here too.",
            "Voice · Subtitles · Music · Titles",
            "audio",
        ),
        chapter(6, "Edit existing videos", "Part six: edit videos you already have."),
        PromoShot(
            "record",
            "Import a talking video and speech is transcribed on your machine, so the footage never leaves it. "
            "Delete words and the video follows. Remove filler words in one click, with undo, redo and find and "
            "replace.",
            "Edit video by text",
            "cut",
        ),
        chapter(7, "Languages and delivery", "Finally, languages and delivery."),
        PromoShot(
            "record",
            "Make other language versions in one click. The visuals are shared; only the voice and subtitles change, "
            "ready to test in new markets.",
            "Language versions in one click",
            "versions",
        ),
        PromoShot(
            "record",
            "Swap text, image, video and voice models between providers, using your own API keys. Your projects "
            "and footage stay on your own computer.",
            "Any model · Runs locally",
            "settings",
        ),
        PromoShot(
            "scene",
            "Shotwright. Open source and free, with a Windows installer. Search for Shotwright on GitHub to get it.",
            action="outro",
        ),
    ],
}

EDITIONS: dict[str, dict[str, list[PromoShot]]] = {"short": SHORT, "long": LONG}
SHOTS = SHORT
"""旧名字：默认的宣传片分镜。"""

DEMO_SCRIPT = {
    "zh": "这几款儿童羽绒服也太好看了吧！浅粉色、米白色、蓝色、黑色四色可选。100到170全尺码覆盖，多大娃都合身。居家、上学、出去玩穿都保暖舒服。喜欢的快进店挑合适的款式下单呀。",
    "en": "These kids' down jackets are so cute! Light pink, off-white, blue and black. Sizes 100 to 170, so every kid fits. Warm and cozy at home, at school and at the park. Love them? Pick your favorite in our store.",
}
"""录屏「贴上脚本」那一段里粘贴的演示脚本。"""

DEMO_IDEA = {
    "zh": "给 3 到 10 岁孩子的轻薄羽绒服做条抖音广告，四种颜色，主打保暖、耐脏、好活动，结尾引导进店下单。",
    "en": "A TikTok ad for light down jackets for kids aged 3 to 10. Four colours, warm, easy to clean, "
    "easy to move in. End by asking viewers to order in our store.",
}
"""录屏「说想法」那一段里输入的想法。"""

DEMO_BRIEF = {
    "zh": (
        "用途：在抖音投放的新品推广广告。\n"
        "受众：25–40 岁的宝爸宝妈，在意孩子冬天出门冻着、衣服容易脏。\n"
        "产品：儿童轻薄羽绒服：浅粉色、米白色、蓝色、黑色四色，防泼面料，宽松直筒，长度及臀。\n"
        "卖点：按重要程度：1. 填充足量羽绒，保暖 2. 外层防泼耐脏，一擦就干净 3. 宽松不臃肿，跑跳不受束缚。\n"
        "价格与引导：【待补充：价格或优惠】，结尾引导进店下单。\n"
        "开头：前 3 秒用孩子在户外冻得喊冷、衣服蹭脏的场景抓住注意力。\n"
        "场景：冬天的小区户外、家里玄关、上学路上。\n"
        "风格：画面明亮温暖，节奏中等，配音亲切活泼。\n"
        "必须有：面料特写、孩子跑跳的画面、四种颜色的展示。\n"
        "不要有：竞品、画面文字、夸大保暖功效的表述。"
    ),
    "en": (
        "Purpose: a launch ad running on TikTok.\n"
        "Audience: parents aged 25–40 who worry about kids getting cold and dirty outdoors in winter.\n"
        "Product: a light kids' down jacket in light pink, off-white, blue and black; splash-proof shell, "
        "relaxed straight cut, hip length.\n"
        "Selling points, most important first: 1. Plenty of down, very warm 2. Splash-proof shell wipes clean "
        "3. Relaxed fit, free to run and play.\n"
        "Price and call to action: [To fill in: price or offer], end by asking viewers to order in the store.\n"
        "Opening: in the first 3 seconds, a kid outdoors complaining about the cold, jacket smeared with dirt.\n"
        "Settings: a residential courtyard in winter, the front door at home, the way to school.\n"
        "Style: bright and warm, medium pace, friendly lively voice.\n"
        "Must include: fabric close-ups, kids running and jumping, all four colours.\n"
        "Avoid: competitors, on-screen text, exaggerated warmth claims."
    ),
}
"""「说想法」那一段里 AI 整理出的诉求。录屏时拦下整理诉求的请求、直接返回这一段：
不调用大模型、不花钱，每次录出来都一样。界面和流程都是真实的。"""
