import streamlit as st
import pandas as pd
import google.generativeai as genai
from datetime import datetime
import json

# --- 1. 配置你的 AI ---
# ⚠️⚠️⚠️ 请在这里填入你在 Google AI Studio 申请的 API Key
GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"] 

# 配置 Gemini
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')
except Exception as e:
    st.error(f"API Key 配置错误: {e}")

# --- 2. 页面配置 ---
st.set_page_config(
    page_title="日语语法解析 AI版",
    page_icon="🇯🇵",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# 隐藏右上角菜单的样式 (解决你的汉化需求)
hide_menu_style = """
        <style>
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        </style>
        """
st.markdown(hide_menu_style, unsafe_allow_html=True)

# 初始化历史记录
if 'history' not in st.session_state:
    st.session_state['history'] = []

# --- 3. 核心功能：AI 分析 ---
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
        # 清理返回的文本，确保是纯 JSON
        clean_text = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(clean_text)
    except Exception as e:
        return [{"word": "错误", "pos_meaning": f"AI分析失败: {e}"}]

# --- 4. 界面 UI ---
st.title(" 语法解析 (AI Pro)")

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
                
                # 保存历史
                timestamp = datetime.now().strftime("%m-%d %H:%M")
                st.session_state['history'].insert(0, {
                    "time": timestamp,
                    "sentence": sentence,
                    "data": result_data
                })
                
                # 显示结果
                st.success("解析完成！")
                st.markdown("### 📝 深度拆解")
                
                # 转换为表格显示，并重命名列头以匹配你的截图需求
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
st.subheader("📚 学习足迹")
for item in st.session_state['history']:
    with st.expander(f"🕒 {item['time']} | {item['sentence'][:10]}..."):
        st.info(item['sentence'])
        df_hist = pd.DataFrame(item['data'])

        st.dataframe(df_hist, use_container_width=True, hide_index=True)
