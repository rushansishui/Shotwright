"""本地渲染基准（services/bench_local.py）：能跑、能算出复用率、报告可读。"""

from __future__ import annotations

import json

from shotwright.services import bench_local, sample


def test_plan_shots_repeats_scenes_with_unique_text():
    """镜头数超过一轮时循环场景，但文案必须各不相同，否则会命中同一份缓存。"""
    plan = sample._plan_shots(sample.SHOTS["zh"], 12, "zh-CN")
    assert len(plan) == 12
    assert len({item.text for item in plan}) == 12


def test_plan_shots_keeps_default_five():
    assert len(sample._plan_shots(sample.SHOTS["zh"], None, "zh-CN")) == 5


def test_plan_shots_trims_when_asked_for_fewer():
    assert len(sample._plan_shots(sample.SHOTS["en"], 2, "en-US")) == 2


def _report() -> dict:
    return {
        "started_at": 0,
        "machine": {"platform": "test", "cpu_count": 4, "python": "3.12"},
        "ffmpeg": "ffmpeg version test",
        "quality": "final",
        "language": "zh-CN",
        "shots": 12,
        "build_seconds": 1.0,
        "summary": {
            "cold_seconds": 20.0,
            "warm_seconds": 5.0,
            "speedup": 4.0,
            "warm_reuse_ratio": 0.83,
            "warm_shots_rendered": 1,
            "warm_stage_seconds": {"final": 3.0, "assembly": 1.5, "shot": 0.4},
            "peak_rss_mb": 200.0,
            "shots": 12,
            "video_seconds": 40.0,
        },
        "cold": {
            "label": "冷启动",
            "seconds": 20.0,
            "stages": 26,
            "reused": 0,
            "redone": ["assembly", "final", "shot", "voice"],
            "reuse_ratio": 0.0,
            "shots_rendered": 12,
            "duration": 40.0,
        },
        "warm": [
            {
                "label": "改镜头 1",
                "seconds": 5.0,
                "stages": 26,
                "reused": 22,
                "redone": ["assembly", "final", "shot", "voice"],
                "reuse_ratio": 0.846,
                "shots_rendered": 1,
                "duration": 40.0,
            }
        ],
        "video": "sample.mp4",
        "video_bytes": 1024,
    }


def test_format_report_has_conclusion_and_reproduce_steps():
    text = bench_local.format_report(_report())
    assert "改一个镜头" in text
    assert "只重渲 **1/12** 个镜头" in text
    assert "比整片重来快 **4.0 倍**" in text
    # 时间花在哪：按耗时从大到小列出来。
    assert "改一镜的 5.0 秒里：final 3.0 秒、assembly 1.5 秒、shot 0.4 秒" in text
    assert "怎么复现" in text
    assert "bench --local" in text


def test_format_report_shows_cold_variance():
    """跑多次冷启动时，报告要写清楚波动范围，读者才知道该怎么看。"""
    data = _report()
    data["summary"].update({"cold_min": 24.0, "cold_max": 26.0, "cold_spread_pct": 8.0})
    data["cold_runs"] = [data["cold"], data["cold"], data["cold"]]
    text = bench_local.format_report(data)
    assert "3 次取中位数" in text
    assert "24.0–26.0 秒" in text


def test_run_local_bench_reuses_most_stages(settings, store, tmp_path):
    report = bench_local.run_local_bench(
        store,
        settings,
        tmp_path / "bench",
        quality="draft",
        shots=3,
        runs=1,
        edits=1,
        progress=lambda *_: None,
    )
    assert report.cold.seconds > 0
    assert report.median_warm > 0
    # 只改了一个镜头：大部分阶段应当直接复用。
    warm = report.warm[0]
    assert warm.reused > 0
    assert warm.reused < warm.stages
    assert report.peak_rss_mb is None or report.peak_rss_mb > 0

    data = json.loads((tmp_path / "bench" / "report.json").read_text(encoding="utf-8"))
    assert data["summary"]["speedup"] > 0
    assert (tmp_path / "bench" / "sample.mp4").is_file()
    assert (tmp_path / "bench" / "report.md").is_file()
    # 默认不保留示例项目，跑完就删。
    assert store.list() == []


def _minimal_report(*, cold_shots: int = 3, warm_shots: int = 1, warm_reused: int = 5):
    cold = bench_local.RunResult(
        label="冷启动 1", seconds=10.0, stages=8, reused=0, shots_rendered=cold_shots
    )
    warm = bench_local.RunResult(
        label="改镜头 1", seconds=4.0, stages=8, reused=warm_reused, shots_rendered=warm_shots
    )
    return bench_local.LocalBenchReport(
        started_at=0,
        machine={},
        ffmpeg="",
        quality="draft",
        language="zh-CN",
        shots=3,
        build_seconds=1.0,
        cold_runs=[cold],
        warm=[warm],
        peak_rss_mb=None,
        video="sample.mp4",
        video_bytes=1,
    )


def test_check_report_passes_a_healthy_run():
    assert bench_local.check_report(_minimal_report()) == []


def test_check_report_flags_a_lost_cache():
    problems = bench_local.check_report(_minimal_report(warm_shots=3, warm_reused=0))
    assert any("只重渲 1 个" in problem for problem in problems)
    assert any("没有任何阶段复用" in problem for problem in problems)
