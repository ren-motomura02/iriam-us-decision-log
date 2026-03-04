"""
Discord チャンネル会話取得ツール

DSPイラスト制作チャンネルの会話をMarkdownとして取得する。
Bot Tokenで認証し、Discord REST APIを直接利用する。

使い方:
  # 特定チャンネル1つを取得
  python discord_fetch.py --channel 123456789

  # 複数チャンネルを取得
  python discord_fetch.py --channels 123 456 789

  # カテゴリ配下の全チャンネルを一括取得
  python discord_fetch.py --category 123456789

  # チャンネル名にキーワードを含むものをフィルタ（--categoryと併用）
  python discord_fetch.py --category 123456789 --keyword iriam

  # サーバー内の全チャンネルを一覧表示（取得せず確認のみ）
  python discord_fetch.py --list-channels --guild 123456789

  # 出力先を指定（デフォルト: output/）
  python discord_fetch.py --category 123456789 --output data/discord-logs/
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_URL = "https://discord.com/api/v10"


def get_headers(token: str) -> dict:
    return {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }


def api_request(token: str, endpoint: str, params: dict | None = None) -> dict | list:
    """Discord APIにリクエストを送信する。Rate limit対応付き。"""
    url = f"{BASE_URL}{endpoint}"
    headers = get_headers(token)

    while True:
        resp = requests.get(url, headers=headers, params=params)

        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 1.0)
            print(f"  Rate limited. Waiting {retry_after:.1f}s...", file=sys.stderr)
            time.sleep(retry_after)
            continue

        if resp.status_code == 403:
            print(f"  Permission denied: {endpoint}", file=sys.stderr)
            return []

        resp.raise_for_status()
        return resp.json()


def get_guild_channels(token: str, guild_id: str) -> list[dict]:
    """サーバーの全チャンネルを取得する。"""
    return api_request(token, f"/guilds/{guild_id}/channels")


def get_channels_in_category(token: str, guild_id: str, category_id: str, keyword: str | None = None) -> list[dict]:
    """カテゴリ配下のテキストチャンネルを取得する。"""
    all_channels = get_guild_channels(token, guild_id)
    channels = [
        ch for ch in all_channels
        if ch.get("parent_id") == str(category_id) and ch.get("type") == 0  # type 0 = text channel
    ]
    if keyword:
        channels = [ch for ch in channels if keyword.lower() in ch.get("name", "").lower()]
    channels.sort(key=lambda ch: ch.get("name", ""))
    return channels


def get_channel_info(token: str, channel_id: str) -> dict:
    """チャンネル情報を取得する。"""
    return api_request(token, f"/channels/{channel_id}")


def fetch_all_messages(token: str, channel_id: str) -> list[dict]:
    """チャンネルの全メッセージをページネーションで取得する。"""
    messages = []
    before = None

    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before

        batch = api_request(token, f"/channels/{channel_id}/messages", params=params)

        if not batch:
            break

        messages.extend(batch)
        print(f"  Fetched {len(messages)} messages...", end="\r", file=sys.stderr)

        if len(batch) < 100:
            break

        before = batch[-1]["id"]

    print(f"  Fetched {len(messages)} messages total.", file=sys.stderr)

    # 時系列順にソート（古い→新しい）
    messages.sort(key=lambda m: m["id"])
    return messages


def format_timestamp(iso_timestamp: str) -> str:
    """ISO 8601タイムスタンプを読みやすい形式に変換する。"""
    dt = datetime.fromisoformat(iso_timestamp.replace("+00:00", "+00:00"))
    return dt.strftime("%Y-%m-%d %H:%M")


def message_to_markdown(msg: dict) -> str:
    """メッセージ1件をMarkdownテキストに変換する。"""
    lines = []

    author = msg.get("author", {})
    username = author.get("global_name") or author.get("username", "Unknown")
    timestamp = format_timestamp(msg["timestamp"])

    # 送信者とタイムスタンプ
    lines.append(f"**{username}** ({timestamp})")

    # リプライ先の表示
    ref = msg.get("referenced_message")
    if ref:
        ref_author = ref.get("author", {})
        ref_name = ref_author.get("global_name") or ref_author.get("username", "Unknown")
        ref_content = ref.get("content", "")
        if len(ref_content) > 80:
            ref_content = ref_content[:80] + "..."
        lines.append(f"> ↩️ Reply to **{ref_name}**: {ref_content}")

    # メッセージ本文
    content = msg.get("content", "")
    if content:
        lines.append(content)

    # 埋め込み（embeds）
    for embed in msg.get("embeds", []):
        title = embed.get("title", "")
        description = embed.get("description", "")
        url = embed.get("url", "")
        if title or description:
            embed_text = f"📋 **[Embed]** {title}"
            if url:
                embed_text += f" ({url})"
            lines.append(embed_text)
            if description:
                for desc_line in description.split("\n"):
                    lines.append(f"> {desc_line}")

    # 添付ファイル
    for att in msg.get("attachments", []):
        filename = att.get("filename", "file")
        url = att.get("url", "")
        content_type = att.get("content_type", "")
        if content_type and content_type.startswith("image/"):
            lines.append(f"🖼️ {filename} ({url})")
        else:
            lines.append(f"📎 {filename} ({url})")

    # リアクション
    reactions = msg.get("reactions", [])
    if reactions:
        reaction_str = " ".join(
            f"{r['emoji'].get('name', '?')}×{r['count']}" for r in reactions
        )
        lines.append(f"[Reactions: {reaction_str}]")

    return "\n".join(lines)


def channel_to_markdown(channel_info: dict, messages: list[dict]) -> str:
    """チャンネル全体をMarkdownドキュメントに変換する。"""
    name = channel_info.get("name", "unknown")
    channel_id = channel_info.get("id", "")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    lines = [
        f"# #{name}",
        "",
        f"**Channel ID**: {channel_id}",
        f"**取得日時**: {now}",
        f"**メッセージ数**: {len(messages)}",
        "",
        "---",
        "",
        "## 会話ログ",
        "",
    ]

    for msg in messages:
        lines.append(message_to_markdown(msg))
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def save_channel(channel_info: dict, messages: list[dict], output_dir: Path) -> Path:
    """チャンネルの会話をMarkdownファイルとして保存する。"""
    name = channel_info.get("name", "unknown")
    channel_id = channel_info.get("id", "")
    filename = f"{name}_{channel_id}.md"
    filepath = output_dir / filename

    md = channel_to_markdown(channel_info, messages)
    filepath.write_text(md, encoding="utf-8")
    return filepath


def resolve_guild_id(token: str, channel_id: str | None = None, category_id: str | None = None) -> str | None:
    """チャンネルまたはカテゴリIDからguild_idを解決する。"""
    target_id = channel_id or category_id
    if not target_id:
        return None
    info = get_channel_info(token, target_id)
    return info.get("guild_id")


def list_channels(token: str, guild_id: str) -> None:
    """サーバーの全チャンネルをカテゴリごとに一覧表示する。"""
    channels = get_guild_channels(token, guild_id)

    # カテゴリでグループ化
    categories = {}
    uncategorized = []

    for ch in channels:
        if ch["type"] == 4:  # category
            categories[ch["id"]] = {"name": ch["name"], "channels": []}

    for ch in channels:
        if ch["type"] == 0:  # text channel
            parent = ch.get("parent_id")
            if parent and parent in categories:
                categories[parent]["channels"].append(ch)
            else:
                uncategorized.append(ch)

    for cat_id, cat in sorted(categories.items(), key=lambda x: x[1]["name"]):
        print(f"\n📁 {cat['name']} (ID: {cat_id})")
        for ch in sorted(cat["channels"], key=lambda c: c["name"]):
            print(f"   💬 #{ch['name']} (ID: {ch['id']})")

    if uncategorized:
        print(f"\n📁 (カテゴリなし)")
        for ch in sorted(uncategorized, key=lambda c: c["name"]):
            print(f"   💬 #{ch['name']} (ID: {ch['id']})")


def main():
    parser = argparse.ArgumentParser(description="Discord チャンネル会話取得ツール")

    # 取得対象の指定
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--channel", type=str, help="取得するチャンネルID（1つ）")
    group.add_argument("--channels", type=str, nargs="+", help="取得するチャンネルID（複数）")
    group.add_argument("--category", type=str, help="カテゴリID（配下の全テキストチャンネルを取得）")
    group.add_argument("--list-channels", action="store_true", help="チャンネル一覧を表示（取得しない）")

    # オプション
    parser.add_argument("--guild", type=str, help="サーバーID（--list-channels, --keyword使用時に必要）")
    parser.add_argument("--keyword", type=str, help="チャンネル名のフィルタ（--categoryと併用）")
    parser.add_argument("--output", type=str, default="output", help="出力ディレクトリ（デフォルト: output/）")
    parser.add_argument("--env", type=str, default=None, help=".envファイルのパス")

    args = parser.parse_args()

    # .envの読み込み
    env_path = args.env or os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)

    token = os.getenv("DSP_DISCORD_BOT_TOKEN")
    if not token:
        print("Error: DSP_DISCORD_BOT_TOKEN が設定されていません。", file=sys.stderr)
        print(f"  .envファイル ({env_path}) にトークンを設定してください。", file=sys.stderr)
        sys.exit(1)

    # チャンネル一覧表示モード
    if args.list_channels:
        guild_id = args.guild
        if not guild_id:
            # チャンネルやカテゴリからguild_idを推測
            print("Error: --guild でサーバーIDを指定してください。", file=sys.stderr)
            sys.exit(1)
        list_channels(token, guild_id)
        return

    # 取得対象のチャンネルリストを構築
    target_channels = []

    if args.channel:
        info = get_channel_info(token, args.channel)
        target_channels = [info]

    elif args.channels:
        for ch_id in args.channels:
            info = get_channel_info(token, ch_id)
            target_channels.append(info)

    elif args.category:
        guild_id = args.guild
        if not guild_id:
            guild_id = resolve_guild_id(token, category_id=args.category)
        if not guild_id:
            print("Error: guild_idが解決できません。--guild で指定してください。", file=sys.stderr)
            sys.exit(1)
        target_channels = get_channels_in_category(token, guild_id, args.category, args.keyword)

    else:
        parser.print_help()
        sys.exit(1)

    if not target_channels:
        print("取得対象のチャンネルが見つかりませんでした。", file=sys.stderr)
        sys.exit(1)

    # 出力ディレクトリ作成
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 各チャンネルのメッセージを取得・保存
    print(f"\n取得対象: {len(target_channels)} チャンネル", file=sys.stderr)
    print(f"出力先: {output_dir.resolve()}\n", file=sys.stderr)

    results = []
    for i, ch in enumerate(target_channels, 1):
        name = ch.get("name", "unknown")
        ch_id = ch.get("id", "")
        print(f"[{i}/{len(target_channels)}] #{name} ({ch_id})", file=sys.stderr)

        messages = fetch_all_messages(token, ch_id)
        filepath = save_channel(ch, messages, output_dir)
        results.append({"name": name, "id": ch_id, "messages": len(messages), "file": str(filepath)})

    # サマリー表示
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"取得完了", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    total_messages = 0
    for r in results:
        total_messages += r["messages"]
        print(f"  #{r['name']}: {r['messages']} messages → {r['file']}", file=sys.stderr)
    print(f"\n合計: {len(results)} チャンネル / {total_messages} メッセージ", file=sys.stderr)


if __name__ == "__main__":
    main()
