import streamlit as st
import pandas as pd
import google.generativeai as genai
from datetime import datetime
import json
import gspread
import pytz 
import time
from gtts import gTTS 
import io

# --- 1. 配置你的 AI ---
try:
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

@st.cache_resource(ttl=3600)
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
            df['data'] = df['data_json'].apply(lambda x: json.loads(x) if x else {})
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
    checkbox_key = f"sel_{ts}"
    is_checked = st.session_state[checkbox_key] 
    st.session_state.delete_selections[ts] = is_checked
    if not is_checked and st.session_state.select_all:
        st.session_state.select_all = False

def update_selections():
    select_all_state = st.session_state.select_all
    
    history_df = load_history() 
    
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
    if not timestamps_to_delete:
        st.toast("⚠️ 请至少选择一条记录进行删除。", icon="⚠️")
        return

    if delete_records_by_bulk(timestamps_to_delete):
        st.session_state.select_all = False
        st.session_state.delete_selections = {}
        time.sleep(1) 
        load_history.clear()

def text_to_speech(text, lang_name):
    """使用 gTTS 生成语音，返回音频字节流"""
    lang_map = {
        '英语': 'en', '日语': 'ja', '中文': 'zh-cn', '法语': 'fr', 
        '韩语': 'ko', '西班牙语': 'es', '德语': 'de', '俄语': 'ru', '意大利语': 'it'
    }
    lang_code = lang_map.get(lang_name, 'en') 
    
    try:
        tts = gTTS(text=text, lang=lang_code)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        return fp
    except Exception as e:
        print(f"TTS Error: {e}")
        return None

# --- 4. 核心功能：AI 分析 (新增 Correction) ---
def analyze_with_ai(text):
    # 🌟 实用功能一：新增语法纠错和润色要求
    prompt = f"""
    请作为一位精通全球语言的语言学专家，分析以下文本：
    “{text}”
    
    请执行以下步骤：
    1. **自动识别** 输入文本的语言（例如：日语、英语、法语、韩语、中文、西班牙语等）。
    2. **检查和润色：** 检查原文是否有语法错误、表达不自然或不地道的地方。
        - 如果有错误或不地道，请提供一个**完全修正且地道的版本**。
        - 如果原文完美或非常地道，请返回原文。
    3. 将文本翻译成流畅的 **中文（简体）**。
    4. 分析文本中的语气、惯用语、语法结构或断句逻辑。
    5. 对文本进行逐词/逐结构拆解分析。

    请输出一个严格的 JSON 格式对象，包含以下五个字段：
    1. "language": 识别出的语言名称 (字符串，例如 "英语", "日语")。
    2. "correction": **修正/润色后的版本** (如果原文无错，则返回原文)。
    3. "translation": 中文翻译。
    4. "nuances": 详细的语法笔记、惯用语解释或文化背景说明。
    5. "structure": 一个列表，包含逐词拆解，每个元素包含：
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
    initial_sidebar_state="expanded" 
)

# 时尚的 UI 样式
st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    td { white-space: normal !important; word-wrap: break-word !important; }
    
    .stApp { background-color: #fafafa; }
    .main .block-container { 
        background-color: #ffffff; 
        padding: 2rem; 
        border-radius: 15px; 
        box-shadow: 0 4px 6px rgba(0,0,0,0.05); 
    }
    
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
    
    .trans-text { font-size: 18px; color: #333; line-height: 1.6; }
    
    .nuance-box {
        background-color: #fff8e1;
        border-left: 4px solid #ffc107;
        padding: 15px;
        border-radius: 4px;
        color: #5d4037;
    }
    /* 🌟 新增：修正框样式 */
    .correction-box {
        background-color: #e6ffe6; /* 浅绿色背景 */
        padding: 15px;
        border-radius: 8px;
        margin-top: 15px;
        border: 1px solid #4CAF50;
    }
    .correction-box strong { color: #2E7D32; }
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
            with st.spinner('🤖 AI 正在识别语言、检查错误并拆解语法...'):
                ai_result = analyze_with_ai(sentence)
                
                if "error" in ai_result:
                    st.error(ai_result["error"])
                else:
                    save_record(sentence, ai_result)
                    load_history.clear() 
                    
                    st.toast("✅ 解析完成！已保存到云端。", icon="🎉")
                    
                    # --- 结果展示区 (使用 Tabs 优化布局) ---
                    st.markdown("###")
                    
                    lang_name = ai_result.get('language', '英语')
                    correction = ai_result.get('correction', sentence) # 🌟 实用功能一
                    
                    audio_fp = text_to_speech(sentence, lang_name)
                    
                    # 顶部基本信息卡片
                    with st.container():
                        c_lang, c_audio = st.columns([0.2, 0.8])
                        with c_lang:
                            st.markdown(f"<span class='lang-tag'>{lang_name}</span>", unsafe_allow_html=True)
                        with c_audio:
                            if audio_fp:
                                st.audio(audio_fp.getvalue(), format='audio/mp3')
                            else:
                                st.warning("🔊 无法生成或播放音频，请检查网络或更换移动浏览器。")
                    
                    # 使用 Tabs 分页展示
                    tab1, tab2, tab3 = st.tabs(["📝 翻译与笔记", "🧩 结构拆解", "🔍 原始数据"])
                    
                    with tab1:
                        # 🌟 实用功能一：纠错结果展示
                        if correction != sentence:
                            st.markdown(f"""
                            <div class="correction-box">
                                <strong>⚠️ 修正/润色后的版本:</strong><br>{correction}
                                <br><br>
                                <strong>原文:</strong><br><del style="color: grey;">{sentence}</del>
                            </div>
                            """, unsafe_allow_html=True)
                        else:
                             st.markdown(f"""
                            <div class="correction-box">
                                <strong>✅ 恭喜!</strong><br>您的句子表达自然且准确。
                            </div>
                            """, unsafe_allow_html=True)

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

# --- 7. 学习足迹 (新增复习模式) ---
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
# 🌟 实用功能二：复习模式状态
if 'review_mode' not in st.session_state:
    st.session_state.review_mode = False

# 加载数据
history_df = load_history()

if not history_df.empty and 'timestamp' in history_df.columns:
    
    # 顶部工具栏：筛选 + 复习模式 + 导出
    col_filter, col_review, col_export = st.columns([0.6, 0.2, 0.2])
    
    with col_filter:
        available_languages = history_df['language'].unique().tolist()
        if len(available_languages) > 0:
            st.markdown("##### 语言筛选")
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

    with col_review:
        # 🌟 实用功能二：复习模式开关
        st.markdown("##### 复习模式")
        st.checkbox("开启闪卡", key='review_mode', value=st.session_state.review_mode)
    
    with col_export:
        st.markdown("##### 导出数据")
        csv = history_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 导出 CSV",
            data=csv,
            file_name=f'learning_history_{datetime.now().strftime("%Y%m%d")}.csv',
            mime='text/csv',
            use_container_width=True
        )

    # 执行过滤
    filtered_df = history_df.copy()
    if st.session_state.filter_language:
        filtered_df = filtered_df[filtered_df['language'] == st.session_state.filter_language]

    search_query = st.text_input("🔍 搜索历史:", placeholder="搜索原文、翻译或笔记...", key='search_query')
    if search_query:
        filtered_df = filtered_df[
            filtered_df['sentence'].str.contains(search_query, case=False, na=False) | 
            (filtered_df['data'].astype(str).str.contains(search_query, case=False, na=False))
        ]

    # 批量删除逻辑
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

    # 列表显示
    if filtered_df.empty:
        st.info("📭 没有找到匹配的记录")
    else:
        for index, item in filtered_df.iterrows():
            timestamp = item['timestamp']
            lang_label = item.get('language', '未知')
            
            # 复习模式下，只显示句子，不进行截断
            if st.session_state.review_mode:
                 display_sentence = item['sentence']
            else:
                 display_sentence = item['sentence'][:30] + '...' if len(item['sentence']) > 30 else item['sentence']
            
            with st.container():
                c_check, c_content = st.columns([0.05, 0.95])
                
                with c_check:
                    checkbox_key = f"sel_{timestamp}"
                    if checkbox_key not in st.session_state:
                        st.session_state[checkbox_key] = st.session_state.delete_selections.get(timestamp, False)
                    st.checkbox("", key=checkbox_key, on_change=update_individual_selection, args=(timestamp,), label_visibility="hidden")
                
                with c_content:
                    # 🌟 复习模式下的 Expander 标题
                    expander_label = f"[{lang_label}] {display_sentence}"
                    
                    # 🌟 实用功能二：复习模式内容控制
                    if st.session_state.review_mode:
                        # 复习模式下，默认折叠，只显示原文
                        with st.expander(expander_label):
                            # 使用 session state 动态控制答案显示
                            reveal_key = f'reveal_{timestamp}'
                            if reveal_key not in st.session_state:
                                st.session_state[reveal_key] = False
                                
                            if st.session_state[reveal_key]:
                                st.button("隐藏答案", key=f'hide_btn_{timestamp}', on_click=lambda: st.session_state.update({reveal_key: False}))
                                show_answer = True
                            else:
                                st.button("显示答案", key=f'show_btn_{timestamp}', type="primary", on_click=lambda: st.session_state.update({reveal_key: True}))
                                show_answer = False
                            
                            st.markdown("---")
                            
                            if show_answer:
                                st.caption(f"👤 {item['user']} | 🕒 {timestamp}")
                                data = item.get('data', {})
                                if data and "structure" in data:
                                    st.markdown(f"**翻译：** {data.get('translation', '')}")
                                    h_tab1, h_tab2 = st.tabs(["结构表", "笔记"])
                                    with h_tab1:
                                        h_df = pd.DataFrame(data['structure'])
                                        st.table(h_df.rename(columns=COLUMN_MAPPING))
                                    with h_tab2:
                                        st.info(data.get('nuances', '无笔记'))
                                else:
                                    st.warning("数据无法解析")
                    else:
                        # 正常模式下，展开即显示所有内容
                        with st.expander(expander_label):
                            st.caption(f"🕒 {timestamp} | 👤 {item['user']}")
                            data = item.get('data', {})
                            if data and "structure" in data:
                                if st.button("🔊 朗读", key=f"tts_{timestamp}"):
                                    audio_bytes = text_to_speech(item['sentence'], lang_label)
                                    if audio_bytes:
                                        st.audio(audio_bytes.getvalue(), format='audio/mp3')
                                    else:
                                        st.toast("🔊 移动端播放失败。", icon="⚠️")

                                st.markdown(f"**翻译：** {data.get('translation', '')}")
                                
                                # 🌟 修正对比（历史记录）
                                correction_hist = data.get('correction', item['sentence'])
                                if correction_hist != item['sentence']:
                                    st.info(f"💡 **修正版本:** {correction_hist}")

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
