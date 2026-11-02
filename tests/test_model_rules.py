"""每家模型的提示词写法（services/model_rules.py）和按模型收紧的分镜自检。"""

from __future__ import annotations

import pytest

from shotwright.core.models import Canvas, Look, LookSubject, Project, Shot, ShotVisual
from shotwright.services import model_rules, prompting


def _project(prompt: str, *, style: str = "film " * 200, scene: str = "") -> Project:
    project = Project(title="t", canvas=Canvas(aspect="9:16"), language="zh-CN")
    project.look = Look(
        subjects=[
            LookSubject(
                code="P1",
                name="咖啡机",
                kind="product",
                appearance="哑光白色圆柱机身，高约 20 厘米，顶部浅木纹盖子，正面一个银色圆形按键",
            )
        ],
        scene=scene,
        style=style,
        avoid="其他品牌",
    )
    shot = Shot(text="一句话")
    shot.visual = ShotVisual(mode="ai", prompt=prompt, camera="push_in", link="承接上一个镜头的桌面")
    project.shots.append(shot)
    return project


def test_style_lookup_falls_back_to_default():
    assert model_rules.style_for("kling").id == "kling"
    assert model_rules.style_for("kling-extra").id == "default"
    assert model_rules.style_for(None).id == "default"
    assert model_rules.style_for("").id == "default"


def test_smaller_models_have_smaller_budgets():
    kling = model_rules.style_for("kling")
    minimax = model_rules.style_for("minimax")
    seedance = model_rules.style_for("seedance")
    assert minimax.budget < kling.budget < seedance.budget
    assert minimax.max_shot_prompt < kling.max_shot_prompt < seedance.max_shot_prompt


@pytest.mark.parametrize("provider", ["seedance", "wan", "kling", "minimax", "fal"])
def test_compose_stays_within_each_budget(provider):
    style = model_rules.style_for(provider)
    # 全片风格写得极长：不按模型整理的话，提示词会超过可灵、海螺的预算。
    project = _project("P1 放在浅色木桌中央，清晨的阳光从左侧窗户斜照进来，镜头缓慢推近。")
    text = prompting.compose(project, project.shots[0], provider_id=provider)
    assert len(text) <= style.budget


def test_essential_sections_survive_the_trim():
    # 超预算时先丢风格和场景，主体外观和禁止项要留着，否则产品和人物会对不上。
    project = _project("P1 放在桌上。", scene="场景描述 " * 300)
    text = prompting.compose(project, project.shots[0], provider_id="minimax")
    assert "哑光白色圆柱机身" in text
    assert "其他品牌" in text
    assert "画面中不出现任何文字" in text


def test_style_is_dropped_before_subjects():
    # 全片设定本身有字数上限（style 最多 600 字），所以靠很长的画面描述把总长度顶超预算。
    project = _project("P1 放在桌上，" + "阳光从窗户照进来，" * 150, style="胶片质感 " * 100)
    text = prompting.compose(project, project.shots[0], provider_id="minimax")
    assert len(text) <= model_rules.style_for("minimax").budget
    assert "视觉风格" not in text
    assert "哑光白色圆柱机身" in text


def test_one_action_hint_only_for_small_models():
    project = _project("P1 放在桌上。")
    minimax = prompting.compose(project, project.shots[0], provider_id="minimax")
    seedance = prompting.compose(project, project.shots[0], provider_id="seedance")
    assert "只表现一个连贯的动作" in minimax
    assert "只表现一个连贯的动作" not in seedance


def test_unknown_provider_keeps_everything():
    project = _project("P1 放在桌上。")
    text = prompting.compose(project, project.shots[0], provider_id=None)
    assert "视觉风格" in text and "哑光白色圆柱机身" in text


def test_fit_truncates_on_a_sentence_end():
    sections = [("body", ["第一句话。" * 50]), ("avoid", ["不要出现文字。"])]
    text = model_rules.fit(sections, budget=40)
    assert len(text) <= 40
    assert not text.endswith("第一")


def test_too_long_compares_against_the_style_limit():
    style = model_rules.style_for("minimax")
    # 恰好等于上限不算超；多一个字才算。
    assert not model_rules.too_long(style, "短" * style.max_shot_prompt)
    assert model_rules.too_long(style, "短" * (style.max_shot_prompt + 1))


def test_check_storyboard_flags_prompts_over_the_model_limit():
    from shotwright.services.storyboard import Storyboard, check_storyboard

    shot = Shot(text="一句话")
    shot.visual = ShotVisual(mode="ai", prompt="P1 " + "很长的描述" * 100, link="承接")
    board = Storyboard(title="t", look=Look(), shots=[shot])
    assert not any("建议上限" in issue for issue in check_storyboard(board, lang="zh"))
    flagged = check_storyboard(board, lang="zh", provider_id="minimax")
    assert any("建议上限" in issue and "MiniMax" in issue for issue in flagged)
