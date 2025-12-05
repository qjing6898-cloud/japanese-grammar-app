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
            # 移除原始 JSON 字符串列 (保留其他列以便筛选)
            # df = df.drop(columns=['data_json']) 
            
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

def delete_record(timestamp_to_delete):
    """根据时间戳删除 Google Sheets 中的记录"""
    gc = get_sheets_client()
    if not gc: return False
    
    try:
        spreadsheet = gc.open_by_url(SHEET_URL)
        worksheet = spreadsheet.sheet1
        
        # 获取第一列（时间戳列）的所有值
        timestamps = worksheet.col_values(1)
        
        # 查找要删除的时间戳所在的行号 (注意：gspread行号从1开始，且列表索引从0开始)
        try:
            # timestamps列表包含表头，所以索引要小心处理
            row_index = timestamps.index(timestamp_to_delete) + 1
            worksheet.delete_rows(row_index)
            return True
        except ValueError:
            st.error("未找到对应记录，可能已被删除。")
            return False
            
    except Exception as e:
        st.error(f"删除失败: {e}")
        return False

# --- 3. 页面配置 ---
st.set_page_config(
    page_title="日语语法伴侣 AI版 (云同步)",
    page_icon="🇯🇵",
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
        </style>
        """
st.markdown(hide_menu_style, unsafe_allow_html=True)


if 'user_id' not in st.session_state:
    st.session_state['user_id'] = '用户A'

# --- 4. 核心功能：AI 分析 (升级版) ---
def analyze_with_ai(text):
    # 🌟 提示词升级：增加翻译和语法应用
    prompt = f"""
    请作为一位专业的日语老师，分析以下日语句子：
    “{text}”
    
    请输出一个严格的 JSON 格式对象，包含以下三个字段：
    1. "translation": 句子的中文翻译。
    2. "nuances": 一个字符串，详细解释句子中的惯用语、语气、断句逻辑或特定语法应用（类似“语法笔记”）。
    3. "structure": 一个列表，包含逐词拆解，每个元素包含：
       - "word": 原文单词
       - "reading": 罗马音
       - "pos_meaning": 词性及中文含义
       - "grammar": 简短语法说明
       - "standard": 标准形式

    示例 JSON 结构:
    {{
        "translation": "中文翻译...",
        "nuances": "这里使用了...的惯用型...",
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

# 列名映射
COLUMN_MAPPING = {
    "word": "单词",
    "reading": "读音",
    "pos_meaning": "品词 / 意味", 
    "grammar": "语法说明",
    "standard": "标准形式"
}

# --- 5. 界面 UI ---
st.title("🇯🇵 日语语法伴侣 (Pro Max)")

st.session_state['user_id'] = st.sidebar.text_input("输入你的昵称:", value=st.session_state['user_id'])

# 输入区
with st.container():
    sentence = st.text_area("输入日语:", height=80, placeholder="例如：決めちゃいますからね")
    
    if st.button("✨ AI 深度解析", type="primary"):
        if not sentence:
            st.warning("请输入句子")
        else:
            with st.spinner('AI 老师正在翻译和拆解 (约需5秒)...'):
                ai_result = analyze_with_ai(sentence)
                
                # 检查是否有错误
                if "error" in ai_result:
                    st.error(ai_result["error"])
                else:
                    # 写入 Google Sheets
                    save_record(sentence, ai_result)
                    
                    st.success("解析完成！")
                    
                    # 🌟 1. 显示中文翻译 (新增需求)
                    st.markdown(f"""
                    <div class="translation-box">
                        <b>🇨🇳 中文翻译：</b><br>{ai_result.get('translation', '')}
                    </div>
                    """, unsafe_allow_html=True)

                    # 🌟 2. 显示表格 (自动换行)
                    st.markdown("### 🧩 结构拆解")
                    df = pd.DataFrame(ai_result.get('structure', []))
                    if not df.empty:
                        df_display = df.rename(columns=COLUMN_MAPPING)
                        st.table(df_display)

                    # 🌟 3. 显示语法/惯用语详解 (新增需求)
                    st.markdown(f"""
                    <div class="grammar-box">
                        <b>💡 语法笔记与惯用语：</b><br>
                        {ai_result.get('nuances', '无特殊说明').replace(chr(10), '<br>')}
                    </div>
                    """, unsafe_allow_html=True)

st.divider()

# --- 6. 学习足迹 (含搜索与删除) ---
st.subheader("📚 学习足迹")

# 加载数据
history_df = load_history()

if not history_df.empty and 'timestamp' in history_df.columns:
    
    # 🌟 需求二：搜索框架
    search_query = st.text_input("🔍 搜索历史记录 (输入关键词):", placeholder="输入日语或翻译关键词...")
    
    # 执行过滤
    if search_query:
        # 模糊搜索：在句子列中查找
        filtered_df = history_df[history_df['sentence'].str.contains(search_query, case=False, na=False)]
    else:
        filtered_df = history_df

    # 显示记录
    if filtered_df.empty:
        st.info("没有找到匹配的记录。")
    else:
        # 遍历显示
        for index, item in filtered_df.iterrows():
            display_sentence = item['sentence'][:20] + '...' if len(item['sentence']) > 20 else item['sentence']
            
            # 使用 expander 包装单条记录
            with st.expander(f"🕒 {item['timestamp']} | {display_sentence}"):
                
                # 布局：左边显示内容，右边放删除按钮
                col1, col2 = st.columns([0.85, 0.15])
                
                with col1:
                    st.markdown(f"**原文：** {item['sentence']}")
                    
                    # 解析数据
                    data = item.get('data', {})
                    if data and "structure" in data:
                        # 显示翻译
                        st.markdown(f"**翻译：** {data.get('translation', '无')}")
                        
                        # 显示表格
                        st.markdown("---")
                        df_hist = pd.DataFrame(data['structure'])
                        st.table(df_hist.rename(columns=COLUMN_MAPPING))
                        
                        # 显示语法笔记
                        if data.get('nuances'):
                             st.info(f"💡 笔记：{data.get('nuances')}")
                    else:
                        st.warning("⚠️ 旧数据或解析失败，无法显示详细内容")

                # 🌟 需求三：删除功能
                with col2:
                    # 为每个按钮生成唯一的 key
                    btn_key = f"del_{item['timestamp']}"
                    if st.button("🗑️ 删除", key=btn_key, type="secondary"):
                        with st.spinner("删除中..."):
                            if delete_record(item['timestamp']):
                                st.success("已删除")
                                time.sleep(1) # 给一点时间让用户看到提示
                                st.rerun() # 刷新页面
                            else:
                                st.error("删除失败")

else:
    st.info("还没有学习记录，快去解析第一句日语吧！")
