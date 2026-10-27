#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""狀態根金鑰櫃:一行一隻 NAME=value,產品只走 secret(NAME)。

不叫 secrets.py:Python 標準庫已有 secrets,同名會蓋掉它。
值不得出現在 print、log、例外訊息。落地密文前綴 enc.v1.;讀取時解密。
缺值且收集開關開著時,走社群請人用 LINE 填入(needs_fill)。
"""
from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600
OPEN_MASK = 0o077  # group/other 任一位置位=太寬

MISSING_FILE = "missing_file"
MISSING_LINE = "missing_line"
PERM_TOO_OPEN = "perm_too_open"
DISABLED = "disabled"
TOKEN_SHORT = "token_short"
NEEDS_CONFIRM = "needs_confirm"
OUTSIDE_WINDOW = "outside_window"
NEEDS_FILL = "needs_fill"

README_TEXT = (
    "金鑰只放這一棵目錄,不進版控、日誌、dm、回報、交接、人格卡。\n"
    "社群、直播、遊戲一個平台一個檔;其他類型一個類型一個檔。\n"
    "一行一隻 NAME=value,行名全大寫;多帳號加代號不另開檔;多行私鑰先編成一行 base64。\n"
    "落地值以 enc.v1. 加密;wrap.key 只留在本目錄。缺值時由 LINE 填入再加密寫回。\n"
    "index.json 只記用途與輪替日,不記值。權限必須是目錄 0700、檔 0600。\n"
)

# (相對路徑, 平台, 類型, 檔頭註解, ((NAME, 用途, 消費者, 功能, 第一批), ...))
LAYOUT = (
    ("ez2.env", "ez2", "type",
     "Ez2 本體:授權與本機配對鍵。私鑰只留這裡,公鑰才出門。",
     (("EZ2_LICENSE", "授權(base64)", "ez2", "license", False),
      ("EZ2_PAIRING_ED25519_PRIVATE", "配對私鑰(base64)", "support-intake", "support-intake", True),
      ("EZ2_PAIRING_ED25519_PUBLIC", "配對公鑰", "support-intake", "support-intake", True),
      ("EZ2_DEMO_TOKEN", "示範 token(由舊 demo-token.txt 搬入)", "ez2", "demo", False))),
    # AI API key 一引擎一檔：api-key-<provider>.env（the maintainer 2026-09-19）。
    # 各家 CLI 的 OAuth 登入仍不放這裡。舊 ai.env 若還在，讀取端可手動搬，不再是 LAYOUT 正本。
    ("api-key-claude.env", "claude", "type",
     "Claude／Anthropic 雲端 API key。一行 ANTHROPIC_API_KEY=…",
     (("ANTHROPIC_API_KEY", "Anthropic API", "claude", "ai-api", False),)),
    ("api-key-codex.env", "codex", "type",
     "Codex／OpenAI 雲端 API key。一行 OPENAI_API_KEY=…",
     (("OPENAI_API_KEY", "OpenAI API", "codex", "ai-api", False),)),
    ("api-key-grok.env", "grok", "type",
     "Grok／xAI 雲端 API key。一行 XAI_API_KEY=…",
     (("XAI_API_KEY", "xAI API", "grok", "ai-api", False),)),
    ("api-key-agy.env", "agy", "type",
     "AGY／Google Gemini 雲端 API key。一行 GOOGLE_API_KEY=…",
     (("GOOGLE_API_KEY", "Google / Gemini API", "agy", "ai-api", False),)),
    ("api-key-copilot.env", "copilot", "type",
     "GitHub Copilot token。一行 GITHUB_COPILOT_TOKEN=…",
     (("GITHUB_COPILOT_TOKEN", "GitHub Copilot", "copilot", "ai-api", False),)),
    ("api-key-deepseek.env", "deepseek", "type",
     "DeepSeek 雲端 API key（終端席 auth；非 OAuth）。一行 DEEPSEEK_API_KEY=…",
     (("DEEPSEEK_API_KEY", "DeepSeek API", "deepseek", "ai-api", False),)),
    ("api-key-nvidia.env", "nvidia", "type",
     "NVIDIA NIM 雲端 API key（OpenAI-compat 終端席；非 OAuth）。",
     (("NVIDIA_API_KEY", "NVIDIA API key（build.nvidia.com / NGC）", "nvidia", "ai-api", False),
      ("NVIDIA_BASE_URL", "NVIDIA base URL（可空=https://integrate.api.nvidia.com；也可寫 …/v1）", "nvidia", "ai-api", False),)),
    ("api-key-openrouter.env", "openrouter", "type",
     "OpenRouter API-key seat (OpenAI-compat; https://openrouter.ai).",
     (("OPENROUTER_API_KEY", "OpenRouter API key（dashboard sk-or-…）", "openrouter", "ai-api", False),
      ("OPENROUTER_BASE_URL", "OpenAI-compat base（可空=https://openrouter.ai/api/v1）", "openrouter", "ai-api", False),
      ("OPENROUTER_HTTP_REFERER", "可選 HTTP-Referer（站點排名）", "openrouter", "ai-api", False),
      ("OPENROUTER_APP_TITLE", "可選 X-Title（站點名稱）", "openrouter", "ai-api", False),)),
    ("api-key-typesafe.env", "typesafe", "type",
     "TypeSafe System One API-key seat (native; https://api.typesafe.ai).",
     (("TYPESAFE_API_KEY", "TypeSafe API key（dashboard）", "typesafe", "ai-api", False),
      ("TYPESAFE_BASE_URL", "System One base（可空=https://api.typesafe.ai）", "typesafe", "ai-api", False),)),
    ("push.env", "push", "type",
     "瀏覽器 Web Push。VAPID 私鑰用來簽名,與公鑰成對。",
     (("WEBPUSH_VAPID_PUBLIC", "VAPID 公鑰", "push", "push", False),
      ("WEBPUSH_VAPID_PRIVATE", "VAPID 私鑰", "push", "push", False),
      ("WEBPUSH_VAPID_SUBJECT", "VAPID subject(mailto: 或 https:)", "push", "push", False))),
    ("grokbot.env", "grokbot", "type",
     "AIB↔Cursor Grok Bot 橋接喚醒。例行工作 AIB bridge push wake 的 webhook。",
     (("GROKBOT_WAKE_URL", "Grok Bot Webhook URL", "sand-bridge", "bridge-wake", False),
      ("GROKBOT_WAKE_KEY", "Grok Bot Webhook sender key", "sand-bridge", "bridge-wake", False))),
    ("codexbot.env", "codexbot", "type",
     "AIB↔Cursor DotsBot 橋接喚醒（之後自啟）。",
     (("CODEXBOT_WAKE_URL", "DotsBot Webhook URL", "sand-bridge", "bridge-wake", False),
      ("CODEXBOT_WAKE_KEY", "DotsBot Webhook sender key", "sand-bridge", "bridge-wake", False))),
    ("services.env", "services", "type",
     "第三方服務。Deepgram 從舊 secrets/deepgram.env 搬入。",
     (("DEEPGRAM_API_KEY", "Deepgram 語音轉寫", "transcribe", "transcribe", False),)),
    ("backup.env", "backup", "type",
     "遠端備份。未開備份可留空。",
     (("BACKUP_ENDPOINT", "備份端點", "backup", "backup", False),
      ("BACKUP_KEY_ID", "備份金鑰 ID", "backup", "backup", False),
      ("BACKUP_SECRET", "備份密鑰", "backup", "backup", False))),
    ("dev.env", "dev", "type",
     "開發機。SSH 只記 ~/.ssh 裡的檔名,不把私鑰貼進來。客戶版可無此檔。",
     (("GITHUB_TOKEN", "GitHub 個人 token", "dev", "dev", False),
      ("SSH_HOST_EU01_KEY", "~/.ssh 檔名(EU01)", "dev", "dev", False),
      ("SSH_HOST_SIN01_KEY", "~/.ssh 檔名(SIN01)", "dev", "dev", False))),
    ("payment.env", "payment", "type",
     "收款。定 provider 後才填。",
     (("STRIPE_SECRET_KEY", "Stripe 密鑰", "payment", "payment", False),
      ("STRIPE_WEBHOOK_SECRET", "Stripe webhook 簽名", "payment", "payment", False))),
    # 10-21 @ee4f8(PROPOSAL-gaps E):原本沒有 Cloudflare 類,`aib secret ask CF_WORKER_TOKEN` 回 unknown_name。
    # CF_ / CLOUDFLARE_ 開頭的名字都落這一檔;伺服器專用的 token 用 --remote-only 送,不佔這格。
    # 母鑰 CF_TOKEN_MINTER 不准放這裡(SE_ONLY,@abb89 審 #222):本機檔同帳號 AI 讀得到 ⇒ 只放 Secure Enclave 封存。
    ("cloudflare.env", "cloudflare", "type",
     "Cloudflare。只放給本機工具用的一般 token;不放 global key,也不放 CF_TOKEN_MINTER(那把只進 Secure Enclave)。",
     (("CF_API_TOKEN", "Cloudflare API token(限權限的一般 token)", "cf-tools", "cf-tools", False),)),
    # 10-24 @ee4f8(Andy「彈出一個視窗讓我設定key」):WordPress 站(FluentSMTP)寄信用的 Amazon SES 金鑰。
    # 只送伺服器(--remote-only),本機不存:這把只在站上用,放本機檔 = 同帳號 AI 讀得到又多一份。
    ("fluentmail.env", "fluentmail", "type",
     "FluentSMTP(WordPress 寄信)的 Amazon SES 金鑰。只准 --remote-only 送到伺服器,本機不存。",
     (("FLUENTMAIL_AWS_ACCESS_KEY_ID", "SES access key ID(IAM 只給 ses:SendRawEmail)", "wp-mail", "wp-mail", False),
      ("FLUENTMAIL_AWS_SECRET_ACCESS_KEY", "SES secret access key", "wp-mail", "wp-mail", False))),
    ("social/line.env", "line", "social",
     "LINE 官方帳號。在 LINE Developers 的 Channel 頁複製。多帳號:LINE_SHOP2_CHANNEL_SECRET。",
     (("LINE_CHANNEL_SECRET", "Messaging API 頻道密鑰", "support-intake", "support-intake", True),
      ("LINE_CHANNEL_ACCESS_TOKEN", "Messaging API 頻道 token", "support-intake", "support-intake", True),
      ("LINE_LOGIN_CHANNEL_ID", "LINE Login 頻道 ID", "line-login", "line-login", True),
      ("LINE_LOGIN_CHANNEL_SECRET", "LINE Login 頻道密鑰", "line-login", "line-login", True))),
    ("social/whatsapp.env", "whatsapp", "social",
     "WhatsApp Cloud API。在 Meta 開發者後台的 WhatsApp 產品頁。",
     (("WHATSAPP_ACCESS_TOKEN", "雲端 API token", "whatsapp", "whatsapp", True),
      ("WHATSAPP_PHONE_NUMBER_ID", "電話編號 ID", "whatsapp", "whatsapp", True),
      ("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "Webhook 驗證字", "whatsapp", "whatsapp", True))),
    ("social/telegram.env", "telegram", "social",
     "Telegram Bot。跟 @BotFather 要 token。",
     (("TELEGRAM_BOT_TOKEN", "Bot token", "telegram", "telegram", True),)),
    ("social/meta.env", "meta", "social",
     "Meta App(Messenger / Instagram / Threads / WhatsApp 共用)。",
     (("META_APP_ID", "Meta App ID", "meta", "meta", True),
      ("META_APP_SECRET", "Meta App 密鑰", "meta", "meta", True))),
    ("social/messenger.env", "messenger", "social",
     "Facebook 粉絲頁。Page token 與 Meta App 分開輪替。",
     (("MESSENGER_PAGE_ACCESS_TOKEN", "粉絲頁 token", "messenger", "messenger", True),)),
    ("social/instagram.env", "instagram", "social",
     "Instagram 專業帳號。",
     (("INSTAGRAM_ACCESS_TOKEN", "IG token", "instagram", "instagram", False),)),
    ("social/threads.env", "threads", "social",
     "Threads。",
     (("THREADS_ACCESS_TOKEN", "Threads token", "threads", "threads", False),)),
    ("social/x.env", "x", "social",
     "X (Twitter) API。",
     (("X_API_KEY", "API key", "x", "x", False),
      ("X_API_SECRET", "API secret", "x", "x", False),
      ("X_ACCESS_TOKEN", "Access token", "x", "x", False),
      ("X_ACCESS_SECRET", "Access secret", "x", "x", False),
      ("X_BEARER_TOKEN", "Bearer", "x", "x", False))),
    ("social/youtube.env", "youtube", "social",
     "YouTube Data API。OAuth 用戶端 JSON 請編成一行 base64。",
     (("YOUTUBE_OAUTH_CLIENT", "OAuth 用戶端 JSON(base64)", "youtube", "youtube", True),
      ("YOUTUBE_REFRESH_TOKEN", "OAuth refresh", "youtube", "youtube", True),
      ("YOUTUBE_API_KEY", "公開 API key", "youtube", "youtube", True))),
    ("social/douyin.env", "douyin", "social",
     "抖音開放平台(中國大陸)。與國際版 TikTok 分開。",
     (("DOUYIN_CLIENT_KEY", "client key", "douyin", "douyin", True),
      ("DOUYIN_CLIENT_SECRET", "client secret", "douyin", "douyin", True))),
    ("social/tiktok.env", "tiktok", "social",
     "TikTok 國際版開放平台。",
     (("TIKTOK_CLIENT_KEY", "client key", "tiktok", "tiktok", True),
      ("TIKTOK_CLIENT_SECRET", "client secret", "tiktok", "tiktok", True))),
    ("social/linkedin.env", "linkedin", "social",
     "LinkedIn。",
     (("LINKEDIN_CLIENT_ID", "client id", "linkedin", "linkedin", False),
      ("LINKEDIN_CLIENT_SECRET", "client secret", "linkedin", "linkedin", False),
      ("LINKEDIN_ACCESS_TOKEN", "access token", "linkedin", "linkedin", False))),
    ("social/discord.env", "discord", "social",
     "Discord Bot。",
     (("DISCORD_BOT_TOKEN", "bot token", "discord", "discord", False),
      ("DISCORD_APPLICATION_ID", "application id", "discord", "discord", False))),
    ("social/slack.env", "slack", "social",
     "Slack。",
     (("SLACK_BOT_TOKEN", "bot token", "slack", "slack", False),
      ("SLACK_SIGNING_SECRET", "簽名密鑰", "slack", "slack", False))),
    ("social/wechat.env", "wechat", "social",
     "微信公眾號。",
     (("WECHAT_APPID", "AppID", "wechat", "wechat", False),
      ("WECHAT_APPSECRET", "AppSecret", "wechat", "wechat", False),
      ("WECHAT_TOKEN", "伺服器驗證 Token", "wechat", "wechat", False),
      ("WECHAT_ENCODING_AES_KEY", "消息加解密 key", "wechat", "wechat", False))),
    ("social/xiaohongshu.env", "xiaohongshu", "social",
     "小紅書開放平台。",
     (("XIAOHONGSHU_APP_KEY", "app key", "xiaohongshu", "xiaohongshu", False),
      ("XIAOHONGSHU_APP_SECRET", "app secret", "xiaohongshu", "xiaohongshu", False))),
    ("live/youtube-live.env", "youtube-live", "live",
     "YouTube 直播推流鍵,與 Data API 憑證分開。",
     (("YOUTUBE_LIVE_STREAM_KEY", "直播推流鍵", "youtube-live", "youtube-live", False),)),
    ("live/twitch.env", "twitch", "live",
     "Twitch。",
     (("TWITCH_CLIENT_ID", "client id", "twitch", "twitch", False),
      ("TWITCH_CLIENT_SECRET", "client secret", "twitch", "twitch", False),
      ("TWITCH_STREAM_KEY", "推流鍵", "twitch", "twitch", False))),
    ("live/facebook-live.env", "facebook-live", "live",
     "Facebook 直播。",
     (("FACEBOOK_LIVE_STREAM_KEY", "直播推流鍵", "facebook-live", "facebook-live", False),)),
    ("live/tiktok-live.env", "tiktok-live", "live",
     "TikTok 直播推流鍵,與開放平台憑證分開。",
     (("TIKTOK_LIVE_STREAM_KEY", "直播推流鍵", "tiktok-live", "tiktok-live", False),)),
    ("live/douyin-live.env", "douyin-live", "live",
     "抖音直播推流鍵。",
     (("DOUYIN_LIVE_STREAM_KEY", "直播推流鍵", "douyin-live", "douyin-live", False),)),
    ("live/17live.env", "17live", "live",
     "17LIVE。",
     (("SEVENTEEN_LIVE_TOKEN", "17LIVE token", "17live", "17live", False),)),
    ("live/kick.env", "kick", "live",
     "Kick。",
     (("KICK_CLIENT_ID", "client id", "kick", "kick", False),
      ("KICK_CLIENT_SECRET", "client secret", "kick", "kick", False))),
    ("live/obs.env", "obs", "live",
     "本機 OBS WebSocket。",
     (("OBS_WEBSOCKET_PASSWORD", "OBS WebSocket 密碼", "obs", "obs", False),)),
    ("games/steam.env", "steam", "games",
     "Steam Web API。",
     (("STEAM_WEB_API_KEY", "Web API key", "steam", "steam", False),
      ("STEAM_ACCOUNT_ID", "帳號 ID", "steam", "steam", False))),
    ("games/xbox.env", "xbox", "games",
     "Xbox / Microsoft。",
     (("XBOX_CLIENT_ID", "client id", "xbox", "xbox", False),
      ("XBOX_CLIENT_SECRET", "client secret", "xbox", "xbox", False))),
    ("games/playstation.env", "playstation", "games",
     "PlayStation。NPSSO 是登入 cookie,輪替快。",
     (("PLAYSTATION_NPSSO", "NPSSO", "playstation", "playstation", False),)),
    ("games/nintendo.env", "nintendo", "games",
     "Nintendo。",
     (("NINTENDO_SESSION_TOKEN", "session token", "nintendo", "nintendo", False),)),
    ("games/epic.env", "epic", "games",
     "Epic Games。",
     (("EPIC_CLIENT_ID", "client id", "epic", "epic", False),
      ("EPIC_CLIENT_SECRET", "client secret", "epic", "epic", False))),
    ("games/roblox.env", "roblox", "games",
     "Roblox。",
     (("ROBLOX_API_KEY", "API key", "roblox", "roblox", False),)),
    ("games/minecraft.env", "minecraft", "games",
     "Minecraft 伺服器 RCON。",
     (("MINECRAFT_SERVER_RCON_PASSWORD", "RCON 密碼", "minecraft", "minecraft", False),)),
)

# 已開啟功能 → 必填行。全空=未使用。有任一非空 ⇒ 其餘也要齊。
FEATURES = {
    "support-intake": ("social/line.env",
                       ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN")),
    "whatsapp": ("social/whatsapp.env",
                 ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
                  "WHATSAPP_WEBHOOK_VERIFY_TOKEN")),
    "telegram": ("social/telegram.env", ("TELEGRAM_BOT_TOKEN",)),
    "messenger": ("social/messenger.env", ("MESSENGER_PAGE_ACCESS_TOKEN",)),
    "youtube": ("social/youtube.env",
                ("YOUTUBE_OAUTH_CLIENT", "YOUTUBE_REFRESH_TOKEN")),
    "douyin": ("social/douyin.env",
               ("DOUYIN_CLIENT_KEY", "DOUYIN_CLIENT_SECRET")),
    "tiktok": ("social/tiktok.env",
               ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET")),
    "push": ("push.env", ("WEBPUSH_VAPID_PUBLIC", "WEBPUSH_VAPID_PRIVATE")),
}

CUSTOMER_FIRST_BATCH = (
    "support-intake", "whatsapp", "telegram", "messenger",
    "youtube", "douyin", "tiktok",
)


def _name_maps():
    """精確行名優先;其餘用平台前綴最長匹配(LINE_SHOP2_…、YOUTUBE_LIVE_…)。"""
    exact = {}
    prefixes = {}
    for rel, plat, _cat, _cmt, keys in LAYOUT:
        plat_prefix = plat.replace("-", "_").upper() + "_"
        prefixes.setdefault(plat_prefix, rel)
        for name, _p, _cons, _feat, _fb in keys:
            exact[name] = rel
            i = name.find("_")
            if i > 0:
                prefixes.setdefault(name[: i + 1], rel)
    return exact, tuple(sorted(prefixes.items(), key=lambda kv: (-len(kv[0]), kv[0])))


EXACT_FILES, PREFIX_TABLE = _name_maps()


# 只准放 Secure Enclave 的名字(se_vault.SE_ONLY 同一份):金鑰櫃檔案一律拒寫(@abb89 審 #222)
SE_ONLY_NAMES = ("CF_TOKEN_MINTER",)
# 只准送到伺服器的名字(aib secret ask … --push … --remote-only):金鑰櫃檔案一律拒寫(10-24 @ee4f8)
REMOTE_ONLY_NAMES = ("FLUENTMAIL_AWS_ACCESS_KEY_ID", "FLUENTMAIL_AWS_SECRET_ACCESS_KEY")
REMOTE_ONLY_FILES = ("fluentmail.env",)  # 前綴對到這類的名字(FLUENTMAIL_…_SITE2)也一樣(@abb89 審 #301)


def file_for_name(name):
    if not isinstance(name, str) or not name:
        return None
    if name in EXACT_FILES:
        return EXACT_FILES[name]
    for prefix, rel in PREFIX_TABLE:
        if name.startswith(prefix):
            return rel
    return None


NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}")  # 10-24 @abb89 #301 第 2 輪:名字帶換行 / = 會把一行寫進別的檔


def valid_name(name):
    """金鑰名字白名單(一律 fullmatch;$ 會放過結尾換行)。不符 ⇒ 呼叫端回 bad_name。"""
    return isinstance(name, str) and NAME_RE.fullmatch(name) is not None


def is_remote_only(name):
    """只准送伺服器:在名單,或名字落在只送伺服器的那類檔。"""
    return name in REMOTE_ONLY_NAMES or file_for_name(name) in REMOTE_ONLY_FILES


class SecretError(Exception):
    def __init__(self, reason, name, detail=""):
        self.reason = reason
        self.name = name
        self.detail = detail
        # 訊息只帶名字與理由,永不帶值
        msg = "%s %s" % (reason, name)
        if detail:
            msg += " (%s)" % detail
        super().__init__(msg)


def secrets_dir(env=None):
    env = os.environ if env is None else env
    override = env.get("EZ2_AIB_SECRETS_DIR") or ""
    if override:
        return Path(os.path.expanduser(override))
    root = (env.get("EZ2_AIB_STATE_ROOT")
            or env.get("EZ2_AIB_HOME")
            or "~/.ez2-aib")
    return Path(os.path.expanduser(root)) / "secrets"


def _refuse_vault(path):
    mem = Path(__file__).resolve().parents[1] / "memory"
    if str(mem) not in sys.path:
        sys.path.insert(0, str(mem))
    from brain_write_plan import WritePlanError, refuse_vault_output  # noqa: E402
    try:
        refuse_vault_output(path)
    except WritePlanError as err:
        raise SecretError(PERM_TOO_OPEN, "SECRETS_DIR", "vault") from err


def _too_open(path, want):
    try:
        mode = stat.S_IMODE(os.lstat(str(path)).st_mode)
    except OSError:
        return True
    if mode != want:
        return True
    if mode & OPEN_MASK:
        return True
    return False


def parse_env_bytes(raw):
    """回 {NAME: value}。註解與空行丟掉。值原樣。"""
    out = {}
    if not raw:
        return out
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        if key:
            out[key] = val
    return out


def _read_file(path):
    with open(path, "rb") as fh:
        return fh.read()


def secret(name, env=None, now=None, token_check=None, send=None):
    """取一支金鑰。成功回字串;失敗丟 SecretError。

    讀之前先過開關:關閉、算力不足、不在時段、到期需確認,都不回值。
    """
    from secret_read_gate import assert_read_allowed
    assert_read_allowed(name, env=env, now=now, token_check=token_check,
                        send=send)
    rel = file_for_name(name)
    if rel is None:
        raise SecretError(MISSING_FILE, name, "unknown name")
    d = secrets_dir(env)
    _refuse_vault(d)
    if not d.is_dir():
        raise SecretError(MISSING_FILE, name, "dir")
    if _too_open(d, DIR_MODE):
        raise SecretError(PERM_TOO_OPEN, name, "dir")
    path = d / rel
    if path.is_file() and _too_open(path, FILE_MODE):
        raise SecretError(PERM_TOO_OPEN, name, "file")
    rows = {}
    if path.is_file():
        rows = parse_env_bytes(_read_file(path))
    raw = rows.get(name) if name in rows else ""
    if not raw:
        from secret_collect import request_fill, NEEDS_FILL as FILL
        payload = request_fill(name, env=env, send=send, now=now)
        if payload is not None:
            raise SecretError(FILL, name, "awaiting line fill")
        if not path.is_file():
            raise SecretError(MISSING_FILE, name, "file")
        raise SecretError(MISSING_LINE, name)
    try:
        import secret_crypto as C
        if C.is_envelope(raw):
            return C.decrypt_value(raw, name, d)
    except Exception as err:  # noqa: BLE001
        reason = getattr(err, "reason", "decrypt_failed")
        if reason == "perm_too_open":
            raise SecretError(PERM_TOO_OPEN, name, "wrap.key") from err
        raise SecretError("decrypt_failed", name) from err
    return raw


def load_file(rel, env=None):
    """讀一支檔的所有行(給 doctor/init)。權限太寬一樣拒。"""
    d = secrets_dir(env)
    _refuse_vault(d)
    path = d / rel
    if not path.is_file():
        return {}
    if d.is_dir() and _too_open(d, DIR_MODE):
        raise SecretError(PERM_TOO_OPEN, rel, "dir")
    if _too_open(path, FILE_MODE):
        raise SecretError(PERM_TOO_OPEN, rel, "file")
    return parse_env_bytes(_read_file(path))


def index_entries():
    rows = []
    for rel, plat, _cat, _cmt, keys in LAYOUT:
        for name, purpose, consumer, feature, _fb in keys:
            rows.append({
                "name": name,
                "file": rel,
                "platform": plat,
                "consumer": consumer,
                "feature": feature,
                "purpose": purpose,
                "created": "",
                "rotate_by": "",
                "account": "",
                "backend": "file",
            })
    return rows


def template_text(rel):
    for path, _plat, _cat, comment, keys in LAYOUT:
        if path == rel:
            lines = ["# %s" % comment, "# 一行一隻 NAME=value;空=未設定。"]
            for name, purpose, _c, _f, _fb in keys:
                lines.append("# %s" % purpose)
                lines.append("%s=" % name)
            return "\n".join(lines) + "\n"
    return ""
