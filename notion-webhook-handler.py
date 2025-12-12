import json
import os
import urllib.request
import urllib.error

from openai import OpenAI

# ---- OpenAI ----
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# ---- Notion ----
NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_VERSION = os.environ.get("NOTION_VERSION", "2022-06-28")


def _extract_company_info(notion_page_payload: dict) -> tuple[str | None, str | None]:
    """
    Notion webhook payload の data.properties から
    - 企業名（title）
    - Website（url）
    を抜き出す
    """
    props = (notion_page_payload.get("data") or {}).get("properties") or {}

    # 企業名: title -> title[0].plain_text
    company_name = None
    try:
        title_arr = (props.get("企業名") or {}).get("title") or []
        if title_arr:
            company_name = title_arr[0].get("plain_text")
    except Exception:
        company_name = None

    # Website: url
    website = None
    try:
        website = (props.get("Website") or {}).get("url")
    except Exception:
        website = None

    return company_name, website


def _truncate_jp_150(text: str) -> str:
    """ざっくり「150文字以下」を担保（Unicodeの文字数ベース）"""
    text = (text or "").strip()
    if len(text) <= 150:
        return text
    return text[:150].rstrip() + "…"


def _notion_update_overview_and_description(page_id: str, overview: str, description: str) -> None:
    if not NOTION_API_KEY:
        raise RuntimeError("NOTION_API_KEY is not set")

    overview = _truncate_jp_150(overview)

    description = (description or "").strip()
    if len(description) > 1800:
        description = description[:1800] + "…"

    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {
        "properties": {
            "Overview": {
                "rich_text": [{"type": "text", "text": {"content": overview}}]
            },
            "Description by Agent": {
                "rich_text": [{"type": "text", "text": {"content": description}}]
            },
        }
    }

    req = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        method="PATCH",
        headers={
            "Authorization": f"Bearer {NOTION_API_KEY}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            _ = resp.read().decode("utf-8", errors="replace")
            print("Notion update status:", resp.status)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print("Notion HTTPError:", e.code)
        print("Notion HTTPError body:", body)
        raise


def lambda_handler(event, context):
    # HTTP API (payload v2.0) の想定
    body_raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64
        body_raw = base64.b64decode(body_raw).decode("utf-8")

    try:
        data = json.loads(body_raw) if body_raw else {}
    except json.JSONDecodeError:
        data = {"_raw": body_raw}

    try:
        # Notion page_id
        page_id = (data.get("data") or {}).get("id")
        if not page_id:
            raise RuntimeError("page_id not found in event body at data.id")

        company_name, website = _extract_company_info(data)
        if not company_name and not website:
            raise RuntimeError("company info not found (企業名 / Website)")

        print("page_id:", page_id)
        print("company_name:", company_name)
        print("website:", website)

        # ---- OpenAI web search + summarize (2粒度を一括生成) ----
        prompt = f"""
あなたは企業調査アナリストである。最新のWeb情報を検索して、次の企業の事業内容を要約せよ。

- 企業名: {company_name or "(不明)"}
- Website: {website or "(不明)"}

### 調査情報源の指示（最重要）
1. 一次情報としてのホームページ（URL）の情報を核とする。
2. それに加え、客観的かつ広範な情報を収集するため、少なくとも５つ以上の信頼できる外部情報源
   （TechCrunch, Bloomberg, Crunchbase, 公式プレスリリース等）を調査し、情報を統合する。
3. 情報鮮度を重視し、直近3年以内の情報を優先して利用すること。
4. 特に、技術的な特徴や市場での評価については、第三者による客観的な見解を優先して分析に含めること。

出力は「必ず」JSONのみ（前後に説明文を付けない）で返せ。
JSONスキーマ:
{{
  "overview": "150文字以下の日本語で、企業の事業内容を一文で要約せよ。",
  "description": "日本語で詳細要約せよ。文体は必ず『だ・である』調とする。
- 事業概要（2〜4文）
- 主な提供価値/顧客（箇条書き 2〜4個）
- 補足（あれば：資金調達/主要プロダクト/対象市場など 1〜2行）
- 出典（以下の表記ルールに従い、最後にまとめて記載せよ）
"
}}

### 出典表記ルール
- 出典は description の最後にまとめて記載すること。
- 表記形式は以下を厳守せよ。
  「サイト名＋記事タイトル（年）＋(URL)」
- 複数ある場合は箇条書きで列挙すること。

注意:
- 不確かな情報は断定せず「可能性がある」などで表現すること。
- Website がある場合はまずそれを起点に企業を特定せよ。
""".strip()

        print("calling OpenAI (web_search)...")
        response = client.responses.create(
            model="gpt-5",
            tools=[{"type": "web_search"}],
            input=prompt,
        )

        raw = (response.output_text or "").strip()
        print("OpenAI done. raw length:", len(raw))

        # JSONパース（失敗したらそのままdescriptionに入れてoverviewは空にする）
        overview = ""
        description = ""
        try:
            obj = json.loads(raw)
            overview = obj.get("overview") or ""
            description = obj.get("description") or ""
        except Exception:
            print("WARNING: OpenAI output was not valid JSON. Falling back.")
            overview = ""
            description = raw

        # 保険：overviewが空ならdescriptionから雑に作る
        if not overview:
            overview = _truncate_jp_150(description.replace("\n", " "))

        # ---- Write to Notion (2カラム同時更新) ----
        _notion_update_overview_and_description(page_id, overview, description)
        print("Notion update done")

        return {
            "statusCode": 200,
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"ok": True})
        }

    except Exception as e:
        print("Error:", str(e))
        return {
            "statusCode": 500,
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"ok": False, "error": str(e)})
        }
