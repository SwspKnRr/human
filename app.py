import time
import re
from typing import List, Set, Optional, Literal

import requests
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st


# -----------------------------
# 전역 설정
# -----------------------------

# 아주 기본적인 불용어 (필요할 때 추가해가면 됨)
DEFAULT_STOPWORDS: Set[str] = {
    "그냥", "근데", "그리고", "또", "좀", "이거", "저거", "거의",
    "지금", "오늘", "내일", "어제", "그럼", "제발",
    "the", "and", "or", "but", "a", "an", "to", "of",
}

# 숫자/영문/한글/초성까지 허용
TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ]+")


# -----------------------------
# 1. 텍스트 토큰화
# -----------------------------

def tokenize_text(
    text: str,
    stopwords: Optional[Set[str]] = None,
    min_len: int = 2,
) -> List[str]:
    """
    게시글 텍스트를 단어 리스트로 변환.
    - 영문은 소문자로
    - min_len 이하 토큰 제거
    - stopwords 제거
    """
    if not isinstance(text, str):
        return []
    stopwords = stopwords or set()
    tokens: List[str] = []

    for match in TOKEN_PATTERN.finditer(text):
        token = match.group(0)

        # 영문은 소문자로
        if re.fullmatch(r"[A-Za-z]+", token):
            token = token.lower()

        if len(token) < min_len:
            continue
        if token in stopwords:
            continue

        tokens.append(token)

    return tokens


# -----------------------------
# 2. 디씨 미주갤 크롤러 (간단 버전)
# -----------------------------

def crawl_dc_minor(
    gallery_id: str,
    start_page: int,
    end_page: int,
    delay: float = 1.0,
) -> pd.DataFrame:
    """
    디시 마이너 갤러리(list → 글 본문)를 간단 크롤링.

    - gallery_id 예: 'us_stock' (실제 갤 주소 확인 필요)
    - start_page, end_page: 리스트 페이지 범위 (1부터 시작)
    - 너무 큰 범위 넣으면 오래 걸리고, 사이트에 부담 줄 수 있으니 적당히.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; stock-sentiment-bot/0.1; +https://example.com)"
    }

    rows = []

    for page in range(start_page, end_page + 1):
        list_url = f"https://gall.dcinside.com/mgallery/board/lists/?id={gallery_id}&page={page}"
        try:
            res = requests.get(list_url, headers=headers, timeout=10)
            res.raise_for_status()
        except Exception as e:
            st.warning(f"리스트 페이지 {page} 요청 실패: {e}")
            continue

        soup = BeautifulSoup(res.text, "html.parser")

        # 게시글 행 선택 (클래스명은 실제 HTML 보고 필요하면 조정)
        trs = soup.select("tr.ub-content.us-post") or soup.select("tr.ub-content")

        for tr in trs:
            # 제목, 링크
            a_tag = tr.select_one("a.ub-word")
            if a_tag is None:
                continue

            title = a_tag.get_text(strip=True)
            href = a_tag.get("href")
            if not href:
                continue

            # 링크 보정
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = "https://gall.dcinside.com" + href
            post_url = href

            # 작성 시각 (리스트에 있는 경우)
            date_td = tr.select_one("td.gall_date")
            if date_td is None:
                timestamp_text = ""
            else:
                # 보통 title 속성에 전체 시각, 텍스트에는 시/날짜 일부만 있음
                timestamp_text = date_td.get("title") or date_td.get_text(strip=True)

            # 글 본문 요청
            content_text = ""
            try:
                time.sleep(delay)
                pres = requests.get(post_url, headers=headers, timeout=10)
                pres.raise_for_status()
                psoup = BeautifulSoup(pres.text, "html.parser")
                # 본문 영역 (역시 실제 HTML 보고 클래스명 조정 가능)
                content_div = psoup.select_one("div.write_div")
                if content_div:
                    content_text = content_div.get_text(separator=" ", strip=True)
            except Exception as e:
                st.warning(f"본문 요청 실패: {post_url}, 오류: {e}")

            rows.append(
                {
                    "timestamp_raw": timestamp_text,
                    "title": title,
                    "content": content_text,
                    "url": post_url,
                    "page": page,
                }
            )

        time.sleep(delay)

    if not rows:
        return pd.DataFrame(columns=["timestamp", "title", "content", "url", "page"])

    df = pd.DataFrame(rows)

    # timestamp 파싱 (형식이 다를 수 있어서 몇 가지 패턴 시도)
    def parse_ts(x: str):
        import datetime as dt
        x = (x or "").strip()
        for fmt in ("%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M", "%Y.%m.%d", "%Y-%m-%d"):
            try:
                return dt.datetime.strptime(x, fmt)
            except Exception:
                continue
        return pd.NaT

    df["timestamp"] = df["timestamp_raw"].apply(parse_ts)
    df = df.dropna(subset=["timestamp"]).reset_index(drop=True)

    return df[["timestamp", "title", "content", "url", "page"]]


# -----------------------------
# 3. 일자별 단어 통계 만들기
# -----------------------------

def build_daily_word_stats(
    df_posts: pd.DataFrame,
    stopwords: Optional[Set[str]] = None,
    min_len: int = 2,
) -> pd.DataFrame:
    """
    raw posts DataFrame → (date, word) 단위 일자별 통계로 변환
    """
    if df_posts.empty:
        return pd.DataFrame(
            columns=["date", "word", "count", "freq", "total_words", "total_posts"]
        )

    df = df_posts.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.date

    # title + content 합치기
    df["text"] = (
        df.get("title", "").fillna("").astype(str)
        + " "
        + df.get("content", "").fillna("").astype(str)
    )

    df["tokens"] = df["text"].apply(
        lambda x: tokenize_text(x, stopwords=stopwords or DEFAULT_STOPWORDS, min_len=min_len)
    )
    df["token_count"] = df["tokens"].apply(len)

    daily_stats = []

    for date, grp in df.groupby("date"):
        total_posts = len(grp)
        total_words = int(grp["token_count"].sum())

        exploded = grp[["tokens"]].explode("tokens")
        exploded = exploded.dropna(subset=["tokens"])

        if exploded.empty:
            continue

        word_group = exploded.groupby("tokens", as_index=False).size()
        word_group = word_group.rename(columns={"tokens": "word", "size": "count"})

        if total_words > 0:
            word_group["freq"] = word_group["count"] / total_words
        else:
            word_group["freq"] = 0.0

        word_group["date"] = date
        word_group["total_words"] = total_words
        word_group["total_posts"] = total_posts

        daily_stats.append(word_group)

    if not daily_stats:
        return pd.DataFrame(
            columns=["date", "word", "count", "freq", "total_words", "total_posts"]
        )

    df_daily = pd.concat(daily_stats, ignore_index=True)
    df_daily = df_daily[["date", "word", "count", "freq", "total_words", "total_posts"]]
    return df_daily


# -----------------------------
# 4. 조회 함수들
# -----------------------------

def get_range_word_stats(
    df_daily: pd.DataFrame,
    start_date: str,
    end_date: str,
    min_days: int = 1,
    top_n: int = 50,
    sort_by: Literal["sum_count", "avg_freq", "max_freq"] = "sum_count",
) -> pd.DataFrame:
    """
    특정 기간 [start_date, end_date] 내에서 단어별 집계
    """
    df = df_daily.copy()
    if not pd.api.types.is_string_dtype(df["date"]):
        df["date"] = df["date"].astype(str)

    mask = (df["date"] >= start_date) & (df["date"] <= end_date)
    sub = df.loc[mask]

    if sub.empty:
        return pd.DataFrame()

    grouped = (
        sub.groupby("word")
        .agg(
            sum_count=("count", "sum"),
            days_appeared=("date", "nunique"),
            avg_freq=("freq", "mean"),
            max_freq=("freq", "max"),
        )
        .reset_index()
    )

    grouped = grouped[grouped["days_appeared"] >= min_days]

    if grouped.empty:
        return grouped

    if sort_by == "sum_count":
        grouped = grouped.sort_values("sum_count", ascending=False)
    elif sort_by == "avg_freq":
        grouped = grouped.sort_values("avg_freq", ascending=False)
    elif sort_by == "max_freq":
        grouped = grouped.sort_values("max_freq", ascending=False)
    else:
        raise ValueError(f"invalid sort_by: {sort_by}")

    if top_n and top_n > 0:
        grouped = grouped.head(top_n)

    return grouped


def get_day_word_stats(
    df_daily: pd.DataFrame,
    target_date: str,
    min_count: int = 1,
    top_n: int = 100,
    sort_by: Literal["count", "freq"] = "count",
) -> pd.DataFrame:
    """
    특정 날짜의 단어 분포 조회
    """
    df = df_daily.copy()
    if not pd.api.types.is_string_dtype(df["date"]):
        df["date"] = df["date"].astype(str)

    sub = df[df["date"] == target_date]

    if sub.empty:
        return pd.DataFrame()

    sub = sub[sub["count"] >= min_count]

    if sub.empty:
        return sub

    if sort_by == "count":
        sub = sub.sort_values("count", ascending=False)
    elif sort_by == "freq":
        sub = sub.sort_values("freq", ascending=False)
    else:
        raise ValueError(f"invalid sort_by: {sort_by}")

    if top_n and top_n > 0:
        sub = sub.head(top_n)

    return sub.reset_index(drop=True)


# -----------------------------
# 5. Streamlit UI
# -----------------------------

def main():
    st.set_page_config(page_title="디씨 미주갤 단어 관찰실", layout="wide")
    st.title("📊 디씨 미국 주식 마이너 갤러리 · 단어 관찰 실험실 (V1)")

    # ----------------- 사이드바: 데이터 준비 -----------------
    st.sidebar.header("1. 데이터 준비")

    st.sidebar.markdown("**옵션 A. CSV 업로드 (raw_posts)**")
    uploaded = st.sidebar.file_uploader("raw_posts CSV 업로드", type=["csv"])

    st.sidebar.markdown("---")
    st.sidebar.markdown("**옵션 B. 앱에서 직접 크롤링 (실험용)**")
    gallery_id = st.sidebar.text_input("갤러리 ID", value="stockus")
    start_page = st.sidebar.number_input("시작 페이지", min_value=1, value=1, step=1)
    end_page = st.sidebar.number_input("끝 페이지", min_value=1, value=2, step=1)
    delay = st.sidebar.number_input("요청 간격(초)", min_value=0.0, value=1.0, step=0.5)

    crawl_button = st.sidebar.button("디씨에서 크롤링 실행")

    df_posts: Optional[pd.DataFrame] = None

    # CSV 업로드 우선
    if uploaded is not None:
        df_posts = pd.read_csv(uploaded)
        st.success(f"CSV 업로드 완료: {len(df_posts)} rows")

    # 크롤링 실행 시
    if crawl_button:
        with st.spinner("디씨 미주갤에서 글 수집 중... (페이지 수가 많으면 오래 걸림)"):
            df_crawled = crawl_dc_minor(
                gallery_id=gallery_id,
                start_page=int(start_page),
                end_page=int(end_page),
                delay=float(delay),
            )
        if df_crawled.empty:
            st.error("크롤링 결과가 비어 있습니다. 갤러리 ID / 페이지 범위를 확인하세요.")
        else:
            st.success(f"크롤링 완료: {len(df_crawled)} posts")
            st.dataframe(df_crawled.head())
            if df_posts is None:
                df_posts = df_crawled
            else:
                # 업로드 + 크롤링 같이 쓰고 싶을 수도 있으니 합치기
                df_posts = pd.concat([df_posts, df_crawled], ignore_index=True)

    if df_posts is None or df_posts.empty:
        st.info("좌측에서 CSV를 업로드하거나, 크롤링을 먼저 실행하세요.")
        return

    # ----------------- 일자별 단어 통계 -----------------
    st.markdown("### 2. 일자별 단어 통계 생성")

    if st.checkbox("일자별 단어 통계 새로 계산하기", value=True):
        with st.spinner("일자별 단어 통계 계산 중..."):
            df_daily = build_daily_word_stats(df_posts)
        if df_daily.empty:
            st.error("일자별 단어 통계를 만들 수 없습니다. 데이터 내용을 확인하세요.")
            return
        st.success(f"완료: {df_daily['date'].nunique()}일, {len(df_daily)} (date, word) rows")
        st.session_state["df_daily"] = df_daily
    else:
        df_daily = st.session_state.get("df_daily")
        if df_daily is None or df_daily.empty:
            st.warning("저장된 df_daily가 없습니다. 통계를 한 번 계산해 주세요.")
            return

    # ----------------- 탭: 기간 / 일자 모드 -----------------
    tab_range, tab_day = st.tabs(["📅 기간 단어 빈도", "📆 특정 날짜 단어 분포"])

    # ----- 탭 1: 기간 단어 빈도 -----
    with tab_range:
        st.subheader("기간 단어 빈도")

        col1, col2 = st.columns(2)
        min_date = pd.to_datetime(df_daily["date"]).min()
        max_date = pd.to_datetime(df_daily["date"]).max()
        with col1:
            start = st.date_input("시작 날짜", value=min_date, min_value=min_date, max_value=max_date)
        with col2:
            end = st.date_input("끝 날짜", value=max_date, min_value=min_date, max_value=max_date)

        col3, col4, col5 = st.columns(3)
        with col3:
            min_days = st.number_input("최소 등장 일수", min_value=1, value=1)
        with col4:
            top_n = st.number_input("표시 단어 수 (Top N)", min_value=10, max_value=300, value=50, step=10)
        with col5:
            sort_by = st.selectbox("정렬 기준", ["sum_count", "avg_freq", "max_freq"])

        if st.button("기간 단어 빈도 조회"):
            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")
            stats = get_range_word_stats(
                df_daily,
                start_date=start_str,
                end_date=end_str,
                min_days=int(min_days),
                top_n=int(top_n),
                sort_by=sort_by,  # type: ignore[arg-type]
            )
            if stats.empty:
                st.warning("조건에 맞는 단어가 없습니다.")
            else:
                st.write(f"선택 기간: {start_str} ~ {end_str}")
                st.dataframe(stats)

                st.markdown("#### 상위 단어 막대 그래프 (sum_count 기준)")
                chart_data = stats.set_index("word")["sum_count"]
                st.bar_chart(chart_data)

    # ----- 탭 2: 특정 날짜 단어 분포 -----
    with tab_day:
        st.subheader("특정 날짜 단어 분포")

        all_dates = sorted(pd.to_datetime(df_daily["date"]).unique())
        default_date = all_dates[-1] if all_dates else None
        target = st.date_input("날짜 선택", value=default_date)

        col1, col2, col3 = st.columns(3)
        with col1:
            min_count = st.number_input("최소 등장 횟수", min_value=1, value=3)
        with col2:
            top_n_day = st.number_input("표시 단어 수 (Top N)", min_value=10, max_value=300, value=50, step=10)
        with col3:
            sort_by_day = st.selectbox("정렬 기준", ["count", "freq"])

        if st.button("해당 날짜 단어 분포 조회"):
            t_str = target.strftime("%Y-%m-%d")
            day_stats = get_day_word_stats(
                df_daily,
                target_date=t_str,
                min_count=int(min_count),
                top_n=int(top_n_day),
                sort_by=sort_by_day,  # type: ignore[arg-type]
            )
            if day_stats.empty:
                st.warning("조건에 맞는 단어가 없습니다.")
            else:
                st.write(f"선택 날짜: {t_str}")
                st.dataframe(day_stats)

                st.markdown("#### 단어 막대 그래프")
                chart_data = day_stats.set_index("word")["count"]
                st.bar_chart(chart_data)


if __name__ == "__main__":
    main()
