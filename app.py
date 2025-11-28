import time
import random
import re
from typing import List, Set, Optional
import requests
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st
import plotly.express as px

# KoNLPy 임포트 시도
try:
    from konlpy.tag import Okt
except ImportError:
    st.error("KoNLPy가 설치되지 않았습니다. requirements.txt를 확인하세요.")
except Exception as e:
    # Java 미설치 등 오류 발생 시 무시하고 진행 (형태소 분석만 안됨)
    pass

# -----------------------------
# 1. 설정 및 토크나이저
# -----------------------------

DEFAULT_STOPWORDS: Set[str] = {
    "그냥", "근데", "그리고", "또", "좀", "이거", "저거", "거의",
    "지금", "오늘", "내일", "어제", "그럼", "제발", "진짜", "존나", 
    "시발", "병신", "형들", "형님", "개추", "비추", "정도", "때문", 
    "사람", "생각", "무슨", "어떻게", "왜", "다시", "계속", "나", "너", "우리",
    "하나", "지금", "보고", "가지", "달러", "주식", "시장", "매수", "매도"
}

@st.cache_resource
def get_tokenizer():
    try:
        return Okt()
    except:
        return None

def tokenize_text_korean(text: str, stopwords: Optional[Set[str]] = None, min_len: int = 2) -> List[str]:
    if not isinstance(text, str):
        return []
    stopwords = stopwords or set()
    cleaned_text = re.sub(r"[^가-힣a-zA-Z0-9\s]", " ", text)
    
    okt = get_tokenizer()
    if okt:
        try:
            nouns = okt.nouns(cleaned_text)
        except:
            nouns = cleaned_text.split()
    else:
        nouns = cleaned_text.split()

    final_tokens = []
    for n in nouns:
        if len(n) >= min_len and n not in stopwords:
            final_tokens.append(n)
    return final_tokens

# -----------------------------
# 2. 강력해진 크롤러 (V3)
# -----------------------------

def crawl_dc_v3(
    gallery_id: str,
    gallery_type: str, # 'minor', 'major', 'mini'
    start_page: int,
    end_page: int,
) -> pd.DataFrame:
    
    # 갤러리 타입에 따른 URL 구조 변경
    base_url = "https://gall.dcinside.com"
    if gallery_type == "minor":
        list_base = f"{base_url}/mgallery/board/lists/"
        view_base = f"{base_url}/mgallery/board/view/"
    elif gallery_type == "mini":
        list_base = f"{base_url}/mini/board/lists/"
        view_base = f"{base_url}/mini/board/view/"
    else: # major (정식 갤러리)
        list_base = f"{base_url}/board/lists/"
        view_base = f"{base_url}/board/view/"

    # 차단 방지를 위한 헤더 보강
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://gall.dcinside.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    rows = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    total_pages = end_page - start_page + 1
    
    # 세션 사용 (쿠키 유지)
    session = requests.Session()
    session.headers.update(headers)

    for idx, page in enumerate(range(start_page, end_page + 1)):
        status_text.text(f"🔍 {page}페이지 스캔 중... (갤러리: {gallery_id})")
        progress_bar.progress((idx) / total_pages)
        
        # 목록 요청
        params = {'id': gallery_id, 'page': page}
        try:
            res = session.get(list_base, params=params, timeout=10)
            if res.status_code != 200:
                st.warning(f"{page}페이지 접속 실패 (Code: {res.status_code})")
                continue
        except Exception as e:
            st.error(f"연결 오류: {e}")
            continue

        soup = BeautifulSoup(res.text, "html.parser")
        
        # 게시글 목록 찾기 (selector가 갤러리마다 다를 수 있어 여러 개 시도)
        trs = soup.select("tr.ub-content.us-post") 
        if not trs:
            trs = soup.select("tr.ub-content") # 일반적인 경우

        if not trs:
            # tr이 없으면 리스트 구조가 다른 것 (또는 차단됨)
            if "게시물이 없습니다" in res.text:
                st.info(f"{page}페이지에 게시물이 없습니다.")
            elif "location.replace" in res.text:
                st.error("🚨 디시인사이드 접근이 차단되었습니다. 잠시 후 다시 시도하세요.")
                break
            continue

        for tr in trs:
            # 공지사항 필터링 (선택 사항)
            if "공지" in tr.get_text():
                continue

            a_tag = tr.select_one("a.ub-word")
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            link_href = a_tag.get("href")
            
            # 링크에서 글 번호(no) 추출하여 상세 주소 생성
            # href 예: /mgallery/board/view/?id=stockus&no=1234&page=1
            if not link_href: 
                continue
                
            match = re.search(r'no=([0-9]+)', link_href)
            if match:
                post_no = match.group(1)
                post_url = f"{view_base}?id={gallery_id}&no={post_no}"
            else:
                continue

            # 날짜
            date_td = tr.select_one("td.gall_date")
            timestamp_str = date_td.get("title") or date_td.get_text(strip=True) if date_td else ""

            # 본문 수집 (속도 위해 0.3~1.0초 딜레이)
            content_text = ""
            try:
                time.sleep(random.uniform(0.3, 0.8))
                pres = session.get(post_url, timeout=5)
                if pres.status_code == 200:
                    psoup = BeautifulSoup(pres.text, "html.parser")
                    content_div = psoup.select_one("div.write_div")
                    if content_div:
                        content_text = content_div.get_text(separator=" ", strip=True)
            except:
                pass

            rows.append({
                "timestamp_str": timestamp_str,
                "title": title,
                "content": content_text,
                "url": post_url
            })

    progress_bar.progress(1.0)
    status_text.text("수집 종료")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    
    # 날짜 파싱
    def parse_date(x):
        x = str(x).strip()
        # 오늘 날짜는 14:30 처럼 시간만 나옴 -> 오늘 날짜 붙여줌
        if re.match(r"\d{2}:\d{2}", x):
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            return pd.to_datetime(f"{today} {x}")
        
        for fmt in ["%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d"]:
            try:
                return pd.to_datetime(x, format=fmt)
            except:
                continue
        return pd.NaT

    import datetime
    df["timestamp"] = df["timestamp_str"].apply(parse_date)
    df = df.dropna(subset=["timestamp"])
    df["date"] = df["timestamp"].dt.date
    
    return df

# -----------------------------
# 3. 통계 및 메인 UI
# -----------------------------

def build_stats_v3(df_posts: pd.DataFrame):
    all_rows = []
    
    # 진행바
    prog = st.progress(0)
    total = len(df_posts)
    
    for i, row in df_posts.iterrows():
        if i % 5 == 0: prog.progress(min(i/total, 1.0))
        text = str(row["title"]) + " " + str(row["content"])
        tokens = tokenize_text_korean(text, stopwords=DEFAULT_STOPWORDS)
        for t in tokens:
            all_rows.append({"date": row["date"], "word": t})
            
    prog.progress(1.0)
    
    if not all_rows: return pd.DataFrame()
    
    df_t = pd.DataFrame(all_rows)
    df_daily = df_t.groupby(["date", "word"]).size().reset_index(name="count")
    
    daily_total = df_t.groupby("date").size().reset_index(name="total_words")
    df_daily = df_daily.merge(daily_total, on="date")
    df_daily["freq"] = df_daily["count"] / df_daily["total_words"]
    
    return df_daily

def main():
    st.set_page_config(page_title="주식 심리 분석기 V3", layout="wide")
    st.title("📊 디씨 갤러리 분석기 V3")
    st.caption("갤러리 타입 선택 기능 추가로 수집 오류 해결")

    if "df_daily" not in st.session_state: st.session_state["df_daily"] = pd.DataFrame()
    if "df_posts" not in st.session_state: st.session_state["df_posts"] = pd.DataFrame()

    with st.sidebar:
        st.header("1. 수집 설정")
        
        # 갤러리 ID
        gal_id = st.text_input("갤러리 ID", value="stockus")
        
        # ⚠️ 여기가 핵심: 갤러리 타입 선택
        gal_type = st.radio("갤러리 종류 (중요)", 
                            ["minor", "major", "mini"], 
                            index=0,
                            format_func=lambda x: "마이너 갤러리" if x=="minor" else ("정식 갤러리" if x=="major" else "미니 갤러리"))
        st.info("※ '미주갤'은 마이너, '코스피/비트코인'은 정식입니다.")

        c1, c2 = st.columns(2)
        sp = c1.number_input("시작 페이지", 1, 1000, 1)
        ep = c2.number_input("끝 페이지", 1, 1000, 3)

        if st.button("데이터 수집 시작"):
            with st.spinner("데이터 수집 중..."):
                df = crawl_dc_v3(gal_id, gal_type, sp, ep)
            
            if df.empty:
                st.error("수집된 데이터가 없습니다! 갤러리 ID나 종류를 확인하세요.")
            else:
                st.success(f"{len(df)}개 글 수집 성공!")
                st.session_state["df_posts"] = df
                
                with st.spinner("단어 분석 중..."):
                    stats = build_stats_v3(df)
                    st.session_state["df_daily"] = stats

    # 메인 화면
    df_d = st.session_state["df_daily"]
    
    if not df_d.empty:
        tab1, tab2 = st.tabs(["트렌드 차트", "원본 데이터"])
        
        with tab1:
            words = sorted(df_d["word"].unique())
            picks = st.multiselect("추적할 단어", words, default=words[:5] if len(words)>5 else words)
            
            if picks:
                sub = df_d[df_d["word"].isin(picks)].sort_values("date")
                fig = px.line(sub, x="date", y="count", color="word", markers=True)
                st.plotly_chart(fig, use_container_width=True)
                
        with tab2:
            st.dataframe(st.session_state["df_posts"])
    else:
        st.info("좌측 사이드바에서 수집을 시작해주세요.")

if __name__ == "__main__":
    main()