"""
X (Twitter) アカウント情報取得ツール

X API v2を使って、アカウントのプロフィール情報と最近のツイートを取得する。
Giveaway当選者のbot判定などに活用。

使い方:
  # 単一アカウント
  python x_account_lookup.py aspie234

  # 複数アカウント比較
  python x_account_lookup.py aspie234 AcelisUni

  # ファイルに保存
  python x_account_lookup.py aspie234 --output data/x-lookup/
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_URL = "https://api.twitter.com/2"


def get_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def api_request(token: str, endpoint: str, params: dict | None = None) -> dict:
    """X API v2にリクエストを送信する。"""
    url = f"{BASE_URL}{endpoint}"
    resp = requests.get(url, headers=get_headers(token), params=params)

    if resp.status_code == 429:
        reset = resp.headers.get("x-rate-limit-reset", "")
        print(f"  Rate limited. Reset at: {reset}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 401:
        print("Error: Invalid bearer token.", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(f"Error: {resp.status_code} - {resp.text}", file=sys.stderr)
        return {}

    return resp.json()


def lookup_user(token: str, username: str) -> dict | None:
    """ユーザー名からプロフィール情報を取得する。"""
    params = {
        "user.fields": "created_at,description,public_metrics,profile_image_url,verified,location,url",
    }
    data = api_request(token, f"/users/by/username/{username}", params)
    if "data" not in data:
        errors = data.get("errors", [])
        if errors:
            print(f"  User @{username}: {errors[0].get('detail', 'Not found')}", file=sys.stderr)
        return None
    return data["data"]


def get_recent_tweets(token: str, user_id: str, max_results: int = 20) -> list[dict]:
    """ユーザーの最近のツイートを取得する。"""
    params = {
        "tweet.fields": "created_at,public_metrics,text",
        "max_results": min(max_results, 100),
        "exclude": "retweets,replies",
    }
    data = api_request(token, f"/users/{user_id}/tweets", params)
    return data.get("data", [])


def format_number(n: int) -> str:
    """数値を読みやすい形式に変換する。"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def user_to_markdown(user: dict, tweets: list[dict]) -> str:
    """ユーザー情報とツイートをMarkdown形式に変換する。"""
    lines = []
    metrics = user.get("public_metrics", {})
    created = user.get("created_at", "")
    if created:
        created = datetime.fromisoformat(created.replace("Z", "+00:00")).strftime("%Y-%m-%d")

    lines.append(f"# @{user['username']}")
    lines.append("")
    lines.append(f"**Name**: {user.get('name', 'N/A')}")
    lines.append(f"**Bio**: {user.get('description', '(empty)')}")
    if user.get("location"):
        lines.append(f"**Location**: {user['location']}")
    if user.get("url"):
        lines.append(f"**URL**: {user['url']}")
    lines.append(f"**Followers**: {format_number(metrics.get('followers_count', 0))} / **Following**: {format_number(metrics.get('following_count', 0))}")
    lines.append(f"**Tweets**: {format_number(metrics.get('tweet_count', 0))}")
    lines.append(f"**Account Created**: {created}")
    lines.append("")

    if not tweets:
        lines.append("## Recent Tweets")
        lines.append("")
        lines.append("(No tweets found)")
    else:
        lines.append(f"## Recent Tweets ({len(tweets)})")
        lines.append("")
        for tweet in tweets:
            t_created = tweet.get("created_at", "")
            if t_created:
                t_created = datetime.fromisoformat(t_created.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
            t_metrics = tweet.get("public_metrics", {})
            likes = t_metrics.get("like_count", 0)
            rts = t_metrics.get("retweet_count", 0)
            replies = t_metrics.get("reply_count", 0)

            lines.append(f"**{t_created}** (❤️ {likes} / 🔁 {rts} / 💬 {replies})")
            lines.append(tweet.get("text", ""))
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="X (Twitter) アカウント情報取得ツール")
    parser.add_argument("usernames", nargs="+", help="取得するXアカウントのユーザー名（@なし）")
    parser.add_argument("--output", type=str, default=None, help="出力ディレクトリ（指定時はファイル保存）")
    parser.add_argument("--tweets", type=int, default=20, help="取得するツイート数（デフォルト: 20）")
    parser.add_argument("--env", type=str, default=None, help=".envファイルのパス")
    args = parser.parse_args()

    env_path = args.env or os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)

    token = os.getenv("X_BEARER_TOKEN")
    if not token:
        print("Error: X_BEARER_TOKEN が設定されていません。", file=sys.stderr)
        print(f"  .envファイル ({env_path}) にトークンを設定してください。", file=sys.stderr)
        sys.exit(1)

    output_dir = None
    if args.output:
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)

    for username in args.usernames:
        username = username.lstrip("@")
        print(f"Looking up @{username}...", file=sys.stderr)

        user = lookup_user(token, username)
        if not user:
            continue

        tweets = get_recent_tweets(token, user["id"], args.tweets)
        md = user_to_markdown(user, tweets)

        if output_dir:
            filepath = output_dir / f"{username}.md"
            filepath.write_text(md, encoding="utf-8")
            print(f"  Saved: {filepath}", file=sys.stderr)
        else:
            print(md)
            if username != args.usernames[-1]:
                print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    main()
