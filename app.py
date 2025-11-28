import time
import random
import re
from typing import List, Set, Optional, Literal
import datetime

import requests
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st
import plotly.express as px  # 시계열 차트를 예쁘게 그리기 위해 추가

# KoNLPy (형태소 분석기) 임포트
# Java가 설치되어 있어야 작동합니다.
try:
    from konlpy.tag import Okt
except ImportError:
    st.error("KoNLPy가 설치되지 않았습니다. 'pip install konlpy'를 실행하세요.")
except Exception as e:
    st.error(f"KoNLPy 초기화 오류 (Java 설치 확인 필요): {e}")

# -----------------------------
# 1. 설정 및 불용어
# -----------------------------

# 주식 커뮤니티용 확장 불용어
DEFAULT_STOPWORDS: Set[str] = {
    "그냥", "근데", "그리고", "또", "좀", "이거", "저거", "거의",
    "지금", "오늘", "내일", "어제", "그럼", "제발", "진짜", "존나", 
    "시발", "병신", "형들", "형님", "개추", "비추", "정도", "때문", 
    "사람", "생각", "무슨", "어떻게", "왜", "다시", "계속", "나", "너", "우리",
    "하나", "지금", "보고", "가지", "달러", "주식", "시장"
}

# -----------------------------
# 2. 텍스트 처리 (형태소 분석 적용)
# -----------------------------

@st.cache_resource
def get_tokenizer():
    """
    Okt 인스턴스는 로딩에 시간이 걸리므로 캐싱하여 사용
    """
    return Okt()

def tokenize_text_korean(
    text: str,
    stopwords: Optional[Set[str]] = None,
    min_len: int = 2,
) -> List[str]:
    """
    KoNLPy(Okt)를 사용하여 명사만 추출.
    - 교착어 특성상 단순 띄어쓰기가 아닌 '명사' 추출이 핵심.
    """
    if not isinstance(text, str):
        return []
    
    stopwords = stopwords or set()
    
    # 1. 기본적인 정제 (특수문자 제거 등)
    # 한글, 영문, 숫자만 남기고 제거
    cleaned_text = re.sub(r"[^가-힣a-zA-Z0-9\s]", " ", text)
    
    # 2. 형태소 분석 (명사 추출)
    try:
        okt = get_tokenizer()
        nouns = okt.nouns(cleaned_text) # 명사만 추출
    except Exception:
        # Java 오류 등으로 실패 시 간단한 split으로 대체 (Fall-back)
        nouns = cleaned_text.split()

    # 3. 영문 처리 (Okt는 영문을 잘 못 잡을 수 있으므로 별도 추출해서 합칠 수도 있음)
    # 여기서는 간단히 Okt 결과 + 원문의 영단어(소문자)를 병합하는 방식 사용
    english_tokens = re.findall(r"[a-zA-Z]+", text)
    english_tokens = [t.lower() for t in english_tokens]
    
    # 4. 최종 필터링
    final_tokens = []
    
    # 한글 명사 필터링
    for n in nouns:
        if len(n) >= min_len and n not in stopwords:
            final_tokens.append(n)
            
    # 영문 토큰 필터링
    for e in english_tokens:
        if len(e) >= min_len and e not in stopwords:
            final_tokens.append(e)

    return final_tokens


# -----------------------------
# 3. 크롤러 (차단 방지 기능 추가)
# -----------------------------

def crawl_dc_minor_v2(
    gallery_id: str,
    start_page: int,
    end_page: int,
    min_delay: float = 0.5,
    max_delay: float = 1.5,
) -> pd.DataFrame:
    """
    디시 마이너 갤러리 크롤링 (랜덤 딜레이 적용)
    """
    # User-Agent를 일반 브라우저처럼 위장
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    rows = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total_pages = end_page - start_page + 1
    
    for idx, page in enumerate(range(start_page, end_page + 1)):
        status_text.text(f"현재 {page}페이지 수집 중...")
        progress_bar.progress((idx) / total_pages)
        
        list_url = f"https://gall.dcinside.com/mgallery/board/lists/?id={gallery_id}&page={page}"
        
        try:
            res = requests.get(list_url, headers=headers, timeout=10)
            res.raise_for_status()
        except Exception as e:
            st.warning(f"페이지 {page} 접속 실패: {e}")
            continue

        soup = BeautifulSoup(res.text, "html.parser")
        trs = soup.select("tr.ub-content.us-post") or soup.select("tr.ub-content")

        for tr in trs:
            a_tag = tr.select_one("a.ub-word")
            if a_tag is None:
                continue

            title = a_tag.get_text(strip=True)
            href = a_tag.get("href")
            if not href:
                continue
            
            # 링크 보정
            if href.startswith("//"):
                post_url = "https:" + href
            elif href.startswith("/"):
                post_url = "https://gall.dcinside.com" + href
            else:
                post_url = href

            # 날짜
            date_td = tr.select_one("td.gall_date")
            timestamp_text = date_td.get("title") or date_td.get_text(strip=True) if date_td else ""

            # 본문 수집 (랜덤 딜레이)
            content_text = ""
            try:
                # 너무 빠르지 않게 쉼
                time.sleep(random.uniform(min_delay, max_delay))
                
                pres = requests.get(post_url, headers=headers, timeout=5)
                if pres.status_code == 200:
                    psoup = BeautifulSoup(pres.text, "html.parser")
                    content_div = psoup.select_one("div.write_div")
                    if content_div:
                        content_text = content_div.get_text(separator=" ", strip=True)
            except Exception:
                pass # 본문 실패해도 제목이라도 건짐

            rows.append({
                "timestamp_str": timestamp_text,
                "title": title,
                "content": content_text,
                "url": post_url
            })
            
        # 페이지 넘어갈 때도 딜레이
        time.sleep(random.uniform(min_delay, max_delay))

    progress_bar.progress(1.0)
    status_text.text("수집 완료!")
    
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    
    # 날짜 파싱 로직
    def parse_ts(x):
        x = str(x).strip()
        patterns = ["%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]
        for pat in patterns:
            try:
                return pd.to_datetime(x, format=pat)
            except:
                continue
        # 오늘 날짜(HH:mm)인 경우 처리 등은 생략하고 NaT 처리
        return pd.NaT

    df["timestamp"] = df["timestamp_str"].apply(parse_ts)
    # 날짜 파싱 실패한 행(오래된 글이나 형식 다른 글) 제거 혹은 오늘 날짜로 가정
    df = df.dropna(subset=["timestamp"])
    df["date"] = df["timestamp"].dt.date
    
    return df


# -----------------------------
# 4. 통계 생성 (일자별)
# -----------------------------

def build_stats_v2(df_posts: pd.DataFrame):
    """
    데이터프레임을 받아 (date, word) 빈도 테이블 생성
    """
    all_rows = []
    
    # 진행 상황 표시
    prog = st.progress(0)
    total_len = len(df_posts)
    
    for i, row in df_posts.iterrows():
        if i % 10 == 0:
            prog.progress(min(i / total_len, 1.0))
            
        full_text = str(row["title"]) + " " + str(row["content"])
        tokens = tokenize_text_korean(full_text, stopwords=DEFAULT_STOPWORDS)
        
        for token in tokens:
            all_rows.append({
                "date": row["date"],
                "word": token
            })
            
    prog.progress(1.0)
            
    if not all_rows:
        return pd.DataFrame()
        
    df_tokens = pd.DataFrame(all_rows)
    
    # 날짜별, 단어별 카운트
    df_daily = df_tokens.groupby(["date", "word"]).size().reset_index(name="count")
    
    # 해당 날짜의 총 단어 수 계산 (빈도율 freq 계산용)
    daily_total = df_tokens.groupby("date").size().reset_index(name="total_words")
    df_daily = df_daily.merge(daily_total, on="date", how="left")
    df_daily["freq"] = df_daily["count"] / df_daily["total_words"]
    
    return df_daily


# -----------------------------
# 5. 메인 UI
# -----------------------------

def main():
    st.set_page_config(page_title="주식 심리 분석기 V2", layout="wide")
    
    st.title("🧠 주식 커뮤니티 심리 분석기 V2")
    st.caption("디시인사이드 미주갤 데이터 기반 · KoNLPy 형태소 분석 · 시계열 트렌드 추적")

    # 세션 상태 초기화
    if "df_posts" not in st.session_state:
        st.session_state["df_posts"] = pd.DataFrame()
    if "df_daily" not in st.session_state:
        st.session_state["df_daily"] = pd.DataFrame()

    # --- 사이드바: 데이터 수집 ---
    with st.sidebar:
        st.header("1. 데이터 수집")
        
        # 갤러리 ID (기본값: 미주갤)
        gallery_id = st.text_input("갤러리 ID", value="stockus") 
        col1, col2 = st.columns(2)
        start_p = col1.number_input("시작 페이지", 1, 1000, 1)
        end_p = col2.number_input("종료 페이지", 1, 1000, 3) # 테스트용으로 적게 설정
        
        if st.button("데이터 크롤링 시작"):
            with st.spinner("디씨 방문 중... (랜덤 딜레이 적용됨)"):
                df_new = crawl_dc_minor_v2(gallery_id, start_p, end_p)
                
            if not df_new.empty:
                st.session_state["df_posts"] = df_new
                st.success(f"{len(df_new)}개 게시글 수집 완료!")
                
                # 수집 후 바로 분석 실행
                with st.spinner("형태소 분석(KoNLPy) 수행 중..."):
                    df_stats = build_stats_v2(df_new)
                    st.session_state["df_daily"] = df_stats
                st.success("분석 데이터 생성 완료!")
            else:
                st.error("수집된 데이터가 없습니다.")

    # --- 메인 화면 ---
    
    df_daily = st.session_state["df_daily"]

    if df_daily.empty:
        st.info("👈 사이드바에서 크롤링을 먼저 실행해주세요.")
        return

    # 탭 구성
    tab1, tab2, tab3 = st.tabs(["📊 주요 키워드(Bar)", "📈 심리/테마 트렌드(Line)", "📝 원본 데이터"])

    # 1. 주요 키워드 랭킹
    with tab1:
        st.subheader("기간 내 최다 언급 단어")
        
        top_n = st.slider("상위 N개 보기", 10, 50, 20)
        
        # 전체 기간 합산
        total_counts = df_daily.groupby("word")["count"].sum().reset_index()
        total_counts = total_counts.sort_values("count", ascending=False).head(top_n)
        
        fig = px.bar(total_counts, x="word", y="count", 
                     title=f"Top {top_n} 키워드", color="count")
        st.plotly_chart(fig, use_container_width=True)

    # 2. 트렌드 분석 (핵심 기능)
    with tab2:
        st.subheader("관심 키워드 시계열 추적")
        st.caption("특정 주식이나 감정 단어가 시간에 따라 어떻게 변하는지 확인하세요.")
        
        # 검색 기능
        all_words = sorted(df_daily["word"].unique())
        default_keywords = ["테슬라", "엔비디아", "롱", "숏", "졸업", "한강"]
        # 데이터에 있는 단어만 필터링
        valid_defaults = [w for w in default_keywords if w in all_words]
        
        selected_words = st.multiselect("추적할 단어를 선택/입력하세요", all_words, default=valid_defaults)
        
        if selected_words:
            # 선택된 단어만 필터링
            mask = df_daily["word"].isin(selected_words)
            chart_df = df_daily[mask].copy()
            
            # 날짜 정렬
            chart_df = chart_df.sort_values("date")
            
            # 라인 차트 그리기
            # y축을 'freq'(비율)로 하면 게시글 수가 다른 날짜끼리 비교하기 더 좋음
            metric = st.radio("지표 선택", ["count (단순 횟수)", "freq (언급 밀도)"], index=0)
            y_col = "count" if "count" in metric else "freq"
            
            fig2 = px.line(chart_df, x="date", y=y_col, color="word", markers=True,
                           title="키워드별 언급 추이 변화")
            st.plotly_chart(fig2, use_container_width=True)
            
            st.markdown("""
            **💡 분석 팁:**
            - **급등:** 평소 잠잠하던 종목이 갑자기 언급량이 폭발하면 '재료'가 떴거나 '과열' 징조입니다.
            - **감정:** '졸업'(수익실현), '한강'(손실) 같은 단어와 종목명의 추이를 겹쳐보세요.
            """)
        else:
            st.info("추적할 단어를 선택해주세요.")

    # 3. 데이터 확인
    with tab3:
        st.dataframe(st.session_state["df_posts"])

if __name__ == "__main__":
    main()