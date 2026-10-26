/** 视频诉求模板：每类一份可填模板（【】里换成自己的内容）和一份填好的例子。
 *
 * 中文内容与 docs/BRIEF_TEMPLATES.md 保持一致。
 */

import type { VideoType } from "./types";

export interface BriefTemplate {
  template: string;
  example: string;
}

export const VIDEO_TYPES: VideoType[] = ["product_ad", "brand", "explainer", "talking", "footage"];

export const BRIEF_TEMPLATES: Record<"zh" | "en", Record<VideoType, BriefTemplate>> = {
  zh: {
    product_ad: {
      template: "用途：在【平台】投放的【新品推广 / 促销 / 详情页】广告。\n受众：【年龄、身份】，在意【痛点或需求】。\n产品：【品类名称，不写品牌名】：【颜色】【材质】【形状尺寸】，【外观特征，如 Logo 位置、包装样式】。\n卖点：按重要程度：1.【卖点一】 2.【卖点二】 3.【卖点三】。\n价格与引导：【价格或优惠】，结尾引导【下单 / 领券 / 进店】。\n开头：前 3 秒用【痛点场景 / 效果对比 / 产品特写】抓住注意力。\n场景：【使用场景，如厨房、办公桌、户外】。\n风格：画面【明亮干净 / 高级质感 / 生活化】，节奏【快 / 中等】，配音【亲切 / 专业 / 活泼】。\n必须有：【产品特写、使用过程、前后对比等】。\n不要有：【竞品、画面文字、夸大功效的表述等】。",
      example: "在抖音投放的新品推广广告。受众是 25–35 岁的上班族，早上时间紧，想快速喝上一杯好咖啡。\n产品是一台便携咖啡机：哑光白色机身，圆柱形，高约 20 厘米，顶部有一圈浅木纹盖子，正面一个银色圆形按键。\n卖点：1. 一键 3 分钟出意式浓缩；2. 充一次电能做 15 杯；3. 能放进通勤包。\n首发价 299 元，结尾引导点击购物车。\n开头 3 秒用「早上手忙脚乱赶地铁」的场景，再切到办公桌上一键出咖啡。\n场景：家里玄关、地铁、办公桌。画面明亮干净，节奏偏快，配音亲切有活力。\n必须有：按下按键的特写、咖啡流出的特写、放进包里的动作。\n不要有：其他品牌的咖啡杯、画面里的文字、「全网最好」之类的说法。",
    },
    brand: {
      template: "用途：在【官网 / 发布会 / 社交媒体】播放的品牌片。\n受众：【合作方 / 潜在客户 / 大众消费者】。\n品牌：【做什么的，不写品牌名】，核心理念是【一句话理念】。\n要传达：1.【价值观或主张】 2.【实力或背书，如工艺、团队、历程】 3.【对用户的承诺】。\n情绪：看完让人感到【温暖 / 信赖 / 振奋 / 高级】。\n叙事：【时间线 / 人物故事 / 产品诞生过程 / 用户群像】。\n人物与场景：【人物外观、服装】，场景【工厂、工作室、城市、自然等】。\n风格：画面【电影感 / 纪实 / 极简】，色调【暖 / 冷 / 自然】，节奏【舒缓 / 递进】，音乐【钢琴 / 弦乐 / 电子】。\n结尾：落在【理念画面 / Logo 定格】。\n必须有：【……】。\n不要有：【……】。",
      example: "在官网首页和发布会播放的品牌片。受众是潜在的企业客户。\n我们是一家做手工皮具的工作室，理念是「慢一点，用得久一点」。\n要传达三件事：坚持手工缝制；每件皮具都可以终身保养；十年只做一件事。\n看完让人感到可信、踏实。\n叙事以一位老匠人一天的工作为线：清晨开门、裁皮、手缝、打磨，最后交到客户手里。\n匠人约 60 岁，灰白短发，深蓝色围裙，戴老花镜。场景是一间有木桌和暖光台灯的老工作室。\n画面纪实电影感，暖色调，节奏舒缓，配钢琴音乐。\n结尾落在成品皮包放在木桌上的静物画面。\n必须有：双手缝线的特写。\n不要有：现代流水线机器、画面里的文字。",
    },
    explainer: {
      template: "用途：在【平台】发布的科普短视频。\n受众：【小白 / 有基础的爱好者 / 学生】，现在对【主题】的误解或困惑是【……】。\n主题：讲清楚【一个具体问题，越小越好】。\n要点：按讲解顺序：1.【先抛出问题或反常识现象】 2.【原理一】 3.【原理二】 4.【结论或实用建议】。\n讲法：用【生活类比 / 动画示意 / 实拍演示】，避免【公式、术语堆砌】。\n画面：【实拍场景 / 示意动画 / 图表】，主体是【……】。\n风格：语气【轻松 / 严谨】，节奏【中等】，配音【清晰、语速适中】。\n必须有：【关键示意画面，如截面图、前后对比】。\n不要有：【错误示意、恐吓性画面、画面文字】。",
      example: "在小红书发布的科普短视频。受众是刚开始自己做饭的年轻人，很多人以为不粘锅越贵越好、可以用钢丝球刷。\n主题：不粘锅为什么会「粘」，怎么用才能用得久。\n讲解顺序：1. 开头先展示一口新锅煎蛋不粘、半年后却粘锅的对比；2. 涂层是怎么起作用的（用「荷叶上的水珠」类比）；3. 高温空烧和硬物刮擦怎样损伤涂层；4. 三条实用建议：中小火、木铲或硅胶铲、软布清洗。\n画面以厨房实拍为主，原理部分用简单的示意动画。\n语气轻松，节奏中等，配音清晰、语速适中。\n必须有：新锅和旧锅煎蛋的对比画面。\n不要有：着火、冒浓烟这类吓人的画面，画面里的文字。",
    },
    talking: {
      template: "用途：在【平台】发布的口播视频，目的是【种草 / 观点分享 / 教学 / 答疑】。\n受众：【……】。\n主讲人：【性别、年龄感、发型、服装】，状态【亲切 / 专业 / 幽默】。\n观点：一句话观点：【……】。\n内容：1.【开场钩子，一句话抓注意力】 2.【论点或步骤一】 3.【论点或步骤二】 4.【总结与互动，如提问、引导关注】。\n场景：主讲人在【书房 / 门店 / 户外 / 纯色背景】。\n插入画面：讲到【……】时插入【产品特写 / 示意画面 / 实拍素材】。\n风格：口语化，句子短，节奏【快 / 中等】，背景音乐【轻 / 无】。\n必须有：【……】。\n不要有：【……】。",
      example: "在视频号发布的口播视频，分享观点。受众是想开始健身但总坚持不下来的人。\n主讲人是 30 岁左右的女性，黑色马尾，灰色运动套装，状态亲切、有说服力。\n观点：健身坚持不下来，不是因为意志力差，而是一开始定的目标太大。\n内容：1. 开场：「你是不是办了健身卡，只去了三次？」 2. 目标太大会让人一开始就有压力；3. 第一个月只定一个小目标，比如每周走路三次、每次 20 分钟；4. 结尾提问：「你最长坚持过多久？评论区说说。」\n场景：明亮的客厅，背景有一张瑜伽垫和绿植。\n讲到小目标时，插入散步、拉伸的画面。\n口语化，句子短，节奏中等，背景音乐很轻。\n不要有：器械大重量训练画面，画面里的文字。",
    },
    footage: {
      template: "用途：把手机拍的【原片内容】做成在【平台】发布的【广告 / 展示视频】。\n原片：拍的是【主体和动作】，场景是【……】，问题是【画面杂乱 / 光线差 / 手不好看 / 背景不合适】。\n要改的：【换手 / 换背景 / 重新打光 / 统一色调】，改成【目标效果】。\n要保留的：【产品外观、Logo、动作、原声】不能变。\n产品：【颜色】【材质】【形状】，【外观特征】。\n补充镜头：原片不够时，补【产品特写 / 使用场景 / 结尾画面】。\n文案与配音：旁白讲【卖点或要点】，配音【亲切 / 专业】；原声【保留 / 压低 / 去掉】。\n风格：画面【明亮干净 / 高级质感 / 生活化】，节奏【快 / 中等】。\n不要有：【……】。",
      example: "把手机拍的护手霜试用原片做成在小红书发布的商品展示视频。\n原片拍的是在办公桌上挤出护手霜、涂在手背上，问题是桌面杂乱、灯光偏黄。\n要改的：背景换成浅米色的干净桌面，重新打成柔和的自然光。\n要保留的：护手霜管身外观和挤出、涂抹的动作不能变。\n产品是一支淡粉色软管，磨砂质感，长约 12 厘米，白色翻盖。\n原片不够时，补一个护手霜放在花束旁的特写作为结尾。\n旁白讲「不黏手、香味淡、随身带」三点，配音亲切；原声去掉。\n画面明亮干净，节奏中等。\n不要有：画面里的文字、其他护肤品。",
    },
  },
  en: {
    product_ad: {
      template: "Purpose: A [launch / promotion / product page] ad running on [platform].\nAudience: [age, who they are], who care about [pain point or need].\nProduct: [category, no brand name]: [color], [material], [shape and size], [visual details such as logo position or packaging].\nSelling points: In order of importance: 1. [point one] 2. [point two] 3. [point three].\nPrice and call to action: [price or offer]; end by asking viewers to [buy / claim a coupon / visit the store].\nOpening: Grab attention in the first 3 seconds with [a pain-point scene / before-and-after / product close-up].\nSetting: [where it is used, e.g. kitchen, desk, outdoors].\nStyle: Visuals [bright and clean / premium / everyday], pace [fast / medium], voice [friendly / professional / upbeat].\nMust include: [product close-ups, the product in use, before-and-after, etc.].\nAvoid: [competitors, on-screen text, exaggerated claims, etc.].",
      example: "A launch ad for TikTok. The audience is office workers aged 25–35 who are short on time in the morning and want a good coffee fast.\nThe product is a portable coffee maker: matte white body, cylindrical, about 20 cm tall, a light wood-grain ring on the lid, and one round silver button on the front.\nSelling points: 1. one press makes an espresso in 3 minutes; 2. one charge makes 15 cups; 3. fits in a commuter bag.\nLaunch price $39; end by asking viewers to tap the shopping cart.\nOpen with a rushed morning dash to the subway, then cut to a one-press coffee at the office desk.\nSettings: home entryway, subway, office desk. Bright and clean visuals, fairly fast pace, friendly and energetic voice.\nMust include: a close-up of pressing the button, coffee pouring out, putting it into a bag.\nAvoid: cups from other brands, on-screen text, claims like \"the best ever\".",
    },
    brand: {
      template: "Purpose: A brand film for [website / launch event / social media].\nAudience: [partners / potential clients / consumers].\nBrand: [what the company does, no brand name]; its core belief is [one sentence].\nMessage: 1. [values or stance] 2. [proof, e.g. craft, team, history] 3. [promise to customers].\nFeeling: Viewers should feel [warm / trusting / inspired / premium].\nStory: [timeline / one person's story / how the product is made / customer portraits].\nPeople and settings: [appearance and clothing]; settings [factory, studio, city, nature, etc.].\nStyle: Visuals [cinematic / documentary / minimal], tone [warm / cool / natural], pace [calm / building], music [piano / strings / electronic].\nEnding: End on [a shot that expresses the belief / the logo].\nMust include: [...].\nAvoid: [...].",
      example: "A brand film for the website homepage and a launch event, aimed at potential business clients.\nWe are a handmade leather workshop; our belief is \"slow down, make it last\".\nThree messages: every piece is hand-stitched; every piece can be cared for for life; ten years doing one thing.\nViewers should feel this is trustworthy and solid.\nThe story follows one old craftsman through his day: opening up at dawn, cutting leather, hand-stitching, polishing, and handing the piece to a customer.\nThe craftsman is about 60, with short grey hair, a dark blue apron and reading glasses. The setting is an old workshop with a wooden table and a warm desk lamp.\nCinematic documentary look, warm tones, calm pace, piano music.\nEnd on a still shot of the finished bag on the wooden table.\nMust include: a close-up of hands stitching.\nAvoid: modern assembly-line machines, on-screen text.",
    },
    explainer: {
      template: "Purpose: An explainer short for [platform].\nAudience: [beginners / enthusiasts / students]; their misconception or confusion about [topic] is [...].\nTopic: Explain [one specific question, the narrower the better].\nPoints: In order: 1. [open with a question or a surprising fact] 2. [principle one] 3. [principle two] 4. [conclusion or practical tips].\nApproach: Use [everyday analogies / simple animation / live demonstration]; avoid [formulas, jargon].\nVisuals: [live footage / diagrams / charts]; the subject is [...].\nStyle: Tone [light / rigorous], pace [medium], voice [clear, moderate speed].\nMust include: [key visuals such as a cross-section or before-and-after].\nAvoid: [misleading visuals, scary images, on-screen text].",
      example: "An explainer short for Instagram Reels. The audience is young people who have just started cooking; many think pricier non-stick pans are always better and that steel wool is fine for cleaning.\nTopic: why non-stick pans start to stick, and how to make them last.\nOrder: 1. open with a new pan frying an egg cleanly next to a six-month-old pan where it sticks; 2. how the coating works (compare it to water beading on a lotus leaf); 3. how dry heating and hard scraping damage the coating; 4. three tips: low to medium heat, wooden or silicone spatula, soft cloth for cleaning.\nMostly real kitchen footage, with simple animation for the principles.\nLight tone, medium pace, clear voice at a moderate speed.\nMust include: the new pan vs old pan egg comparison.\nAvoid: fire or heavy smoke, on-screen text.",
    },
    talking: {
      template: "Purpose: A talking-head video for [platform], to [recommend / share an opinion / teach / answer questions].\nAudience: [...].\nPresenter: [gender, apparent age, hair, clothing]; manner [friendly / professional / funny].\nPoint: One-sentence point: [...].\nContent: 1. [opening hook, one line] 2. [argument or step one] 3. [argument or step two] 4. [wrap-up and engagement, e.g. a question or follow prompt].\nSetting: The presenter is in [a study / a shop / outdoors / a plain background].\nCutaways: When talking about [...], cut to [product close-up / illustration / footage].\nStyle: Conversational, short sentences, pace [fast / medium], background music [light / none].\nMust include: [...].\nAvoid: [...].",
      example: "A talking-head video for YouTube Shorts sharing an opinion. The audience is people who want to work out but never stick with it.\nThe presenter is a woman around 30 with a black ponytail and a grey tracksuit, friendly and convincing.\nPoint: you don't quit because of weak willpower, but because your first goal was too big.\nContent: 1. Hook: \"Bought a gym membership and went three times?\" 2. A big goal creates pressure from day one; 3. For the first month, set one small goal, like walking three times a week for 20 minutes; 4. End with a question: \"What's the longest you've kept it up? Tell me in the comments.\"\nSetting: a bright living room with a yoga mat and plants in the background.\nWhen talking about small goals, cut to walking and stretching shots.\nConversational, short sentences, medium pace, very light background music.\nAvoid: heavy weightlifting, on-screen text.",
    },
    footage: {
      template: "Purpose: Turn phone footage of [what was filmed] into a [ad / showcase video] for [platform].\nFootage: It shows [subject and action] in [setting]; the problem is [cluttered / poor lighting / unattractive hands / wrong background].\nChange: [replace hands / replace background / relight / unify color] to get [target look].\nKeep: [product appearance, logo, action, original sound] must not change.\nProduct: [color], [material], [shape], [visual details].\nExtra shots: If the footage isn't enough, add [product close-up / usage scene / ending shot].\nScript and voice: The narration covers [selling points]; voice [friendly / professional]; original sound [keep / lower / remove].\nStyle: Visuals [bright and clean / premium / everyday], pace [fast / medium].\nAvoid: [...].",
      example: "Turn phone footage of a hand cream test into a product showcase for Instagram.\nThe footage shows hand cream being squeezed out and rubbed onto the back of a hand on an office desk; the desk is cluttered and the light is yellowish.\nChange: replace the background with a clean beige tabletop and relight with soft natural light.\nKeep: the tube's appearance and the squeezing and rubbing actions must not change.\nThe product is a pale pink soft tube, matte finish, about 12 cm long, with a white flip cap.\nIf the footage isn't enough, add a close-up of the hand cream next to a bouquet as the ending.\nThe narration covers three points: not sticky, light scent, easy to carry; friendly voice; remove the original sound.\nBright and clean visuals, medium pace.\nAvoid: on-screen text, other skincare products.",
    },
  },
};

/** 模板里还没填的占位：中文【…】或英文 [...] 开头的字段说明。 */
export function unfilledSlots(text: string): number {
  const zh = text.match(/【[^】]*】/g)?.length ?? 0;
  const en = text.match(/\[[^\]\n]{1,60}\]/g)?.length ?? 0;
  return zh + en;
}

const APPEARANCE_WORDS =
  /颜色|色|材质|外观|形状|款式|包装|尺寸|厘米|发型|头发|穿|服装|套装|围裙|戴|colou?r|material|shape|matte|packag|\d\s?cm\b|inch|hair|ponytail|wear|outfit|apron|dress|shirt|suit|glasses|white|black|gr[ae]y|red|blue|green|pink|brown|beige|silver|gold/i;

/** 需要写清主体外观的视频类型（科普的主体常是抽象概念，不强求）。 */
const NEEDS_APPEARANCE: VideoType[] = ["product_ad", "brand", "talking", "footage"];

export interface BriefIssues {
  unfilled: number;
  tooShort: boolean;
  noAppearance: boolean;
}

/** 提交前检查诉求：只提醒，用户可以忽略继续。 */
export function checkBrief(text: string, type: VideoType | null): BriefIssues {
  const plain = text.replace(/\s+/g, "");
  return {
    unfilled: unfilledSlots(text),
    tooShort: plain.length < 50,
    noAppearance: type !== null && NEEDS_APPEARANCE.includes(type) && !APPEARANCE_WORDS.test(text),
  };
}

export function hasIssues(issues: BriefIssues): boolean {
  return issues.unfilled > 0 || issues.tooShort || issues.noAppearance;
}
