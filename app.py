import time
import random
import re
from typing import List, Set, Optional
import requests
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st
import plotly.express as px

# -----------------------------
# 설정 및 기초 함수
# -----------------------------
try:
    from konlpy.tag import Okt
except ImportError:
    st.error("KoNLPy 미설치")

DEFAULT_STOPWORDS = {
    "그냥", "근데", "그리고", "좀", "이거", "진짜", "존나", "시발", "병신", 
    "개추", "비추", "생각", "사람", "지금", "주식", "매수", "매도", "오늘"
}

@st.cache_resource
def get_tokenizer():
    try:
        return Okt()
    except:
        return None

def tokenize(text):
    try:
        okt = get_tokenizer()
        nouns = okt.nouns(re.sub(r"[^가-힣a-zA-Z0-9\s]", " ", text))
        return [n for n in nouns if len(n) >= 2 and n not in DEFAULT_STOPWORDS]
    except:
        return text.split()

# -----------------------------
# V4: 진단 기능을 포함한 크롤러
# -----------------------------
def crawl_debug(gallery_id, gallery_type, start_page, end_page):
    # 1. 헤더 설정 (최대한 사람처럼)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://gall.dcinside.com/",
        "Connection": "keep-alive"
    }

    session = requests.Session()
    session.headers.update(headers)
    
    # 2. 메인 페이지 먼저 방문해서 쿠키 획득 (차단 우회 시도)
    try:
        session.get("https://gall.dcinside.com")
    except:
        pass

    rows = []
    
    # URL 설정
    base_url = "https://gall.dcinside.com"
    if gallery_type == "minor":
        list_base = f"{base_url}/mgallery/board/lists/"
        view_base = f"{base_url}/mgallery/board/view/"
    elif gallery_type == "mini":
        list_base = f"{base_url}/mini/board/lists/"
        view_base = f"{base_url}/mini/board/view/"
    else:
        list_base = f"{base_url}/board/lists/"
        view_base = f"{base_url}/board/view/"

    progress = st.progress(0)
    
    # 디버그용 로그
    debug_log = []

    for idx, page in enumerate(range(start_page, end_page + 1)):
        params = {'id': gallery_id, 'page': page}
        
        try:
            res = session.get(list_base, params=params, timeout=10)
            
            # 디버그: 응답 상태 저장
            if idx == 0:
                debug_log.append(f"응답 코드: {res.status_code}")
                debug_log.append(f"URL: {res.url}")
                debug_log.append(f"HTML 앞부분(500자): {res.text[:500]}")
            
            soup = BeautifulSoup(res.text, "html.parser")
            
            # 게시글 행 찾기
            trs = soup.select("tr.ub-content.us-post")
            if not trs: trs = soup.select("tr.ub-content")

            if not trs:
                debug_log.append(f"{page}페이지: 게시글 테이블(tr)을 찾지 못함.")
                continue

            for tr in trs:
                if "공지" in tr.get_text(): continue
                
                a = tr.select_one("a.ub-word")
                if not a: continue
                
                title = a.get_text(strip=True)
                link = a.get("href")
                
                if not link: continue
                match = re.search(r'no=([0-9]+)', link)
                if match:
                    post_url = f"{view_base}?id={gallery_id}&no={match.group(1)}"
                    
                    # 상세 내용 수집 (생략 가능)
                    content = ""
                    try:
                        time.sleep(random.uniform(0.1, 0.5))
                        pr = session.get(post_url, timeout=5)
                        ps = BeautifulSoup(pr.text, "html.parser")
                        cd = ps.select_one("div.write_div")
                        if cd: content = cd.get_text(separator=" ", strip=True)
                    except: pass
                    
                    # 날짜 (간단처리)
                    import datetime
                    rows.append({
                        "date": datetime.datetime.now().date(), # 날짜 파싱 복잡해서 일단 오늘로 통일
                        "title": title,
                        "content": content
                    })
                    
        except Exception as e:
            debug_log.append(f"에러 발생: {e}")
            
        progress.progress((idx+1)/(end_page-start_page+1))

    return pd.DataFrame(rows), debug_log

# -----------------------------
# 메인 UI
# -----------------------------
def main():
    st.set_page_config("디씨 분석기 V4 (진단모드)")
    st.title("🕵️ 디씨 분석기 V4 (차단 진단)")
    
    with st.sidebar:
        gid = st.text_input("갤러리 ID", "stockus")
        gtype = st.radio("종류", ["minor", "major", "mini"])
        if st.button("수집 시작"):
            with st.spinner("접속 시도 중..."):
                df, logs = crawl_debug(gid, gtype, 1, 2)
                st.session_state['logs'] = logs
                st.session_state['df'] = df

    # 결과 화면
    if 'df' in st.session_state:
        df = st.session_state['df']
        logs = st.session_state.get('logs', [])

        if df.empty:
            st.error("❌ 데이터 수집 실패!")
            st.warning("디시인사이드가 접속을 차단했을 가능성이 높습니다. 아래 디버그 정보를 확인하세요.")
            
            with st.expander("🛠️ 디버그: 왜 실패했나요?", expanded=True):
                for log in logs:
                    st.text(log)
                    st.markdown("---")
                
                st.markdown("""
                ### 🔍 분석 결과
                1. **HTML에 'location.replace' 등이 보인다면?** -> 차단됨 (Bot Detection)
                2. **HTML이 정상적인데 데이터가 없다면?** -> 갤러리 ID나 종류(마이너/정식) 설정 실수
                3. **응답 코드가 403/404라면?** -> IP 차단
                
                **👉 해결책: 이 코드를 Streamlit Cloud가 아닌 '내 컴퓨터'에서 실행하세요.**
                """)
        else:
            st.success(f"✅ 성공! {len(df)}개 수집됨")
            st.dataframe(df)
            
            # 간단 분석
            all_text = " ".join(df['title'] + " " + df['content'])
            words = tokenize(all_text)
            st.write(pd.Series(words).value_counts().head(20))

if __name__ == "__main__":
    main()