import streamlit as st
import pandas as pd
import google.generativeai as genai
from datetime import datetime
import json
import gspread
import pytz 
import time
from gtts import gTTS # 引入语音库
import io

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
            # 提取语言字段
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
            st.toast("⚠️ 未找到要删除的记录。", icon="⚠️")
            return False

        # 核心：按行号从大到小排序
        rows_to_delete.sort(reverse=True)
        
        success_count = 0
        for row_idx in rows_to_delete:
            worksheet.delete_rows(row_idx)
            success_count += 1
        
        st.toast(f"✅ 成功删除 {success_count} 条记录！", icon="🗑️")
        return True
            
    except Exception as e:
        st.error(f"批量删除失败: {e}")
        return False

# 列名映射
COLUMN_MAPPING = {
    "word": "单词/原文",
    "reading": "发音",
    "pos_meaning": "词性 / 含义", 
    "grammar": "语法说明",
    "standard": "原型"
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
    
    history_df = load_history() 
    
    # 重新应用当前的过滤逻辑，确保全选只针对当前可见的记录
    filter_lang = st.session_state.get('filter_language', None)
    search_query = st.session_state.get('search_query', '')
    
    filtered_df = history_df.copy()
    if filter_lang:
        filtered_df = filtered_df[filtered_df['language'] == filter_lang]
    if search_query:
        filtered_df = filtered_df[
            filtered_df['sentence'].str.contains(search_query, case=False, na=False) | 
            (filtered_df['data'].astype(str).str.contains(search_query, case=False, na=False))
        ]
        
    for ts in filtered_df['timestamp']:
        st.session_state.delete_selections[ts] = select_all_state
        if f"sel_{ts}" in st.session_state:
            st.session_state[f"sel_{ts}"] = select_all_state

def bulk_delete_callback(timestamps_to_delete):
    """删除按钮的回调函数"""
    if not timestamps_to_delete:
        st.toast("⚠️ 请至少选择一条记录进行删除。", icon="⚠️")
        return

    if delete_records_by_bulk(timestamps_to_delete):
        st.session_state.select_all = False
        st.session_state.delete_selections = {}
        time.sleep(1) 
        load_history.clear()
        # 回调结束后会自动刷新

def text_to_speech(text, lang_name):
    """使用 gTTS 生成语音，返回音频字节流"""
    # 简单的语言代码映射
    lang_map = {
        '英语': 'en', '日语': 'ja', '中文': 'zh-cn', '法语': 'fr', 
        '韩语': 'ko', '西班牙语': 'es', '德语': 'de', '俄语': 'ru', '意大利语': 'it'
    }
    # 默认使用英语，如果匹配不到
    lang_code = lang_map.get(lang_name, 'en') 
    
    try:
        tts = gTTS(text=text, lang=lang_code)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        return fp
    except Exception as e:
        print(f"TTS Error: {e}")
        return None

# --- 4. 核心功能：AI 分析 ---
def analyze_with_ai(text):
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
       - "reading": 发音注音 (英语请提供IPA音标，日语提供罗马音，中文提供拼音)
       - "pos_meaning": 词性及中文含义
       - "grammar": 简短语法说明 (时态、变位等)
       - "standard": 原型/标准形式 (如动词原形)

    请确保输出是合法的 JSON 格式。
    """
    
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.replace('```json', '').replace('```', '').strip()
        result = json.loads(clean_text)
        
        if "structure" not in result or "translation" not in result:
             return {"error": "AI返回格式不完整", "structure": []}
            
        return result
        
    except Exception as e:
        return {"error": f"AI分析失败: {e}", "structure": []}

# --- 3. 页面配置 ---
st.set_page_config(
    page_title="全能语言伴侣",
    page_icon="🌍",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# 时尚的 UI 样式
st.markdown("""
<style>
    /* 隐藏默认菜单 */
    #MainMenu {visibility: hidden;}
    header {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* 表格自动换行 */
    td { white-space: normal !important; word-wrap: break-word !important; }
    
    /* 卡片式设计 */
    .stApp { background-color: #fafafa; }
    .css-1r6slb0 { background-color: #ffffff; padding: 2rem; border-radius: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
    
    /* 语言标签 */
    .lang-tag {
        background-color: #e3f2fd;
        color: #1976d2;
        padding: 4px 10px;
        border-radius: 16px;
        font-size: 13px;
        font-weight: 600;
        margin-right: 10px;
        display: inline-block;
    }
    
    /* 翻译框优化 */
    .trans-text { font-size: 18px; color: #333; line-height: 1.6; }
    
    /* 语法笔记 */
    .nuance-box {
        background-color: #fff8e1;
        border-left: 4px solid #ffc107;
        padding: 15px;
        border-radius: 4px;
        color: #5d4037;
    }
</style>
""", unsafe_allow_html=True)


if 'user_id' not in st.session_state:
    st.session_state['user_id'] = '用户A'

# --- 5. 侧边栏：个人中心与统计 ---
with st.sidebar:
    st.header("👤 个人中心")
    st.session_state['user_id'] = st.text_input("昵称", value=st.session_state['user_id'])
    
    st.markdown("---")
    st.subheader("📊 学习仪表盘")
    
    # 实时统计
    hist_df_stats = load_history()
    if not hist_df_stats.empty:
        total_queries = len(hist_df_stats)
        langs_learned = hist_df_stats['language'].nunique()
        top_lang = hist_df_stats['language'].mode()[0] if not hist_df_stats.empty else "无"
        
        c1, c2 = st.columns(2)
        c1.metric("总查询", total_queries)
        c2.metric("涉猎语言", langs_learned)
        st.metric("最爱语言", top_lang)
    else:
        st.info("暂无学习数据")
        
    st.markdown("---")
    st.markdown("💡 *Made with Streamlit & Gemini*")

# --- 6. 主界面 UI ---
st.title("🌍 全能语言伴侣")
st.caption("AI 驱动的多语种翻译、语法解析与发音助手")

# 输入区
with st.container():
    sentence = st.text_area("", height=100, placeholder="在此输入日语、英语、韩语或任何你想学习的语言句子...")
    
    col_btn, col_empty = st.columns([1, 3])
    with col_btn:
        analyze_btn = st.button("✨ 深度解析", type="primary", use_container_width=True)

    if analyze_btn:
        if not sentence:
            st.toast("⚠️ 请先输入句子！", icon="✍️")
        else:
            with st.spinner('🤖 AI 正在识别语言、生成发音并拆解语法...'):
                ai_result = analyze_with_ai(sentence)
                
                if "error" in ai_result:
                    st.error(ai_result["error"])
                else:
                    save_record(sentence, ai_result)
                    load_history.clear() # 清除缓存
                    
                    st.toast("✅ 解析完成！已保存到云端。", icon="🎉")
                    
                    # --- 结果展示区 (使用 Tabs 优化布局) ---
                    st.markdown("###") # Spacer
                    
                    # 生成语音
                    lang_name = ai_result.get('language', '英语')
                    audio_fp = text_to_speech(sentence, lang_name)
                    
                    # 顶部基本信息卡片
                    with st.container():
                        c_lang, c_audio = st.columns([0.2, 0.8])
                        with c_lang:
                            st.markdown(f"<span class='lang-tag'>{lang_name}</span>", unsafe_allow_html=True)
                        with c_audio:
                            if audio_fp:
                                st.audio(audio_fp, format='audio/mp3')
                    
                    # 使用 Tabs 分页展示，界面更清爽
                    tab1, tab2, tab3 = st.tabs(["📝 翻译与笔记", "🧩 结构拆解", "🔍 原始数据"])
                    
                    with tab1:
                        st.markdown("#### 🇨🇳 中文翻译")
                        st.markdown(f"<div class='trans-text'>{ai_result.get('translation', '')}</div>", unsafe_allow_html=True)
                        
                        st.markdown("#### 💡 语法与文化笔记")
                        st.markdown(f"""
                        <div class="nuance-box">
                            {ai_result.get('nuances', '无特殊说明').replace(chr(10), '<br>')}
                        </div>
                        """, unsafe_allow_html=True)
                        
                    with tab2:
                        st.markdown("#### 逐词拆解")
                        df = pd.DataFrame(ai_result.get('structure', []))
                        if not df.empty:
                            df_display = df.rename(columns=COLUMN_MAPPING)
                            st.table(df_display)
                        else:
                            st.info("无法生成结构表格")
                            
                    with tab3:
                        st.json(ai_result)

st.divider()

# --- 7. 学习足迹 (升级版) ---
st.subheader("📚 学习足迹")

# 初始化 session_state
if 'select_all' not in st.session_state:
    st.session_state.select_all = False
if 'delete_selections' not in st.session_state:
    st.session_state.delete_selections = {}
if 'search_query' not in st.session_state:
    st.session_state.search_query = ''
if 'filter_language' not in st.session_state:
    st.session_state.filter_language = None

# 加载数据
history_df = load_history()

if not history_df.empty and 'timestamp' in history_df.columns:
    
    # 🌟 顶部工具栏：筛选 + 导出
    col_filter, col_export = st.columns([0.8, 0.2])
    
    with col_filter:
        # 语言筛选按钮
        available_languages = history_df['language'].unique().tolist()
        if len(available_languages) > 0:
            # st.markdown("##### 语言筛选")
            cols = st.columns(len(available_languages) + 1)
            def set_lang_filter(lang):
                if st.session_state.filter_language == lang:
                    st.session_state.filter_language = None
                else:
                    st.session_state.filter_language = lang
                st.session_state.select_all = False 
                st.session_state.delete_selections = {}

            for i, lang in enumerate(available_languages):
                btn_type = "primary" if st.session_state.filter_language == lang else "secondary"
                if cols[i].button(lang, key=f"filter_btn_{lang}", type=btn_type):
                    set_lang_filter(lang)
                    st.rerun()
    
    with col_export:
        # 🌟 导出功能
        csv = history_df.to_csv(index=False).encode('utf-8-sig') # 解决中文乱码
        st.download_button(
            label="📥 导出 CSV",
            data=csv,
            file_name=f'learning_history_{datetime.now().strftime("%Y%m%d")}.csv',
            mime='text/csv',
        )

    # 🌟 执行过滤
    filtered_df = history_df.copy()
    if st.session_state.filter_language:
        filtered_df = filtered_df[filtered_df['language'] == st.session_state.filter_language]

    search_query = st.text_input("🔍 搜索历史:", placeholder="搜索原文、翻译或笔记...", key='search_query')
    if search_query:
        filtered_df = filtered_df[
            filtered_df['sentence'].str.contains(search_query, case=False, na=False) | 
            (filtered_df['data'].astype(str).str.contains(search_query, case=False, na=False))
        ]

    # --- 批量删除逻辑 ---
    if not filtered_df.empty:
        c_sel, c_del, c_space = st.columns([0.15, 0.35, 0.5])
        c_sel.checkbox("全选", key="select_all", on_change=update_selections)

        timestamps_to_delete = [
            ts for ts, is_checked in st.session_state.delete_selections.items() 
            if is_checked and ts in filtered_df['timestamp'].values
        ]
        
        c_del.button(
            "🗑️ 删除选中", 
            type="primary", 
            key="bulk_delete_main_btn",
            on_click=bulk_delete_callback,
            args=(timestamps_to_delete,)
        )

    # --- 列表显示 ---
    if filtered_df.empty:
        st.info("📭 没有找到匹配的记录")
    else:
        for index, item in filtered_df.iterrows():
            timestamp = item['timestamp']
            display_sentence = item['sentence'][:30] + '...' if len(item['sentence']) > 30 else item['sentence']
            lang_label = item.get('language', '未知')
            
            # 使用 container 增加卡片感
            with st.container():
                c_check, c_content = st.columns([0.05, 0.95])
                
                with c_check:
                    checkbox_key = f"sel_{timestamp}"
                    if checkbox_key not in st.session_state:
                        st.session_state[checkbox_key] = st.session_state.delete_selections.get(timestamp, False)
                    st.checkbox("", key=checkbox_key, on_change=update_individual_selection, args=(timestamp,), label_visibility="hidden")
                
                with c_content:
                    with st.expander(f"[{lang_label}] {display_sentence}"):
                        st.caption(f"🕒 {timestamp} | 👤 {item['user']}")
                        
                        data = item.get('data', {})
                        if data and "structure" in data:
                            # 这里也可以加 TTS
                            if st.button("🔊 朗读", key=f"tts_{timestamp}"):
                                audio_bytes = text_to_speech(item['sentence'], lang_label)
                                if audio_bytes:
                                    st.audio(audio_bytes, format='audio/mp3')

                            st.markdown(f"**翻译：** {data.get('translation', '')}")
                            
                            # 简单的 Tab 布局用于历史记录
                            h_tab1, h_tab2 = st.tabs(["结构表", "笔记"])
                            with h_tab1:
                                h_df = pd.DataFrame(data['structure'])
                                st.table(h_df.rename(columns=COLUMN_MAPPING))
                            with h_tab2:
                                st.info(data.get('nuances', '无笔记'))
                        else:
                            st.warning("数据无法解析")

else:
    st.info("🌟 欢迎使用！输入第一个句子开始你的语言之旅吧！")
