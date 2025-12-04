import streamlit as st
import pandas as pd
import google.generativeai as genai
from datetime import datetime
import json
import gspread
import pytz 
import math # 引入 math 库，虽然在这里没用到，但保持代码清洁

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
SHEET_URL = "https://docs.google.com/spreadsheets/d/1xrXmiV5yEYIC4lDfgjk79vQDNVHYZugW6XUReZbHWjY/edit?gid=0#gid=0" 

# 替换你 app_mobile.py 中的 wrap_text 函数
def wrap_text(text, width=12):
    """将文本切分为列表，Streamlit ListColumn 会强制每段新起一行"""
    if not isinstance(text, str):
        return [text] # 确保返回列表
    
    # 返回一个包含 12 字符片段的列表
    return [text[i:i+width] for i in range(0, len(text), width)]
@st.cache_resource(ttl=3600) # 缓存连接
def get_sheets_client():
    try:
        if "GCP_JSON_STRING" in st.secrets:
            key_dict = json.loads(st.secrets["GCP_JSON_STRING"])
            gc = gspread.service_account_from_dict(key_dict)
            return gc
        elif "gcp_service_account" in st.secrets:
            gcp_sa = st.secrets["gcp_service_account"]
            gc = gspread.service_account_from_dict(gcp_sa)
            return gc
        else:
            st.warning("未找到 Google Cloud 凭证 (GCP_JSON_STRING)。")
            return None
    except Exception as e:
        st.error(f"Google Sheets 认证失败: {e}")
        return None

def load_history():
    """从 Google Sheets 读取历史记录，并将 JSON 字符串解析回 Python 对象"""
    gc = get_sheets_client()
    if not gc: return pd.DataFrame()
    
    try:
        spreadsheet = gc.open_by_url(SHEET_URL)
        worksheet = spreadsheet.sheet1
        df = pd.DataFrame(worksheet.get_all_records())
        
        if 'data_json' in df.columns:
            # 关键步骤：将 data_json 这一列的 JSON 字符串解析成 Python 列表/字典
            df['data'] = df['data_json'].apply(lambda x: json.loads(x) if x else [])
            df = df.drop(columns=['data_json']) # 移除原始 JSON 字符串列
            
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
        
        tz = pytz.timezone('Asia/Shanghai')
        timestamp_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        new_row = [
            timestamp_str,
            sentence,
            json.dumps(result_data, ensure_ascii=False), 
            st.session_state.get('user_id', 'Unknown')
        ]
        
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

# 隐藏右上角菜单的样式
hide_menu_style = """
        <style>
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        </style>
        """
st.markdown(hide_menu_style, unsafe_allow_html=True)


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
        result = json.loads(clean_text)
        
        if not isinstance(result, list) or not result:
            return [{"word": "错误", "pos_meaning": "AI未能返回有效的语法解析结果。请尝试使用不同的句子或检查网络连接。"}]
            
        return result
        
    except json.JSONDecodeError as e:
        error_msg = f"AI返回格式错误，请稍后再试。原始错误：{e}"
        if len(response.text) > 200:
             error_msg += f" ... AI返回内容片段: {response.text[:200]}..."
        return [{"word": "错误", "pos_meaning": error_msg}]

    except Exception as e:
        return [{"word": "错误", "pos_meaning": f"AI分析失败: {e}"}]

# --- 5. 界面 UI ---
st.title("🇯🇵 日语语法伴侣 (云同步 AI Pro)")

st.session_state['user_id'] = st.sidebar.text_input("输入你的昵称 (用于历史记录):", value=st.session_state['user_id'])

# 替换你 app_mobile.py 中的 COLUMN_CONFIG
# 🌟 关键：使用 ListColumn 强制显示列表内容，达到多行效果
COLUMN_CONFIG = {
    "word": "部分 (日文)",
    "reading": "读音 (罗马字)",
    "pos_meaning": st.column_config.ListColumn(
        "品词 / 意味",
        # ListColumn 默认会垂直显示列表中的每个元素，实现换行
        width="medium" 
    ), 
    "grammar": st.column_config.ListColumn(
        "语法说明",
        width="large"
    ),
    "standard": "标准形式"
}

# 输入区
with st.container():
    sentence = st.text_area("输入日语:", height=80, placeholder="例如：決めちゃいますからね")
    
    if st.button("✨ AI 深度解析", type="primary"):
        if not sentence:
            st.warning("请输入句子")
        else:
            with st.spinner('AI 老师正在分析语法 (约需3秒)...'):
                result_data = analyze_with_ai(sentence)
                
                # 写入 Google Sheets (只有成功解析才写入)
                if result_data and 'word' in result_data[0] and '错误' not in result_data[0]['word']:
                    save_record(sentence, result_data)
                
                # 显示结果
                st.success("解析完成！")
                st.markdown("### 📝 深度拆解")
                
                # 🌟 关键：对当前解析结果进行强制换行处理
                wrapped_data = []
                for item in result_data:
                    # 仅对目标列进行换行处理
                    item['pos_meaning'] = wrap_text(item.get('pos_meaning', ''), width=12)
                    item['grammar'] = wrap_text(item.get('grammar', ''), width=12)
                    wrapped_data.append(item)
                    
                df = pd.DataFrame(wrapped_data)
                
                st.dataframe(
                    df, 
                    column_config=COLUMN_CONFIG,
                    use_container_width=True,
                    hide_index=True
                )

st.divider()

# 历史记录
st.subheader("📚 学习足迹 (云同步)")

history_df = load_history()

if not history_df.empty and 'timestamp' in history_df.columns:
    
    for index, item in history_df.iterrows():
        display_sentence = item['sentence'][:20] + '...' if len(item['sentence']) > 20 else item['sentence']
        
        with st.expander(f"🕒 {item['timestamp']} | 用户: {item['user']} | 句子: {display_sentence}"):
            st.info(item['sentence'])
            
            if item['data']:
                # 🌟 关键：对历史记录数据进行强制换行处理
                wrapped_hist_data = []
                for hist_item in item['data']:
                    hist_item['pos_meaning'] = wrap_text(hist_item.get('pos_meaning', ''), width=12)
                    hist_item['grammar'] = wrap_text(hist_item.get('grammar', ''), width=12)
                    wrapped_hist_data.append(hist_item)
                    
                df_hist = pd.DataFrame(wrapped_hist_data)
                st.markdown("##### 详细解析结果")
                
                st.dataframe(
                    df_hist, 
                    column_config=COLUMN_CONFIG,
                    use_container_width=True, 
                    hide_index=True
                )
            else:
                st.warning("本次查询无有效的解析数据。")
    
else:
    st.info("历史记录加载失败或表格为空。请检查 Google Sheets 共享设置和配置。")


