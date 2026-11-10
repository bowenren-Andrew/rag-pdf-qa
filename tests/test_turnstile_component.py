# -*- coding: utf-8 -*-
"""Turnstile 自定义组件：容器 id 遮蔽全局 API 的回归测试。

浏览器会把带 id 的元素暴露成 window.<id>。容器 id 若叫 turnstile，
window.turnstile 就是那个 <div>，脚本会误以为 Cloudflare API 已加载，
随后调用不存在的 window.turnstile.render，界面永远停在“正在加载人机验证…”。
"""
from pathlib import Path

COMPONENT_HTML = (
    Path(__file__).resolve().parents[1] / "components" / "turnstile" / "index.html"
)


def test_容器id不能叫turnstile():
    html = COMPONENT_HTML.read_text(encoding="utf-8")

    assert 'id="turnstile-widget"' in html
    assert 'id="turnstile"' not in html


def test_只认真正的Turnstile_API():
    html = COMPONENT_HTML.read_text(encoding="utf-8")

    assert 'typeof window.turnstile.render === "function"' in html
    assert 'window.turnstile.render(WIDGET_SELECTOR' in html


def test_从Cloudflare官方地址加载脚本():
    html = COMPONENT_HTML.read_text(encoding="utf-8")

    assert "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit" in html