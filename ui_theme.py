# -*- coding: utf-8 -*-
"""
ui_theme.py（v3.7）
职责：仅提供浅色学习风的视觉层（CSS + 纯展示 HTML），不包含任何业务逻辑。
- 色板固定，避免纯黑、霓虹蓝、渐变底与大面积彩色；
- 正文 16px / 行高 1.7，小字 14px，标题 20px / 18px；
- 阅读区最大宽度约 900px 并居中，侧边栏浅灰，卡片 12px 圆角 + 1px 边框 + 轻阴影。
"""
from __future__ import annotations

import html

import streamlit as st


PALETTE = {
    "bg": "#FFFFFF",
    "bg_subtle": "#F8F9FA",
    "text": "#111827",
    "text_muted": "#4B5563",
    "text_faint": "#9CA3AF",
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
    "primary_soft": "#EFF6FF",
    "border": "#E5E7EB",
}


GLOBAL_CSS = """
<style>
  :root {
    --wb-bg: #FFFFFF;
    --wb-bg-subtle: #F8F9FA;
    --wb-text: #111827;
    --wb-text-muted: #4B5563;
    --wb-text-faint: #9CA3AF;
    --wb-primary: #2563EB;
    --wb-primary-hover: #1D4ED8;
    --wb-primary-soft: #EFF6FF;
    --wb-border: #E5E7EB;
    --wb-radius-btn: 8px;
    --wb-radius-card: 12px;
    --wb-shadow-card: 0 1px 2px rgba(17, 24, 39, 0.04),
                      0 10px 28px rgba(17, 24, 39, 0.045);
    color-scheme: light;
  }

  html, body, .stApp, [data-testid="stAppViewContainer"],
  button, input, textarea, select {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                 "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  }

  .stApp,
  [data-testid="stAppViewContainer"],
  [data-testid="stMain"] {
    background: var(--wb-bg);
    color: var(--wb-text);
  }

  /* 顶部平台工具条隐藏，页面自己只保留品牌与额度 */
  header[data-testid="stHeader"],
  [data-testid="stToolbar"],
  [data-testid="stStatusWidget"],
  [data-testid="stDecoration"],
  [data-testid="stSidebarCollapseButton"] {
    display: none !important;
  }

  /* 阅读区：居中、最大宽度约 900px */
  [data-testid="stMainBlockContainer"] {
    max-width: 900px;
    margin: 0 auto;
    padding: 1.35rem 1.75rem 4rem 1.75rem;
  }

  [data-testid="stSidebar"] {
    background: var(--wb-bg-subtle);
    border-right: 1px solid var(--wb-border);
  }
  [data-testid="stSidebarContent"] {
    background: var(--wb-bg-subtle);
  }
  [data-testid="stSidebarUserContent"] {
    padding: 0.85rem 0.9rem 2rem 0.9rem;
  }
  [data-testid="stSidebarNav"] {
    padding: 0.2rem 0.7rem 0.4rem 0.7rem;
  }
  [data-testid="stSidebarNavSeparator"] {
    display: none;
  }
  [data-testid="stSidebarNavLink"] {
    border-radius: 8px;
    padding: 8px 10px;
    color: var(--wb-text-muted);
  }
  [data-testid="stSidebarNavLink"]:hover {
    background: #FFFFFF;
    color: var(--wb-text);
  }
  [data-testid="stSidebarNavLink"][aria-current="page"] {
    background: var(--wb-primary-soft);
    color: var(--wb-primary);
  }
  [data-testid="stSidebarNavLink"] [data-testid="stMarkdownContainer"] p {
    font-size: 14px;
    font-weight: 600;
    line-height: 1.4;
  }

  /* 正文与标题 */
  [data-testid="stMarkdownContainer"] p,
  [data-testid="stMarkdownContainer"] li {
    color: var(--wb-text);
    font-size: 16px;
    line-height: 1.7;
  }
  [data-testid="stMarkdownContainer"] h1 {
    font-size: 20px;
    line-height: 1.35;
    font-weight: 700;
    color: var(--wb-text);
  }
  [data-testid="stMarkdownContainer"] h2 {
    font-size: 20px;
    line-height: 1.4;
    font-weight: 700;
    color: var(--wb-text);
    letter-spacing: -0.01em;
  }
  [data-testid="stMarkdownContainer"] h3 {
    font-size: 18px;
    line-height: 1.45;
    font-weight: 700;
    color: var(--wb-text);
  }
  [data-testid="stMarkdownContainer"] h4 {
    font-size: 16px;
    line-height: 1.5;
    font-weight: 700;
    color: var(--wb-text);
  }
  [data-testid="stMarkdownContainer"] a {
    color: var(--wb-primary);
    text-decoration: none;
  }
  [data-testid="stMarkdownContainer"] a:hover {
    color: var(--wb-primary-hover);
    text-decoration: underline;
  }
  strong {
    color: var(--wb-text);
    font-weight: 600;
  }

  /* 小字：14px */
  [data-testid="stCaptionContainer"] p,
  [data-testid="stCaptionContainer"],
  [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
    color: var(--wb-text-muted);
    font-size: 14px;
    line-height: 1.6;
  }

  [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
  [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] li {
    font-size: 14px;
    line-height: 1.55;
  }

  /* 侧边栏用户卡 */
  .user-chip {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
    padding: 10px 12px;
    border: 1px solid var(--wb-border);
    border-radius: var(--wb-radius-card);
    background: #FFFFFF;
  }
  .avatar {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--wb-primary);
    color: #FFFFFF;
    font-size: 14px;
    font-weight: 700;
    flex: none;
  }
  .user-name {
    color: var(--wb-text);
    font-size: 14px;
    font-weight: 600;
  }

  /* 品牌栏：产品名 + 今日剩余额度 */
  .wb-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 2px 0 16px 0;
    margin-bottom: 26px;
    border-bottom: 1px solid var(--wb-border);
  }
  .wb-brand {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    font-size: 18px;
    line-height: 1.3;
    font-weight: 700;
    letter-spacing: -0.01em;
    color: var(--wb-text);
  }
  .wb-brand-mark {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    border-radius: 9px;
    background: var(--wb-primary-soft);
    color: var(--wb-primary);
    font-size: 15px;
    flex: none;
  }
  .wb-quota {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    border: 1px solid var(--wb-border);
    border-radius: 999px;
    background: var(--wb-bg-subtle);
    color: var(--wb-text-muted);
    font-size: 14px;
    line-height: 1.4;
    white-space: nowrap;
  }
  .wb-quota strong {
    color: var(--wb-primary);
    font-weight: 700;
  }

  /* 登录页 */
  .wb-auth-head {
    text-align: center;
    padding: 10px 0 6px 0;
  }
  .wb-auth-head .wb-brand {
    font-size: 24px;
  }
  .wb-auth-sub {
    margin: 8px auto 18px auto;
    max-width: 560px;
    color: var(--wb-text-muted);
    font-size: 14px;
    line-height: 1.7;
    text-align: center;
  }
  .st-key-auth_card {
    background: #FFFFFF;
    border: 1px solid var(--wb-border) !important;
    border-radius: var(--wb-radius-card) !important;
    box-shadow: var(--wb-shadow-card);
    padding: 10px 22px 20px 22px !important;
  }

  /* 单选：登录 / 注册 做成轻量分段控件 */
  [data-testid="stRadioGroup"] {
    display: flex;
    gap: 4px;
    padding: 4px;
    background: var(--wb-bg-subtle);
    border-radius: 9px;
  }
  [data-testid="stRadioGroup"] > div {
    flex: 1;
  }
  [data-testid="stRadioOption"] {
    display: flex;
    justify-content: center;
    padding: 6px 10px;
    border-radius: 7px;
    transition: background 0.15s ease, box-shadow 0.15s ease;
  }
  [data-testid="stRadioOption"][data-selected="true"] {
    background: #FFFFFF;
    box-shadow: 0 1px 2px rgba(17, 24, 39, 0.08);
  }
  [data-testid="stRadioOption"][data-selected="true"]
  [data-testid="stMarkdownContainer"] p {
    color: var(--wb-primary);
  }
  [data-testid="stRadioOption"] > div > div:first-child {
    display: none;
  }
  [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] p {
    text-align: center;
    font-size: 14px;
    font-weight: 600;
  }

  [data-testid="stRadio"] > div[role="radiogroup"] {
    display: flex;
    gap: 4px;
    padding: 4px;
    background: var(--wb-bg-subtle);
    border-radius: 9px;
  }
  [data-testid="stRadio"] > div[role="radiogroup"] > label {
    flex: 1;
    justify-content: center;
    padding: 6px 10px;
    border-radius: 7px;
    transition: background 0.15s ease, box-shadow 0.15s ease;
  }
  [data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked) {
    background: #FFFFFF;
    box-shadow: 0 1px 2px rgba(17, 24, 39, 0.08);
  }
  [data-testid="stRadio"] [data-testid="stMarkdownContainer"] p {
    font-size: 14px;
    font-weight: 600;
  }

  /* Streamlit 表单默认带内层边框，交给外层卡片负责视觉边界 */
  [data-testid="stForm"] {
    border: 0;
    padding: 0;
  }

  /* 表单控件 */
  [data-testid="stWidgetLabel"] p,
  [data-testid="stWidgetLabel"] span {
    color: var(--wb-text);
    font-size: 14px;
    font-weight: 600;
  }
  [data-testid="stTextInput"] input,
  [data-testid="stTextArea"] textarea,
  [data-testid="stNumberInput"] input {
    color: var(--wb-text);
    background: #FFFFFF;
    border: 1px solid var(--wb-border);
    border-radius: var(--wb-radius-btn);
    font-size: 16px;
    line-height: 1.7;
  }
  [data-testid="stTextInput"] input:focus,
  [data-testid="stTextArea"] textarea:focus,
  [data-testid="stNumberInput"] input:focus {
    border-color: var(--wb-primary);
    box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
  }
  [data-testid="stTextInput"] input::placeholder,
  [data-testid="stTextArea"] textarea::placeholder {
    color: var(--wb-text-faint);
  }

  /* 按钮：8px 圆角；主按钮蓝底白字 */
  .stButton > button,
  .stFormSubmitButton > button,
  .stDownloadButton > button,
  [data-testid="stBaseButton-secondary"] {
    min-height: 38px;
    padding: 7px 14px;
    border: 1px solid var(--wb-border);
    border-radius: var(--wb-radius-btn);
    background: #FFFFFF;
    color: var(--wb-text);
    font-size: 14px;
    font-weight: 600;
    box-shadow: none;
    transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
  }
  .stButton > button:hover,
  .stFormSubmitButton > button:hover,
  .stDownloadButton > button:hover,
  [data-testid="stBaseButton-secondary"]:hover {
    border-color: #BFDBFE;
    background: var(--wb-primary-soft);
    color: var(--wb-primary-hover);
  }
  [data-testid="stBaseButton-primary"],
  [data-testid="stBaseButton-primaryFormSubmit"],
  .stFormSubmitButton > button[kind="primary"],
  .stFormSubmitButton > button[kind="primaryFormSubmit"],
  .stButton > button[kind="primary"] {
    background: var(--wb-primary);
    border-color: var(--wb-primary);
    color: #FFFFFF;
  }
  [data-testid="stBaseButton-primary"]:hover,
  [data-testid="stBaseButton-primaryFormSubmit"]:hover,
  .stFormSubmitButton > button[kind="primary"]:hover,
  .stFormSubmitButton > button[kind="primaryFormSubmit"]:hover,
  .stButton > button[kind="primary"]:hover {
    background: var(--wb-primary-hover);
    border-color: var(--wb-primary-hover);
    color: #FFFFFF;
  }
  [data-testid="stBaseButton-primary"]:disabled,
  .stButton > button:disabled {
    background: #F8F9FA;
    border-color: var(--wb-border);
    color: var(--wb-text-faint);
    cursor: not-allowed;
  }

  /* 侧边栏按钮更紧凑 */
  [data-testid="stSidebar"] .stButton > button {
    min-height: 34px;
    padding: 5px 10px;
    font-size: 13px;
  }
  [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] .stButton,
  [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] .stButton > button {
    width: 100%;
  }
  [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] .stButton > button {
    min-width: 0;
    padding: 4px 5px;
    font-size: 12px;
    white-space: nowrap;
  }
  [data-testid="stSidebar"] [data-testid="stHorizontalBlock"]
  [data-testid="stMarkdownContainer"] p {
    white-space: nowrap;
    overflow: visible;
    text-overflow: clip;
  }
  [data-testid="stSidebar"] [data-testid="stHorizontalBlock"]
  [data-testid="stMarkdownContainer"] {
    overflow: visible;
    text-overflow: clip;
  }

  /* 卡片：12px 圆角 + 1px 边框 + 轻阴影 */
  .st-key-upload_card,
  .st-key-settings_account,
  .st-key-settings_key,
  .st-key-settings_password,
  [class*="st-key-summary_card_"],
  [class*="st-key-doc_card_"] {
    background: #FFFFFF;
    border: 1px solid var(--wb-border) !important;
    border-radius: var(--wb-radius-card) !important;
    box-shadow: var(--wb-shadow-card);
    padding: 18px 20px !important;
  }

  /* 摘要卡：浅灰底 + 左侧 3px 蓝竖线 */
  [class*="st-key-summary_card_"] {
    background: var(--wb-bg-subtle) !important;
    border-left: 3px solid var(--wb-primary) !important;
  }
  .wb-suggest-label {
    margin: 14px 0 6px 0;
    color: var(--wb-text-muted);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.04em;
  }
  [class*="st-key-summary_card_"] .stButton > button {
    width: 100%;
    justify-content: flex-start;
    text-align: left;
    background: #FFFFFF;
    border-color: var(--wb-border);
    font-weight: 500;
  }
  [class*="st-key-summary_card_"] .stButton > button
  [data-testid="stMarkdownContainer"] p {
    width: 100%;
    text-align: left;
  }
  [class*="st-key-summary_card_"] .stButton > button > div,
  [class*="st-key-summary_card_"] .stButton > button > div > span,
  [class*="st-key-summary_card_"] .stButton > button
  [data-testid="stMarkdownContainer"] {
    width: 100%;
    justify-content: flex-start;
    text-align: left;
  }
  [class*="st-key-summary_card_"] .stButton > button:hover {
    background: var(--wb-primary-soft);
    border-color: #BFDBFE;
    color: var(--wb-primary-hover);
  }

  /* 上传区：虚线边框卡片 */
  [data-testid="stFileUploader"] {
    background: transparent;
  }
  [data-testid="stFileUploaderDropzone"] {
    min-height: 132px;
    padding: 24px 18px;
    background: #FFFFFF;
    border: 1.5px dashed #CBD5E1 !important;
    border-radius: var(--wb-radius-card) !important;
    transition: border-color 0.15s ease, background 0.15s ease;
  }
  [data-testid="stFileUploaderDropzone"]:hover {
    border-color: var(--wb-primary) !important;
    background: var(--wb-primary-soft);
  }
  [data-testid="stFileUploaderDropzone"] button {
    border: 1px solid var(--wb-primary);
    border-radius: var(--wb-radius-btn);
    background: var(--wb-primary);
    color: #FFFFFF;
    font-weight: 600;
  }
  [data-testid="stFileUploaderDropzone"] small,
  [data-testid="stFileUploaderDropzoneInstructions"] {
    color: var(--wb-text-faint);
  }

  /* 聊天区：用户右对齐浅蓝气泡，AI 左对齐白底卡片 */
  [data-testid="stChatMessage"] {
    display: flex;
    width: 100%;
    padding: 0 !important;
    margin: 0 0 16px 0;
    gap: 0 !important;
    background: transparent !important;
    border: 0 !important;
  }
  [data-testid="stChatMessageAvatarUser"],
  [data-testid="stChatMessageAvatarAssistant"] {
    display: none !important;
  }
  [data-testid="stChatMessageContent"] {
    max-width: 84%;
    padding: 13px 16px !important;
    border-radius: 14px;
  }
  [data-testid="stChatMessageContent"] > [data-testid="stVerticalBlock"] {
    gap: 0.35rem;
  }
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    justify-content: flex-end;
  }
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] {
    width: fit-content;
    flex: 0 1 auto;
    margin-left: auto !important;
    margin-right: 0 !important;
    background: var(--wb-primary-soft);
    border: 1px solid #DBEAFE;
    border-radius: 16px 16px 4px 16px;
  }
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [data-testid="stChatMessageContent"] {
    width: 100%;
    max-width: 100%;
    margin: 0 !important;
    background: #FFFFFF;
    border: 1px solid var(--wb-border);
    border-radius: 4px 16px 16px 16px;
    box-shadow: var(--wb-shadow-card);
  }
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [data-testid="stChatMessageContent"]::before {
    content: "AI 回答";
    display: inline-block;
    margin-bottom: 6px;
    padding: 2px 8px;
    border-radius: 999px;
    background: var(--wb-primary-soft);
    color: var(--wb-primary);
    font-size: 12px;
    font-weight: 700;
    line-height: 1.6;
  }

  /* 引用出处 pill：小圆角、可展开原文 */
  .wb-source-label {
    margin: 12px 0 2px 0;
    color: var(--wb-text-faint);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  [class*="st-key-source_pills"] [data-testid="stPopover"] > div > button,
  [class*="st-key-source_pill_"] [data-testid="stPopover"] > div > button,
  [data-testid="stPopover"] [data-testid="stPopoverButton"] {
    min-height: 26px;
    padding: 2px 9px;
    border: 1px solid #DBEAFE;
    border-radius: 999px;
    background: var(--wb-primary-soft);
    color: var(--wb-primary);
    font-size: 12px;
    font-weight: 700;
    line-height: 1.5;
  }
  [class*="st-key-source_pills"] [data-testid="stPopover"] > div > button:hover,
  [class*="st-key-source_pill_"] [data-testid="stPopover"] > div > button:hover,
  [data-testid="stPopover"] [data-testid="stPopoverButton"]:hover {
    border-color: var(--wb-primary);
    background: #DBEAFE;
    color: var(--wb-primary-hover);
  }
  [data-testid="stPopover"] [data-testid="stPopoverButton"] > div > div:last-child {
    display: none;
  }
  [data-testid="stPopover"] [data-testid="stPopoverButton"]
  [data-testid="stMarkdownContainer"] p {
    color: var(--wb-primary);
    font-size: 12px;
    font-weight: 700;
    line-height: 1.5;
  }

  /* 侧边栏多选标签：浅蓝底，避免大面积高饱和蓝 */
  [data-testid="stMultiSelect"] span[data-tag] {
    background: var(--wb-primary-soft) !important;
    border: 1px solid #DBEAFE;
    color: var(--wb-primary-hover) !important;
  }
  [data-testid="stMultiSelect"] span[data-tag] span,
  [data-testid="stMultiSelect"] span[data-tag] button {
    color: var(--wb-primary-hover) !important;
  }

  /* 输入区卡片 */
  .st-key-chat_composer {
    margin-top: 18px;
    padding: 12px 14px 8px 14px !important;
    background: #FFFFFF;
    border: 1px solid var(--wb-border) !important;
    border-radius: var(--wb-radius-card) !important;
    box-shadow: var(--wb-shadow-card);
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
  }
  .st-key-chat_composer:focus-within {
    border-color: var(--wb-primary) !important;
    box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
  }
  .st-key-chat_composer [data-testid="stTextArea"] textarea {
    border: 0;
    box-shadow: none;
    padding: 4px 2px;
    background: transparent;
    resize: vertical;
  }
  .st-key-chat_composer [data-testid="stTextArea"] textarea:focus {
    box-shadow: none;
  }

  /* 空状态 */
  .wb-empty {
    padding: 34px 20px 18px 20px;
    text-align: center;
  }
  .wb-empty-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 54px;
    height: 54px;
    margin-bottom: 12px;
    border-radius: 16px;
    background: var(--wb-primary-soft);
    color: var(--wb-primary);
    font-size: 25px;
  }
  .wb-empty h3 {
    margin: 0 0 8px 0;
    color: var(--wb-text);
    font-size: 18px;
    font-weight: 700;
  }
  .wb-empty p {
    max-width: 520px;
    margin: 0 auto;
    color: var(--wb-text-muted);
    font-size: 14px;
    line-height: 1.75;
  }
  .st-key-empty_action {
    max-width: 260px;
    margin: 16px auto 6px auto;
  }
  .st-key-empty_action a {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 9px 16px;
    border: 1px solid var(--wb-primary);
    border-radius: var(--wb-radius-btn);
    background: var(--wb-primary);
    color: #FFFFFF !important;
    font-size: 14px;
    font-weight: 600;
    text-decoration: none !important;
  }
  .st-key-empty_action a:hover {
    background: var(--wb-primary-hover);
    border-color: var(--wb-primary-hover);
  }

  /* 折叠面板 */
  [data-testid="stExpander"] details {
    background: #FFFFFF;
    border: 1px solid var(--wb-border);
    border-radius: var(--wb-radius-card);
    box-shadow: var(--wb-shadow-card);
    overflow: hidden;
  }
  [data-testid="stExpander"] summary {
    padding: 10px 14px;
    font-size: 14px;
  }

  hr {
    border-color: var(--wb-border) !important;
    margin: 1rem 0 !important;
  }
  [data-testid="stDivider"] {
    background: var(--wb-border) !important;
  }

  /* 小屏：保持可读，不被固定宽度卡住 */
  @media (max-width: 820px) {
    [data-testid="stMainBlockContainer"] {
      padding: 1rem 1rem 3rem 1rem;
    }
    .wb-topbar {
      flex-wrap: wrap;
      gap: 8px;
    }
    [data-testid="stChatMessageContent"] {
      max-width: 100%;
    }
  }
</style>
"""


def inject_theme() -> None:
    """注入全局主题样式；只影响外观，不改变任何控件行为。"""
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)


def topbar_html(
    product_name: str = "课程资料问答助手",
    quota_value: str = "—",
    quota_hint: str = "免费版每天 5 次提问、可存 3 个文档；付费版 9.9/月",
) -> str:
    """返回顶部品牌栏 HTML；所有展示文本都做 HTML 转义。"""
    name = html.escape(str(product_name))
    value = html.escape(str(quota_value))
    hint = html.escape(str(quota_hint))
    return (
        '<div class="wb-topbar">'
        f'<div class="wb-brand"><span class="wb-brand-mark">📄</span>{name}</div>'
        f'<div class="wb-quota" title="{hint}">今日剩余额度 '
        f'<strong>{value}</strong></div>'
        '</div>'
    )


def render_topbar(
    product_name: str = "课程资料问答助手",
    quota_value: str = "—",
    quota_hint: str = "免费版每天 5 次提问、可存 3 个文档；付费版 9.9/月",
) -> None:
    """渲染登录后的顶部品牌栏。"""
    st.markdown(
        topbar_html(product_name, quota_value, quota_hint),
        unsafe_allow_html=True,
    )
