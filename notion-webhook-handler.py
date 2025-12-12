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


def _notion_update_description_by_agent(page_id: str, text: str) -> None:
    if not NOTION_API_KEY:
        raise RuntimeError("NOTION_API_KEY is not set")

    text = (text or "").strip()
    if len(text) > 1800:
        text = text[:1800] + "…"

    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {
        "properties": {
            "Description by Agent": {
                "rich_text": [
                    {"type": "text", "text": {"content": text}}
                ]
            }
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
            _ = resp.read().decode("utf-8")
            print("Notion update status:", resp.status)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print("Notion HTTPError:", e.code, body)
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

        # ---- OpenAI web search + summarize ----
        # web_search を有効化すると、モデルが必要に応じてWeb検索して要約を生成できます :contentReference[oaicite:2]{index=2}
        prompt = f"""
あなたは企業調査アナリストです。
次の企業について、最新のWeb情報を検索して事業内容を日本語で簡潔に要約してください。

- 企業名: {company_name or "(不明)"}
- Website: {website or "(不明)"}

出力フォーマット:
- 事業概要（2〜4文）
- 主な提供価値/顧客（箇条書き 2〜4個）
- 補足（あれば：資金調達/主要プロダクト/対象市場など 1〜2行）

注意:
- 不確かな情報は「可能性がある」など断定を避ける
- Website がある場合はまずそれを起点に企業を特定する
""".strip()

        print("calling OpenAI (web_search)...")
        response = client.responses.create(
            model="gpt-5",
            tools=[{"type": "web_search"}],
            input=prompt,
        )
        summary = response.output_text  # web_search 使用時もここに最終テキストが入ります :contentReference[oaicite:3]{index=3}
        print("OpenAI done. summary length:", len(summary))

        # ---- Write to Notion ----
        _notion_update_description_by_agent(page_id, summary)
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
