"""
Discord チャンネルのロール権限削除ツール

"Active" prefixのついたカテゴリ配下のチャンネルから
"IRIAM Illustration Staff.*" にマッチするロールの権限を削除する。

使い方:
  # 実行（確認なし）
  python discord_remove_role_permissions.py

  # ドライラン（実際には変更せず確認のみ）
  python discord_remove_role_permissions.py --dry-run

  # サーバーIDを明示
  python discord_remove_role_permissions.py --guild 1388259934809886762
"""

import argparse
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

BASE_URL = "https://discord.com/api/v10"
DEFAULT_GUILD_ID = "1388259934809886762"

# 対象カテゴリ名のパターン（"Active" prefix）
CATEGORY_PATTERN = re.compile(r"Active", re.IGNORECASE)

# 削除対象ロール名のパターン
ROLE_PATTERN = re.compile(r"^IRIAM Illustration Staff")


def get_headers(token: str) -> dict:
    return {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }


def api_get(token: str, endpoint: str) -> dict | list:
    """GETリクエスト（rate limit対応）"""
    url = f"{BASE_URL}{endpoint}"
    headers = get_headers(token)

    while True:
        resp = requests.get(url, headers=headers)

        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 1.0)
            print(f"  Rate limited. Waiting {retry_after:.1f}s...", file=sys.stderr)
            time.sleep(retry_after)
            continue

        resp.raise_for_status()
        return resp.json()


def api_delete(token: str, endpoint: str) -> bool:
    """DELETEリクエスト（rate limit対応）。成功でTrue。"""
    url = f"{BASE_URL}{endpoint}"
    headers = get_headers(token)

    while True:
        resp = requests.delete(url, headers=headers)

        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 1.0)
            print(f"  Rate limited. Waiting {retry_after:.1f}s...", file=sys.stderr)
            time.sleep(retry_after)
            continue

        if resp.status_code == 204:
            return True

        if resp.status_code == 404:
            # すでに権限が存在しない場合はスキップ
            return False

        resp.raise_for_status()
        return False


def get_guild_channels(token: str, guild_id: str) -> list[dict]:
    return api_get(token, f"/guilds/{guild_id}/channels")


def get_guild_roles(token: str, guild_id: str) -> list[dict]:
    return api_get(token, f"/guilds/{guild_id}/roles")


def main():
    parser = argparse.ArgumentParser(description="Discord チャンネルのロール権限削除ツール")
    parser.add_argument("--guild", type=str, default=DEFAULT_GUILD_ID, help="サーバーID")
    parser.add_argument("--dry-run", action="store_true", help="変更せずに対象を確認のみ")
    parser.add_argument("--env", type=str, default=None, help=".envファイルのパス")
    args = parser.parse_args()

    # .env 読み込み
    env_path = args.env or os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)

    token = os.getenv("DSP_DISCORD_BOT_TOKEN")
    if not token:
        print("Error: DSP_DISCORD_BOT_TOKEN が設定されていません。", file=sys.stderr)
        sys.exit(1)

    guild_id = args.guild
    dry_run = args.dry_run

    if dry_run:
        print("[DRY RUN] 実際の変更は行いません\n")

    # ロール一覧取得
    print("ロール一覧を取得中...")
    all_roles = get_guild_roles(token, guild_id)
    target_roles = [r for r in all_roles if ROLE_PATTERN.search(r["name"])]

    if not target_roles:
        print("Error: 対象ロール（IRIAM Illustration Staff.*）が見つかりませんでした。")
        sys.exit(1)

    print(f"\n対象ロール ({len(target_roles)}件):")
    for r in target_roles:
        print(f"  - {r['name']} (ID: {r['id']})")

    # チャンネル一覧取得
    print("\nチャンネル一覧を取得中...")
    all_channels = get_guild_channels(token, guild_id)

    # Active prefix のカテゴリを抽出
    active_categories = {
        ch["id"]: ch["name"]
        for ch in all_channels
        if ch["type"] == 4 and CATEGORY_PATTERN.search(ch["name"])
    }

    if not active_categories:
        print("Error: 'Active' prefixのカテゴリが見つかりませんでした。")
        sys.exit(1)

    print(f"\n対象カテゴリ ({len(active_categories)}件):")
    for cat_id, cat_name in sorted(active_categories.items(), key=lambda x: x[1]):
        print(f"  - {cat_name} (ID: {cat_id})")

    # 各カテゴリ配下のチャンネルを収集
    # type 0: text, type 2: voice, type 5: announcement, type 15: forum など
    # カテゴリ配下の全チャンネルを対象にする
    target_channels = [
        ch for ch in all_channels
        if ch.get("parent_id") in active_categories
    ]

    print(f"\n対象チャンネル ({len(target_channels)}件):")
    for ch in sorted(target_channels, key=lambda c: (active_categories.get(c.get("parent_id", ""), ""), c["name"])):
        cat_name = active_categories.get(ch.get("parent_id", ""), "?")
        print(f"  - [{cat_name}] #{ch['name']} (ID: {ch['id']})")

    print()

    if not target_channels:
        print("対象チャンネルが見つかりませんでした。")
        sys.exit(0)

    # 権限削除
    role_ids = {r["id"] for r in target_roles}
    removed = 0
    skipped = 0

    for ch in target_channels:
        ch_id = ch["id"]
        ch_name = ch["name"]
        cat_name = active_categories.get(ch.get("parent_id", ""), "?")

        # チャンネルのpermission_overwritesを確認
        permission_overwrites = ch.get("permission_overwrites", [])
        role_overwrites = [
            ow for ow in permission_overwrites
            if ow["type"] == 0 and ow["id"] in role_ids  # type 0 = role
        ]

        if not role_overwrites:
            # 権限が設定されていないチャンネルはスキップ
            skipped += 1
            continue

        for ow in role_overwrites:
            role_id = ow["id"]
            role_name = next((r["name"] for r in target_roles if r["id"] == role_id), role_id)

            if dry_run:
                print(f"[DRY RUN] [{cat_name}] #{ch_name}: ロール '{role_name}' の権限を削除")
                removed += 1
            else:
                print(f"[{cat_name}] #{ch_name}: ロール '{role_name}' の権限を削除中...")
                success = api_delete(token, f"/channels/{ch_id}/permissions/{role_id}")
                if success:
                    print(f"  -> 削除完了")
                    removed += 1
                else:
                    print(f"  -> スキップ（権限が存在しないか削除済み）")
                    skipped += 1

    print(f"\n{'='*60}")
    if dry_run:
        print(f"[DRY RUN] 削除対象: {removed}件, スキップ: {skipped}件")
    else:
        print(f"削除完了: {removed}件, スキップ: {skipped}件")


if __name__ == "__main__":
    main()
