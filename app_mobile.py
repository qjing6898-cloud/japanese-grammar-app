import streamlit as st
import pandas as pd
import google.generativeai as genai
from datetime import datetime
import json
import gspread
import pytz 
from gspread_dataframe import set_with_dataframe # Sheets 依赖

# --- 1. 配置你的 AI ---
try:
    # 从 Streamlit Cloud Secrets 安全读取 Key
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')
except KeyError:
    st.error("无法读取 Gemini API Key。请在 Streamlit Cloud Secrets 中检查 GOOGLE_API_KEY 配置。")
except Exception as e:
    st.error(f"AI 配置错误: {e}")

# --- 2. 数据库连接配置 (Google Sheets) ---
SHEET_TITLE = "Japanese_Grammar_History"
# ⚠️⚠️⚠️ 替换为你自己的 Google Sheets 完整网址，用双引号包裹
SHEET_URL = "https://docs.google.com/spreadsheets/d/1xrXmiV5yEYIC4lDfgjk79vQDNVHYZugW6XUReZbHWjY/edit?gid=0#gid=0" 

@st.cache_resource(ttl=3600) # 缓存连接，避免重复认证
def get_sheets_client():
    try:
        gcp_sa = st.secrets["gcp_service_account"]
        gc = gspread.service_account_from_dict(gcp_sa)
        return gc
    except KeyError:
        st.warning("无法读取 Google Cloud 凭证，历史记录功能未完全启用。请检查 gcp_service_account 配置。")
        return None
    except Exception as e:
        st.error(f"Google Sheets 认证失败: {e}")
        return None

def load_history():
    # 尝试加载 Google Sheets 中的历史记录
    gc = get_sheets_client()
    if not gc: return pd.DataFrame()
    
    try:
        # 使用 URL 打开工作表
        spreadsheet = gc.open_by_url(SHEET_URL)
        worksheet = spreadsheet.sheet1
        # 读取表格所有内容，并用第一行作为列头
        df = pd.DataFrame(worksheet.get_all_records())
        return df.iloc[::-1] # 倒序，最新记录在前
    except gspread.exceptions.SpreadsheetNotFound:
        st.warning(f"Google 表格 '{SHEET_TITLE}' 不存在或无访问权限。请检查共享设置。")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"加载历史记录失败: {e}")
        return pd.DataFrame()


def save_record(sentence, result_data):
    # 将新的记录写入 Google Sheets
    gc = get_sheets_client()
    if not gc: return
    
    try:
        spreadsheet = gc.open_by_url(SHEET_URL)
        worksheet = spreadsheet.sheet1
        
        # 使用东八区时间 (时区修复)
        tz = pytz.timezone('Asia/Shanghai')
        timestamp_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        # 准备要写入的数据行
        new_row = [
            timestamp_str,
            sentence,
            json.dumps(result_data, ensure_ascii=False), # 将解析结果转为JSON字符串存储
            st.session_state.get('user_id', 'Unknown')
        ]
        
        # 确保表格有列头，如果表格为空，先写入列头
        if not worksheet.row_values(1):
            worksheet.append_row(['timestamp', 'sentence', 'data_json', 'user'])

        worksheet.append_row(new_row)
    except Exception as e:
        st.error(f"保存记录到 Google Sheets 失败: {e}")

# --- 3. 页面配置 ---
st.set_page_config(
    page_title="日语语法伴侣 AI版 (云同步)",
    page_icon="🇯🇵",
    layout="centered",
    initial_sidebar_state="collapsed"
)

hide_menu_style = """
        <style>
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        </style>
        """
st.markdown(hide_menu_style, unsafe_allow_html=True)

# 初始化 session_state
if 'user_id' not in st.session_state:
    st.session_state['user_id'] = '用户A'

# --- 4. 核心功能：AI 分析 (保持不变) ---
def analyze_with_ai(text):
    prompt = f"""
    请作为一位专业的日语老师，分析以下日语句子：
    “{text}”
    
    请输出一个严格的 JSON 格式列表，包含以下字段：
    - "word": 原文单词
    - "reading": 罗马音 (Romaji)
    - "pos_meaning": 词性及中文含义 (例如：动词 / 决定)
    - "grammar": 详细语法说明 (例如：てしまう的口语缩略形式)
    - "standard": 标准形式/书面语 (例如：てしまいます)

    请确保输出是合法的 JSON 数组格式，不要包含 Markdown 标记。
    """
    
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(clean_text)
    except Exception as e:
        return [{"word": "错误", "pos_meaning": f"AI分析失败: {e}"}]


# --- 5. 界面 UI ---
st.title("🇯🇵 日语语法伴侣 (云同步 AI Pro)")

# 侧边栏，用于输入昵称
st.session_state['user_id'] = st.sidebar.text_input("输入你的昵称 (用于历史记录):", value=st.session_state['user_id'])


# 输入区
with st.container():
    sentence = st.text_area("输入日语:", height=80, placeholder="例如：決めちゃいますからね")
    
    if st.button("✨ AI 深度解析", type="primary"):
        if not sentence:
            st.warning("请输入句子")
        else:
            with st.spinner('AI 老师正在分析语法 (约需3秒)...'):
                # 调用 AI
                result_data = analyze_with_ai(sentence)
                
                # 写入 Google Sheets (只有成功解析才写入)
                if 'word' in result_data[0] and '错误' not in result_data[0]['word']:
                    save_record(sentence, result_data)
                
                # 显示结果
                st.success("解析完成！")
                st.markdown("### 📝 深度拆解")
                
                df = pd.DataFrame(result_data)
                column_config = {
                    "word": "部分 (日文)",
                    "reading": "读音 (罗马字)",
                    "pos_meaning": "品词 / 意味",
                    "grammar": "语法说明",
                    "standard": "标准形式"
                }
                st.dataframe(
                    df, 
                    column_config=column_config,
                    use_container_width=True,
                    hide_index=True
                )

st.divider()

# 历史记录
st.subheader("📚 学习足迹 (云同步)")

# 从 Google Sheets 读取历史记录并显示
history_df = load_history()

if not history_df.empty and 'timestamp' in history_df.columns:
    st.dataframe(
        history_df[['timestamp', 'sentence', 'user']],
        use_container_width=True,
        hide_index=True,
        column_config={
            "timestamp": "时间 (东八区)",
            "sentence": "查询句子",
            "user": "用户"
        }
    )
    
else:
    st.info("历史记录加载失败或表格为空。请检查 Google Sheets 共享设置和配置。")
