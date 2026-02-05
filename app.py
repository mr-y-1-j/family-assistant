import streamlit as st
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
import json
import tempfile
import os
import time
import requests

st.warning(f"現在のライブラリバージョン: {genai.__version__}")

# ==========================================
# 🔧 設定エリア
# ==========================================
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    NOTION_API_KEY = st.secrets["NOTION_API_KEY"]
    DATABASE_ID = st.secrets["DATABASE_ID"]
except FileNotFoundError:
    st.error("Secretsファイルが見つかりません。")
    st.stop()
except KeyError:
    st.error("APIキー設定が不足しています。")
    st.stop()

MODEL_NAME = 'gemini-1.5-flash'

# ==========================================
# 1. Gemini分析関数 (エラー回避機能付き)
# ==========================================
@st.cache_data(show_spinner=False)
def analyze_file(file_path, mime_type):
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(MODEL_NAME)
    
    with st.spinner('🤖 Geminiがプリントを読んでいます...'):
        try:
            # 1. ファイルアップロード
            uploaded_file = genai.upload_file(path=file_path, mime_type=mime_type)
            
            # 2. 処理完了待ち（重要）
            while uploaded_file.state.name == "PROCESSING":
                time.sleep(1)
                uploaded_file = genai.get_file(uploaded_file.name)

            if uploaded_file.state.name == "FAILED":
                st.error("Google側で画像処理に失敗しました。")
                return None

            # 3. 生成リクエスト
            prompt = """
            あなたは優秀な秘書です。このドキュメントからカレンダー登録用データを抽出してください。
            
            【出力ルール】
            - JSON形式で出力すること
            - date: YYYY-MM-DD (年が不明なら2026年とする)
            - event: 行事名
            - items: 持ち物リスト（文字列の配列。なければ空配列）
            - note: 備考（なければnull）
            """

            response = model.generate_content(
                [uploaded_file, prompt],
                generation_config={"response_mime_type": "application/json"}
            )
            return json.loads(response.text)

        except ResourceExhausted:
            st.error("⚠️ API利用制限（混雑）のためエラーになりました。1分ほど待ってから再度「AI解析開始」を押してください。")
            return None
        except Exception as e:
            st.error(f"エラーが発生しました: {e}")
            return None

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
    
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    for i, item in enumerate(data_list):
        # 進捗表示
        status_text.text(f"送信中: {item['event']}...")
        progress_bar.progress((i + 1) / len(data_list))

        # タイトル作成
        icon = "🎒" if item.get('items') else "🗓️"
        title_text = f"{icon} {item['event']}"
        items_text = "、".join(item.get('items', []))
        note_text = item.get('note') or ""
        
        payload = {
            "parent": {"database_id": DATABASE_ID},
            "properties": {
                "Name": {"title": [{"text": {"content": title_text}}]},
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
            
    status_text.empty()
    progress_bar.empty()
    return success_count

# ==========================================
# 3. アプリ画面 (UI)
# ==========================================
st.title("🏫 学校プリント・スキャナー")

# Session Stateの初期化（ボタンを押してもデータが消えないようにする）
if 'analyzed_data' not in st.session_state:
    st.session_state['analyzed_data'] = None

uploaded_file = st.file_uploader("写真またはPDFを選択", type=['png', 'jpg', 'jpeg', 'pdf'])

if uploaded_file is not None:
    # プレビュー
    if uploaded_file.name.lower().endswith(('.png', '.jpg', '.jpeg')):
        st.image(uploaded_file, caption="プレビュー", width=300)

    # 解析ボタン
    if st.button("AI解析開始"):
        # 一時ファイル保存
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{uploaded_file.name.split('.')[-1]}") as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_path = tmp_file.name

        mime_type = "application/pdf" if uploaded_file.name.endswith(".pdf") else "image/jpeg"
        
        # 解析実行
        result = analyze_file(tmp_path, mime_type)
        
        if result:
            st.session_state['analyzed_data'] = result
            st.success("解析成功！内容を確認してください。")
        
        # 掃除
        os.unlink(tmp_path)

# 解析結果がある場合のみ表示（ボタンを押してもここが維持される）
if st.session_state['analyzed_data']:
    st.subheader("解析結果")
    
    # 編集可能なエディタで表示（修正可能）
    edited_data = st.data_editor(st.session_state['analyzed_data'], num_rows="dynamic")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🚀 Notionに登録する", type="primary"):
            count = send_to_notion(edited_data)
            st.balloons()
            st.success(f"{count}件の予定を登録しました！")
            st.session_state['analyzed_data'] = None # 完了したらクリア
    with col2:
        if st.button("やり直す"):
            st.session_state['analyzed_data'] = None
            st.rerun()
