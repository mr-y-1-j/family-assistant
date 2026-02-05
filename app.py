import streamlit as st
import google.generativeai as genai
import json
import tempfile
import os
import time
import requests
from datetime import datetime

# ==========================================
# 🔧 設定エリア（StreamlitのSecretsから読み込む）
# ==========================================
# ローカルで動かすときは st.secrets の代わりに直接キーを入れてテスト可能ですが、
# 公開時は必ずSecrets機能を使います。
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    NOTION_API_KEY = st.secrets["NOTION_API_KEY"]
    DATABASE_ID = st.secrets["DATABASE_ID"]
except:
    # エラーハンドリング（キー未設定時）
    st.error("APIキーが設定されていません。StreamlitのSecretsを設定してください。")
    st.stop()

MODEL_NAME = 'gemini-2.0-flash'

# ==========================================
# 1. Gemini分析関数
# ==========================================
@st.cache_data(show_spinner=False)
def analyze_file(file_path, mime_type):
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(MODEL_NAME)
    
    with st.spinner('🤖 Geminiがプリントを読んでいます...'):
        uploaded_file = genai.upload_file(path=file_path, mime_type=mime_type)
        
        # 処理待ち
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(1)
            uploaded_file = genai.get_file(uploaded_file.name)

        if uploaded_file.state.name == "FAILED":
            st.error("ファイルの処理に失敗しました")
            return []

        prompt = """
        あなたは優秀な秘書です。このドキュメントからカレンダー登録用データを抽出してください。
        
        【出力ルール】
        - JSONの値は必ず「日本語」で出力。
        - date: YYYY-MM-DD
        - event: 行事名
        - items: 持ち物リスト（なければ空配列）
        - note: 備考（なければnull）
        """

        response = model.generate_content(
            [uploaded_file, prompt],
            generation_config={"response_mime_type": "application/json"}
        )
        return json.loads(response.text)

# ==========================================
# 2. Notion送信関数
# ==========================================
def send_to_notion(data_list):
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    success_count = 0
    
    for item in data_list:
        # タイトル作成（イベント名 + 持ち物ありならアイコン）
        icon = "🎒" if item.get('items') else "🗓️"
        title_text = f"{icon} {item['event']}"
        
        # 持ち物をテキスト化
        items_text = "、".join(item.get('items', []))
        note_text = item.get('note') or ""
        
        payload = {
            "parent": {"database_id": DATABASE_ID},
            "properties": {
                "Name": {"title": [{"text": {"content": title_text}}]},
                # カレンダーの日付（Dateプロパティが必要）
                "Date": {"date": {"start": item['date']}},
                "Tags": {"multi_select": [{"name": "学校"}]},
            },
            "children": [
                {
                    "object": "block",
                    "type": "callout",
                    "callout": {
                        "rich_text": [{"text": {"content": f"持ち物: {items_text}"}}],
                        "icon": {"emoji": "🎒"}
                    }
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"text": {"content": f"備考: {note_text}"}}]}
                }
            ]
        }
        res = requests.post(url, headers=headers, data=json.dumps(payload))
        if res.status_code == 200:
            success_count += 1
            
    return success_count

# ==========================================
# 3. アプリ画面 (UI)
# ==========================================
st.title("🏫 学校プリント・スキャナー")
st.write("プリントの写真をアップロードすると、Notionカレンダーに登録します。")

# ファイルアップローダー
uploaded_file = st.file_uploader("写真またはPDFを選択", type=['png', 'jpg', 'jpeg', 'pdf'])

if uploaded_file is not None:
    # 一時ファイルとして保存（Gemini APIに渡すため）
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{uploaded_file.name.split('.')[-1]}") as tmp_file:
        tmp_file.write(uploaded_file.getvalue())
        tmp_path = tmp_file.name

    # MIMEタイプ判定
    mime_type = "application/pdf" if uploaded_file.name.endswith(".pdf") else "image/jpeg"

    # 画像ならプレビュー表示
    if mime_type != "application/pdf":
        st.image(uploaded_file, caption="プレビュー", use_column_width=True)

    # ボタンが押されたら実行
    if st.button("AI解析開始"):
        result_data = analyze_file(tmp_path, mime_type)
        
        if result_data:
            st.success("解析完了！以下のデータが見つかりました。")
            st.json(result_data)
            
            # Notion登録ボタン
            if st.button("Notionに登録する"):
                count = send_to_notion(result_data)
                st.balloons()
                st.success(f"{count}件の予定をNotionに登録しました！")
    
    # 掃除
    os.unlink(tmp_path)
