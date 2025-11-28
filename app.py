import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import time
import random
import datetime
import yfinance as yf
import mplfinance as mpf
import matplotlib.pyplot as plt

# ---------------------------------------------------------
# 설정 및 유틸리티
# ---------------------------------------------------------
st.set_page_config(
    page_title="디씨 심리 vs 주가 분석기",
    page_icon="📈",
    layout="wide"
)

# 한글 폰트 설정 (Matplotlib)
plt.rcParams['font.family'] = 'Malgun Gothic'
plt.rcParams['axes.unicode_minus'] = False

def simple_tokenizer(text):
    """간단한 띄어쓰기 기반 토크나이저 (KoNLPy 의존성 제거)"""
    text = re.sub(r"[^가-힣a-zA-Z0-9\s]", " ", text)
    return text.split()

# ---------------------------------------------------------
# 크롤링 함수 (V10 로직 이식)
# ---------------------------------------------------------
def crawl_dc(gallery_id, gallery_type, start_page, end_page, is_fast_mode):
    base_url = "https://gall.dcinside.com"
    if gallery_type == "minor":
        list_url = f"{base_url}/mgallery/board/lists/"
        view_url = f"{base_url}/mgallery/board/view/"
    elif gallery_type == "mini":
        list_url = f"{base_url}/mini/board/lists/"
        view_url = f"{base_url}/mini/board/view/"
    else: # major
        list_url = f"{base_url}/board/lists/"
        view_url = f"{base_url}/board/view/"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": base_url,
        "Connection": "keep-alive"
    }
    
    session = requests.Session()
    session.headers.update(headers)
    
    rows = []
    
    # Streamlit 상태 표시줄
    status_text = st.empty()
    progress_bar = st.progress(0)
    total_pages = end_page - start_page + 1

    for idx, page in enumerate(range(start_page, end_page + 1)):
        status_text.text(f"🔍 {page}페이지 수집 중... ({gallery_id})")
        
        try:
            res = session.get(list_url, params={'id': gallery_id, 'page': page}, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")
            
            trs = soup.select("tbody tr")
            if not trs: trs = soup.select("tr")
            
            count_in_page = 0
            for tr in trs:
                # 제목 태그 찾기
                a_tag = tr.select_one("a.ub-word")
                if not a_tag:
                    links = tr.select("a")
                    for l in links:
                        href = l.get("href", "")
                        if "board/view" in href and "no=" in href:
                            a_tag = l
                            break
                if not a_tag: continue

                title = a_tag.get_text(strip=True)
                link = a_tag.get("href")
                
                if "공지" in title or "설문" in title: continue
                
                # 날짜 가져오기
                dt = tr.select_one("td.gall_date")
                date_str = ""
                if dt: date_str = dt.get("title") or dt.get_text(strip=True)

                # 본문 수집 (스피드 모드 아닐 때만)
                content = ""
                if not is_fast_mode:
                    match = re.search(r'no=([0-9]+)', link)
                    if match:
                        post_no = match.group(1)
                        post_link = f"{view_url}?id={gallery_id}&no={post_no}"
                        try:
                            time.sleep(random.uniform(0.1, 0.3))
                            pr = session.get(post_link, timeout=5)
                            ps = BeautifulSoup(pr.text, "html.parser")
                            cd = ps.select_one("div.write_div")
                            if cd: content = cd.get_text(separator=" ", strip=True)
                        except: pass
                
                rows.append({
                    "raw_date": date_str,
                    "title": title,
                    "content": content
                })
                count_in_page += 1
                
            time.sleep(random.uniform(0.5, 0.8)) # 차단 방지 딜레이
            progress_bar.progress((idx + 1) / total_pages)
            
        except Exception as e:
            st.error(f"Error on page {page}: {e}")
            
    status_text.text(f"✅ 수집 완료! 총 {len(rows)}개 게시글.")
    progress_bar.empty()
    
    return pd.DataFrame(rows)

def analyze_data(df):
    """데이터프레임을 받아 날짜별 단어 빈도 분석"""
    def parse_date(x):
        x = str(x).strip()
        if re.match(r"\d{2}:\d{2}", x): return datetime.datetime.now().date()
        for fmt in ["%Y.%m.%d", "%Y-%m-%d"]:
            try: return datetime.datetime.strptime(x, fmt).date()
            except: continue
        return datetime.datetime.now().date()

    if 'raw_date' in df.columns:
        df['date'] = df['raw_date'].apply(parse_date)
    else:
        df['date'] = datetime.datetime.now().date()

    stopwords = {"그냥", "근데", "진짜", "존나", "시발", "생각", "사람", "오늘", "지금", "주식", "매수", "매도", "정도", "때문", "이거", "저거", "어떻게", "왜", "다시", "하나", "뭐냐", "아니", "내가", "형들"}
    
    all_data = []
    for i, row in df.iterrows():
        text = f"{row['title']} {row['content']}"
        tokens = simple_tokenizer(text)
        tokens = [t for t in tokens if len(t) >= 2 and t not in stopwords]
        for t in tokens:
            all_data.append({"date": row['date'], "word": t})
            
    if not all_data: return pd.DataFrame()
    return pd.DataFrame(all_data).groupby(['date', 'word']).size().reset_index(name='count')

# ---------------------------------------------------------
# 메인 UI
# ---------------------------------------------------------

st.title("📈 디씨 심리 vs 주가 캔들 분석기")
st.markdown("크롤링 데이터와 Yahoo Finance 주가 데이터를 결합하여 **인간 지표**를 확인합니다.")

# 사이드바 설정
with st.sidebar:
    st.header("1. 수집 설정")
    gallery_id = st.text_input("갤러리 ID", "stockus")
    gallery_type = st.selectbox("갤러리 종류", ["minor", "major", "mini"], index=0)
    
    col1, col2 = st.columns(2)
    start_page = col1.number_input("시작 페이지", 1, 1000, 1)
    end_page = col2.number_input("끝 페이지", 1, 1000, 10)
    
    is_fast_mode = st.checkbox("⚡ 스피드 모드 (제목만)", value=True, help="체크하면 속도가 50배 빨라집니다.")
    
    if st.button("데이터 수집 시작", type="primary"):
        with st.spinner("데이터 수집 중..."):
            df_posts = crawl_dc(gallery_id, gallery_type, start_page, end_page, is_fast_mode)
            if not df_posts.empty:
                df_daily = analyze_data(df_posts)
                st.session_state['df_posts'] = df_posts
                st.session_state['df_daily'] = df_daily
                st.success("분석 완료!")
            else:
                st.error("데이터를 찾지 못했습니다.")

    st.markdown("---")
    st.header("2. 파일 관리")
    
    # 저장 기능
    if 'df_daily' in st.session_state and not st.session_state['df_daily'].empty:
        csv = st.session_state['df_daily'].to_csv(index=False).encode('utf-8-sig')
        st.download_button("💾 분석 데이터(CSV) 다운로드", csv, "sentiment_data.csv", "text/csv")
    
    # 불러오기 기능
    uploaded_file = st.file_uploader("📂 데이터 불러오기 (CSV)", type="csv")
    if uploaded_file is not None:
        try:
            df_loaded = pd.read_csv(uploaded_file)
            df_loaded['date'] = pd.to_datetime(df_loaded['date']).dt.date
            st.session_state['df_daily'] = df_loaded
            st.success(f"불러오기 성공! ({len(df_loaded)} rows)")
        except Exception as e:
            st.error(f"파일 읽기 오류: {e}")

# 메인 화면
if 'df_daily' in st.session_state and not st.session_state['df_daily'].empty:
    df_daily = st.session_state['df_daily']
    
    tab1, tab2 = st.tabs(["🕯️ 캔들 차트 분석", "📝 원본 데이터"])
    
    with tab1:
        st.subheader("심리 vs 주가 상관관계 분석")
        
        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            ticker = st.text_input("Yahoo Ticker", "TSLA", help="예: TSLA, NVDA, AAPL, BTC-USD")
        with c2:
            # 가장 많이 등장한 단어 자동 추천
            top_word = df_daily.groupby('word')['count'].sum().idxmax()
            keyword = st.text_input("분석할 키워드", top_word)
        with c3:
            st.write("") # 여백용
            st.write("") 
            draw_btn = st.button("차트 그리기")

        if draw_btn:
            # 데이터 준비
            word_df = df_daily[df_daily['word'] == keyword].copy()
            if word_df.empty:
                st.warning("해당 키워드의 데이터가 없습니다.")
            else:
                word_df['date'] = pd.to_datetime(word_df['date'])
                word_df = word_df.set_index('date').sort_index()
                
                # 날짜 범위 설정
                min_date = word_df.index.min() - datetime.timedelta(days=5)
                max_date = word_df.index.max() + datetime.timedelta(days=5)
                
                with st.spinner(f"{ticker} 주가 데이터 가져오는 중..."):
                    try:
                        stock_df = yf.download(ticker, start=min_date, end=max_date, progress=False)
                        
                        if stock_df.empty:
                            st.error("주가 데이터가 없습니다. 티커를 확인하세요.")
                        else:
                            # 데이터 병합
                            merged_df = stock_df.copy()
                            # MultiIndex 컬럼 처리 (yfinance 최신버전 대응)
                            if isinstance(merged_df.columns, pd.MultiIndex):
                                merged_df.columns = merged_df.columns.get_level_values(0)

                            merged_df['WordCount'] = word_df['count']
                            merged_df['WordCount'] = merged_df['WordCount'].fillna(0)
                            
                            # mplfinance 차트 생성
                            mc = mpf.make_marketcolors(up='red', down='blue', inherit=True)
                            s  = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=True)
                            
                            # 추가 플롯 (단어 빈도 막대)
                            ap = mpf.make_addplot(merged_df['WordCount'], type='bar', panel=1, color='purple', ylabel='Mentions')
                            
                            # Figure 객체 반환받기 (returnfig=True)
                            fig, axes = mpf.plot(
                                merged_df, 
                                type='candle', 
                                style=s, 
                                addplot=ap, 
                                volume=False, # 거래량 대신 단어 빈도 사용하므로 끔
                                returnfig=True,
                                panel_ratios=(2,1), # 상단 2 : 하단 1 비율
                                title=f"{ticker} Price vs '{keyword}' Sentiment",
                                figsize=(10, 8)
                            )
                            
                            # Streamlit에 표시
                            st.pyplot(fig)
                            
                    except Exception as e:
                        st.error(f"오류 발생: {e}")

    with tab2:
        st.subheader("수집된 데이터 확인")
        if 'df_posts' in st.session_state:
            st.dataframe(st.session_state['df_posts'])
        else:
            st.dataframe(df_daily)

else:
    st.info("👈 사이드바에서 데이터를 수집하거나 불러오세요.")