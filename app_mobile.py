import streamlit as st
import pandas as pd
import google.generativeai as genai
from datetime import datetime
import json
import gspread
import pytz 
import time

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
# ⚠️⚠️⚠️ 请保持你已经配置好的 Google Sheets 完整网址不变！
SHEET_URL = "https://docs.google.com/spreadsheets/d/1xrXmiV5yEYIC4lDfgjk79vQDNVHYZugW6XUReZbHWjY/edit?gid=0#gid=0" 

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

# 缓存数据读取
@st.cache_data(ttl=60) 
def load_history():
    """从 Google Sheets 读取历史记录"""
    gc = get_sheets_client()
    if not gc: return pd.DataFrame()
    
    try:
        spreadsheet = gc.open_by_url(SHEET_URL)
        worksheet = spreadsheet.sheet1
        df = pd.DataFrame(worksheet.get_all_records())
        
        if 'data_json' in df.columns:
            # 解析 JSON 字符串
            df['data'] = df['data_json'].apply(lambda x: json.loads(x) if x else {})
            
            # 🌟 新增：从 data 中提取语言字段，如果没有（老数据），默认为 "日语"
            df['language'] = df['data'].apply(lambda x: x.get('language', '日语') if isinstance(x, dict) else '未知')
            
        return df.iloc[::-1] # 倒序
    except gspread.exceptions.SpreadsheetNotFound:
        st.warning(f"Google 表格 '{SHEET_TITLE}' 不存在或无访问权限。")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"加载历史记录失败: {e}") 
        return pd.DataFrame()


def save_record(sentence, result_data):
    """将新的记录写入 Google Sheets"""
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

# 批量删除函数
def delete_records_by_bulk(timestamps_list):
    """根据时间戳列表批量删除 Google Sheets 中的记录"""
    gc = get_sheets_client()
    if not gc or not timestamps_list: return False
    
    try:
        spreadsheet = gc.open_by_url(SHEET_URL)
        worksheet = spreadsheet.sheet1
        
        timestamps_col = worksheet.col_values(1)
        
        rows_to_delete = []
        for ts in timestamps_list:
            try:
                row_index = timestamps_col.index(ts) + 1
                rows_to_delete.append(row_index)
            except ValueError:
                continue
        
        if not rows_to_delete:
            st.warning("未找到要删除的记录。")
            return False

        # 核心：按行号从大到小排序
        rows_to_delete.sort(reverse=True)
        
        success_count = 0
        for row_idx in rows_to_delete:
            worksheet.delete_rows(row_idx)
            success_count += 1
        
        st.success(f"成功删除 {success_count} 条记录。")
        return True
            
    except Exception as e:
        st.error(f"批量删除失败: {e}")
        return False

# 列名映射 (通用化，不再局限于日语)
COLUMN_MAPPING = {
    "word": "单词/原文",
    "reading": "发音 (音标/拼音/罗马音)",
    "pos_meaning": "词性 / 含义", 
    "grammar": "语法说明",
    "standard": "原型/标准形式"
}


# --- 辅助函数：状态同步 ---

def update_individual_selection(ts):
    """当单个复选框被点击时调用"""
    checkbox_key = f"sel_{ts}"
    is_checked = st.session_state[checkbox_key] 
    st.session_state.delete_selections[ts] = is_checked
    if not is_checked and st.session_state.select_all:
        st.session_state.select_all = False

def update_selections():
    """当点击全选时调用"""
    select_all_state = st.session_state.select_all
    
    # 重新获取当前筛选后的数据
    history_df = load_history() 
    
    # 1. 应用语言筛选
    filter_lang = st.session_state.get('filter_language', None)
    if filter_lang:
        history_df = history_df[history_df['language'] == filter_lang]
        
    # 2. 应用搜索关键词筛选
    search_query = st.session_state.get('search_query', '')
    if search_query:
        filtered_df = history_df[
            history_df['sentence'].str.contains(search_query, case=False, na=False) | 
            (history_df['data'].astype(str).str.contains(search_query, case=False, na=False))
        ]
    else:
        filtered_df = history_df
        
    for ts in filtered_df['timestamp']:
        st.session_state.delete_selections[ts] = select_all_state
        if f"sel_{ts}" in st.session_state:
            st.session_state[f"sel_{ts}"] = select_all_state

def bulk_delete_callback(timestamps_to_delete):
    """删除按钮的回调函数"""
    if not timestamps_to_delete:
        st.warning("请至少选择一条记录进行删除。")
        return

    if delete_records_by_bulk(timestamps_to_delete):
        st.session_state.select_all = False
        st.session_state.delete_selections = {}
        time.sleep(1) 
        load_history.clear()
        # st.rerun() # 回调结束后会自动刷新


# --- 4. 核心功能：AI 分析 (全语种升级版) ---
def analyze_with_ai(text):
    # 🌟 提示词大升级：支持自动识别语言
    prompt = f"""
    请作为一位精通全球语言的语言学专家，分析以下文本：
    “{text}”
    
    请执行以下步骤：
    1. **自动识别** 输入文本的语言（例如：日语、英语、法语、韩语、中文、西班牙语等）。
    2. 将文本翻译成流畅的 **中文（简体）**。
    3. 分析文本中的语气、惯用语、语法结构或断句逻辑。
    4. 对文本进行逐词/逐结构拆解分析。

    请输出一个严格的 JSON 格式对象，包含以下四个字段：
    1. "language": 识别出的语言名称 (字符串，例如 "英语", "日语")。
    2. "translation": 中文翻译。
    3. "nuances": 详细的语法笔记、惯用语解释或文化背景说明。
    4. "structure": 一个列表，包含逐词拆解，每个元素包含：
       - "word": 原文单词/词组
       - "reading": 发音注音 (英语请提供IPA音标，日语提供罗马音，中文提供拼音，其他语言提供相应的拉丁化发音)
       - "pos_meaning": 词性及中文含义
       - "grammar": 简短语法说明 (时态、变位等)
       - "standard": 原型/标准形式 (如动词原形)

    示例 JSON 结构:
    {{
        "language": "日语",
        "translation": "...",
        "nuances": "...",
        "structure": [
            {{ "word": "...", "reading": "...", "pos_meaning": "...", "grammar": "...", "standard": "..." }}
        ]
    }}

    请确保输出是合法的 JSON 格式，不要包含 Markdown 标记。
    """
    
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.replace('```json', '').replace('```', '').strip()
        result = json.loads(clean_text)
        
        # 简单验证结构
        if "structure" not in result or "translation" not in result:
             return {"error": "AI返回格式不完整", "structure": []}
            
        return result
        
    except Exception as e:
        return {"error": f"AI分析失败: {e}", "structure": []}

# --- 3. 页面配置 ---
st.set_page_config(
    page_title="全能语言伴侣 AI版",
    page_icon="🌍",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# 隐藏右上角菜单
hide_menu_style = """
        <style>
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        /* 强制 st.table 自动换行 */
        td { white-space: normal !important; word-wrap: break-word !important; }
        /* 优化翻译文本样式 */
        .translation-box {
            background-color: #f0f2f6;
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 20px;
            font-size: 16px;
            color: #31333F;
        }
        .grammar-box {
            background-color: #e8f4f9;
            padding: 15px;
            border-radius: 10px;
            margin-top: 10px;
            border-left: 5px solid #4da6ff;
        }
        /* 语言标签样式 */
        .lang-tag {
            background-color: #ffe6e6;
            color: #cc0000;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
            margin-bottom: 5px;
            display: inline-block;
        }
        </style>
        """
st.markdown(hide_menu_style, unsafe_allow_html=True)


if 'user_id' not in st.session_state:
    st.session_state['user_id'] = '用户A'

# --- 5. 界面 UI ---
st.title("🌍 全能语言伴侣 (AI Pro)")

st.session_state['user_id'] = st.sidebar.text_input("输入你的昵称:", value=st.session_state['user_id'])

# 输入区
with st.container():
    sentence = st.text_area("输入任何语言:", height=80, placeholder="例如：Hello world / Bonjour / 決めちゃいますからね")
    
    if st.button("✨ AI 深度解析", type="primary"):
        if not sentence:
            st.warning("请输入句子")
        else:
            with st.spinner('AI 正在识别语言并解析 (约需5秒)...'):
                ai_result = analyze_with_ai(sentence)
                
                if "error" in ai_result:
                    st.error(ai_result["error"])
                else:
                    save_record(sentence, ai_result)
                    load_history.clear()
                    
                    st.success(f"解析完成！识别为：{ai_result.get('language', '未知')}")
                    
                    st.markdown(f"""
                    <div class="translation-box">
                        <span class="lang-tag">{ai_result.get('language', '通用')}</span>
                        <b> 🇨🇳 中文翻译：</b><br>{ai_result.get('translation', '')}
                    </div>
                    """, unsafe_allow_html=True)

                    st.markdown("### 🧩 结构拆解")
                    df = pd.DataFrame(ai_result.get('structure', []))
                    if not df.empty:
                        df_display = df.rename(columns=COLUMN_MAPPING)
                        st.table(df_display)

                    st.markdown(f"""
                    <div class="grammar-box">
                        <b>💡 语法笔记与文化背景：</b><br>
                        {ai_result.get('nuances', '无特殊说明').replace(chr(10), '<br>')}
                    </div>
                    """, unsafe_allow_html=True)

st.divider()

# --- 6. 学习足迹 (含语言筛选、搜索与批量删除) ---
st.subheader("📚 学习足迹")

# 初始化 session_state
if 'select_all' not in st.session_state:
    st.session_state.select_all = False
if 'delete_selections' not in st.session_state:
    st.session_state.delete_selections = {}
if 'search_query' not in st.session_state:
    st.session_state.search_query = ''
if 'filter_language' not in st.session_state:
    st.session_state.filter_language = None  # None 表示显示全部

# 加载数据
history_df = load_history()

if not history_df.empty and 'timestamp' in history_df.columns:
    
    # 🌟 1. 自动生成语言筛选按钮
    # 获取历史记录中出现过的所有语言
    available_languages = history_df['language'].unique().tolist()
    
    if len(available_languages) > 0:
        st.markdown("**按语言筛选：**")
        
        # 动态创建列来放置按钮 (防止按钮换行太丑)
        # 这里使用一个简单的水平布局容器
        cols = st.columns(len(available_languages) + 1)
        
        # 定义一个回调函数来处理按钮点击
        def set_lang_filter(lang):
            if st.session_state.filter_language == lang:
                st.session_state.filter_language = None # 再次点击取消筛选
            else:
                st.session_state.filter_language = lang
            # 重置全选状态，因为列表变了
            st.session_state.select_all = False 
            st.session_state.delete_selections = {}

        # 渲染按钮
        # 渲染 "全部" 状态的指示 (可选，这里通过按钮颜色区分)
        for i, lang in enumerate(available_languages):
            # 检查当前语言是否被选中，给予不同的视觉提示 (通过 type='primary' 或 'secondary')
            btn_type = "primary" if st.session_state.filter_language == lang else "secondary"
            if cols[i].button(lang, key=f"filter_btn_{lang}", type=btn_type):
                set_lang_filter(lang)
                st.rerun()

    st.markdown("---")

    # 🌟 2. 执行多重过滤 (语言 + 搜索)
    filtered_df = history_df.copy()

    # (A) 语言过滤
    if st.session_state.filter_language:
        filtered_df = filtered_df[filtered_df['language'] == st.session_state.filter_language]

    # (B) 搜索过滤
    search_query = st.text_input(
        "🔍 搜索历史记录:", 
        placeholder="输入原文或翻译关键词...",
        key='search_query'
    )
    
    if search_query:
        filtered_df = filtered_df[
            filtered_df['sentence'].str.contains(search_query, case=False, na=False) | 
            (filtered_df['data'].astype(str).str.contains(search_query, case=False, na=False))
        ]

    # --- 批量删除按钮、全选/反选和处理逻辑 ---
    if not filtered_df.empty:
        col_select, col_delete_btn, col_placeholder = st.columns([0.15, 0.35, 0.5])

        col_select.checkbox(
            "全选",
            key="select_all",
            on_change=update_selections
        )

        timestamps_to_delete = [
            ts for ts, is_checked in st.session_state.delete_selections.items() 
            if is_checked and ts in filtered_df['timestamp'].values
        ]
        
        col_delete_btn.button(
            "🗑️ 批量删除选中项", 
            type="primary", 
            key="bulk_delete_main_btn",
            on_click=bulk_delete_callback,
            args=(timestamps_to_delete,)
        )

    # --- 显示记录 ---
    if filtered_df.empty:
        if search_query or st.session_state.filter_language:
            st.info("没有找到匹配的记录。")
        else:
            st.info("没有学习记录。")
    else:
        for index, item in filtered_df.iterrows():
            timestamp = item['timestamp']
            display_sentence = item['sentence'][:20] + '...' if len(item['sentence']) > 20 else item['sentence']
            lang_label = item.get('language', '未知')
            
            col_check, col_expander = st.columns([0.05, 0.95])
            
            with col_check:
                checkbox_key = f"sel_{timestamp}"
                if checkbox_key not in st.session_state:
                    st.session_state[checkbox_key] = st.session_state.delete_selections.get(timestamp, False)

                st.checkbox(
                    label="", 
                    key=checkbox_key, 
                    value=st.session_state.delete_selections.get(timestamp, False),
                    on_change=update_individual_selection,
                    args=(timestamp,),
                    label_visibility="hidden"
                )

            with col_expander:
                with st.expander(f"🕒 {timestamp} | [{lang_label}] {display_sentence}"):
                    
                    st.markdown(f"**操作人：** {item['user']}")
                    st.markdown(f"**原文：** {item['sentence']}")
                    
                    data = item.get('data', {})
                    if data and "structure" in data:
                        st.markdown("---")
                        st.markdown(f"**翻译：** {data.get('translation', '无')}")
                        
                        st.markdown(f"##### {lang_label}结构拆解")
                        df_hist = pd.DataFrame(data['structure'])
                        st.table(df_hist.rename(columns=COLUMN_MAPPING))
                        
                        if data.get('nuances'):
                             st.info(f"💡 笔记：{data.get('nuances')}")
                    else:
                        st.warning("⚠️ 旧数据或解析失败，无法显示详细内容")

else:
    st.info("还没有学习记录，快去输入第一句外语吧！")
