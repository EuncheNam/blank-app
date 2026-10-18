import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="신규 권역 성장 기회 대시보드", layout="wide")
st.title("신규 권역 성장 기회 대시보드")                                  # ✏️ 6주차 메인 제목
st.caption("가입월 · 가입 채널 · 플랫폼별 | 2025.09 ~ 2026.07 가입자")     # ✏️ 6주차 부제

# ------------------------------------------------------------
# 0. 필요한 데이터만 추출 — 쓸 테이블과 컬럼만 메모리 DB에 올리고, 정제 테이블을 처음 한 번만 만듦
# ------------------------------------------------------------
DATA_DIR = "data"                          # 원본 CSV를 넣어 둔 폴더
NEEDED = {                                 # ✏️ 이번 대시보드에 필요한 테이블과 컬럼 (쿼리에 쓰는 것만)
    "app_events": ["event_datetime", "customer_id", "session_id", "event_name",
                   "platform", "traffic_source", "campaign_name"],
    "orders": ["customer_id", "order_datetime"],
}

@st.cache_resource
def get_db():                              # 필요한 컬럼만 메모리 DB에 올리고, 정제 테이블 events·odr을 생성(처음 한 번)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    for name, cols in NEEDED.items():
        # dtype=str: 고객 ID의 앞자리 0(0000002)이 숫자로 바뀌지 않게 글자로 읽음
        pd.read_csv(f"{DATA_DIR}/{name}.csv", usecols=cols, dtype=str).to_sql(name, con, index=False)
    # ✏️ 5주차 제외 규칙: 테스트 계정(T로 시작) 제외 · 이벤트 100건 이상인 비정상 세션 제외
    con.execute("""
        CREATE TABLE events AS
        SELECT *
          FROM app_events
         WHERE customer_id NOT LIKE 'T%'
           AND session_id NOT IN (SELECT session_id FROM app_events
                                   GROUP BY session_id HAVING COUNT(*) >= 100)
    """)
    con.execute("CREATE TABLE odr AS SELECT * FROM orders WHERE customer_id NOT LIKE 'T%'")
    # 고객별로 묶는 집계가 빨라지도록 customer_id에 인덱스를 붙임
    con.execute("CREATE INDEX idx_events_customer ON events(customer_id)")
    con.execute("CREATE INDEX idx_odr_customer ON odr(customer_id)")
    return con

# ------------------------------------------------------------
# 1. 데이터 준비 — 정제된 테이블을 SQL로 집계 (비율이 아니라 분자 · 분모 건수만)
# ------------------------------------------------------------
@st.cache_data
def query(sql):                            # SQL을 실행해 표로 돌려줌
    return pd.read_sql(sql, get_db())

# 고객별 가입일 · 가입 채널 · 플랫폼 (가입 이벤트 기준) - 두 쿼리 모두 사용
SQL_SIGNUP = """
su AS (
    SELECT customer_id,
           DATE(MIN(event_datetime))                    AS 가입일,
           MIN(COALESCE(campaign_name, traffic_source)) AS 가입채널,
           MIN(platform)                                AS 플랫폼
      FROM events
     WHERE event_name = 'sign_up'
     GROUP BY customer_id
)
"""

# ✏️ 가입월 × 가입 채널 × 플랫폼별 가입 후 30일 퍼널 (예제 Q1 · Q3)
SQL_SIGNUP_FUNNEL = f"""
WITH {SQL_SIGNUP},
ev AS (                                     -- 가입 후 30일 안의 행동
    SELECT DISTINCT e.customer_id, e.event_name
      FROM events e
      JOIN su ON e.customer_id = su.customer_id
     WHERE julianday(DATE(e.event_datetime)) - julianday(su.가입일) BETWEEN 0 AND 30
),
fo AS (                                     -- 고객별 첫 주문일
    SELECT customer_id, DATE(MIN(order_datetime)) AS 첫주문일
      FROM odr
     GROUP BY customer_id
)
SELECT strftime('%Y-%m', su.가입일) AS 가입월,
       su.가입채널,
       su.플랫폼,
       COUNT(DISTINCT su.customer_id)                                                         AS 가입자,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'view_restaurant' THEN su.customer_id END)    AS 식당조회,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'add_to_cart'     THEN su.customer_id END)    AS 장바구니,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'begin_checkout'  THEN su.customer_id END)    AS 결제시작,
       COUNT(DISTINCT CASE WHEN julianday(fo.첫주문일) - julianday(su.가입일) <= 30
                           THEN su.customer_id END)                                           AS 첫주문30일
  FROM su
  LEFT JOIN ev ON su.customer_id = ev.customer_id
  LEFT JOIN fo ON su.customer_id = fo.customer_id
 WHERE su.가입일 <= '2026-07-31'            -- 30일을 다 볼 수 있는 가입자만
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

# ✏️ 가입월 × 가입 채널 × 플랫폼별 30일 재주문 (예제 Q2, 분모가 첫 주문 고객이라 표를 따로 만듦)
SQL_REORDER = f"""
WITH {SQL_SIGNUP},
seq AS (
    SELECT customer_id, order_datetime,
           ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_datetime) AS 순번,
           LEAD(order_datetime) OVER (PARTITION BY customer_id ORDER BY order_datetime) AS 다음주문
      FROM odr
)
SELECT strftime('%Y-%m', su.가입일) AS 가입월,
       su.가입채널,
       su.플랫폼,
       COUNT(*)                                                                          AS 첫주문고객,
       SUM(CASE WHEN julianday(DATE(seq.다음주문)) - julianday(DATE(seq.order_datetime)) <= 30
                THEN 1 ELSE 0 END)                                                       AS 재주문30일
  FROM seq
  JOIN su ON seq.customer_id = su.customer_id
 WHERE seq.순번 = 1
   AND seq.order_datetime < '2026-08-01'    -- 30일을 다 볼 수 있는 첫 주문만
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

@st.cache_data
def load_data():
    return query(SQL_SIGNUP_FUNNEL), query(SQL_REORDER)

df, rd = load_data()                       # df: 가입 후 30일 퍼널 / rd: 30일 재주문

# 📌 AI 해설: Gemini가 만든 해설 (키는 "전체" 또는 가입 채널 · 플랫폼 이름과 똑같아야 함)
AI_NOTES = {
    "전체": "전체 가입자 2,774명의 30일 첫 주문 전환율은 69.9%이고, 가장 낮은 단계는 식당조회→장바구니(84.1%)입니다. 가입월별로 보면 2025-09–2025-12 가입월에는 74.3–76.6%였는데, 2026-01 가입월부터 63.5–71.7%로 낮아졌습니다. 반면 INSTA_첫주문쿠폰을 뺀 전환율은 같은 기간에도 71.9–78.3%여서, 전체 전환율이 낮아진 것은 2026-01 가입월부터 들어온 INSTA_첫주문쿠폰 가입자가 합산된 영향으로 보입니다.",
    "Android": "Android 가입자 1,502명의 30일 첫 주문 전환율은 69.2%로 전체(69.9%)보다 0.7%p 낮고, 가장 낮은 단계는 식당조회→장바구니(83.8%)입니다. iOS와 비교하면 30일 첫 주문 전환율 1.5%p, 30일 재주문율(66.6% 대 68.2%) 1.6%p 차이로 모든 지표의 차이가 2%p 이내입니다. 따라서 성과 차이는 플랫폼보다 채널 사이에서 더 크게 나는 것으로 보이며, 플랫폼별 가입월 추이는 따로 확인이 필요합니다.",
    "iOS": "iOS 가입자 1,272명의 30일 첫 주문 전환율은 70.7%로 전체(69.9%)보다 0.8%p 높고, 가장 낮은 단계는 식당조회→장바구니(84.5%)입니다. 30일 재주문율도 68.2%로 전체(67.4%)보다 0.8%p 높지만, Android와의 차이는 모든 지표에서 2%p 이내입니다. iOS만의 뚜렷한 강점이나 약점은 이 숫자들로는 드러나지 않는 것으로 보이며, 채널 구성이 플랫폼별로 다른지 확인이 필요합니다.",
    "organic": "organic 가입자 847명의 30일 첫 주문 전환율은 75.2%로 전체(69.9%)보다 5.3%p 높고, 가장 낮은 단계인 식당조회→장바구니도 88.6%로 전체(84.1%)보다 4.5%p 높습니다. 30일 재주문율도 73.5%로 전체(67.4%)보다 6.1%p 높습니다. 가입자 수가 가장 많은 채널이면서 여러 단계에서 전체보다 높아, 전체 평균을 받쳐 주는 집단으로 보입니다.",
    "INSTA_첫주문쿠폰": "INSTA_첫주문쿠폰은 2026-01 가입월에 처음 나타났습니다. 가입자 739명의 30일 첫 주문 전환율은 55.6%로 전체(69.9%)보다 14.3%p 낮고, 8개 대상 가운데 가장 낮습니다. 가장 낮은 단계인 식당조회→장바구니는 72.4%로 전체(84.1%)보다 11.7%p 낮고, 30일 재주문율도 44.5%로 전체(67.4%)보다 22.9%p 낮습니다. 결제시작→첫주문30일은 98.3%로 전체와 같아서 차이는 주로 장바구니 담기 전후 단계에서 생기는 것으로 보이나, 그 원인은 이 파일만으로는 확인이 필요합니다.",
    "NAVER_검색광고": "NAVER_검색광고 가입자 517명의 30일 첫 주문 전환율은 76.4%로, 전체(69.9%)보다 6.5%p 높고 8개 대상 가운데 가장 높습니다. 가입→식당조회 전환율(97.5%)도 8개 대상 중 가장 높고, 가장 낮은 단계인 식당조회→장바구니는 87.7%로 전체(84.1%)보다 3.6%p 높습니다. 30일 재주문율은 72.6%로 전체(67.4%)보다 5.2%p 높아서, 첫 주문까지의 진행과 재주문 모두 전체 평균보다 나은 집단으로 보입니다.",
    "GOOGLE_앱설치": "GOOGLE_앱설치 가입자 398명의 30일 첫 주문 전환율은 73.6%로 전체(69.9%)보다 3.7%p 높습니다. 식당조회→장바구니 전환율은 89.5%로 8개 대상 중 가장 높고, 그래서 8개 대상 가운데 유일하게 가장 낮은 단계가 장바구니→결제시작(88.0%)입니다. 30일 재주문율은 72.7%로 전체(67.4%)보다 5.3%p 높으며, 다른 집단과 달리 결제 진입 단계가 상대적 약점으로 보이는지 추가 확인이 필요합니다.",
    "친구초대": "친구초대 가입자 273명의 30일 첫 주문 전환율은 74.4%로 전체(69.9%)보다 4.5%p 높습니다. 30일 재주문율(77.3%)은 전체(67.4%)보다 9.9%p 높아 8개 대상 중 가장 높고, 장바구니→결제시작(91.3%)도 가장 높습니다. 가장 낮은 단계는 식당조회→장바구니(87.1%)입니다. 가입자 수가 8개 대상 중 가장 적지만 작은 집단 기준(100명 미만)에는 해당하지 않으며, 재주문 측면에서 강한 집단으로 보입니다.",
}
AI_SOURCE = "🤖 Gemini가 미리 만든 해설 · 전체 기간 기준"

# ------------------------------------------------------------
# 2. 사이드바 필터 — 두 표에 같은 조건을 적용
# ------------------------------------------------------------
SMALL = 100                                # ✏️ 이보다 가입자가 적은 집단은 "작은 집단" (AI 해설도 같은 기준)

if "reset" not in st.session_state:
    st.session_state.reset = 0

def reset_filters():                       # 번호가 바뀌면 위젯이 처음 상태로 새로 만들어짐
    st.session_state.reset += 1

n = st.session_state.reset
months = sorted(df["가입월"].unique())
channels = sorted(df["가입채널"].unique())
platforms = sorted(df["플랫폼"].unique())

st.sidebar.header("필터")
start, end = st.sidebar.select_slider("가입월", options=months,
                                      value=(months[0], months[-1]), key=f"기간_{n}")
sel_channels = st.sidebar.multiselect("가입 채널", channels, default=channels, key=f"채널_{n}")
sel_platforms = st.sidebar.multiselect("플랫폼", platforms, default=platforms, key=f"플랫폼_{n}")
with st.sidebar.expander("고급 설정"):
    base_start, base_end = st.select_slider(
        "평소 범위 기준 기간", options=months, value=(months[0], months[3]), key=f"기준_{n}",   # ✏️ 문제가 없던 기간
        help="문제가 없던 기간을 고르세요. 이 기간의 월별 값 범위를 '평소 범위'로 씁니다.")
    hide_small = st.checkbox(f"가입자 {SMALL}명 미만 집단 제외 (집단 비교 · 드릴다운)", key=f"작은집단_{n}")
st.sidebar.button("필터 초기화", on_click=reset_filters)

# 집단 조건(가입 채널 · 플랫폼)만 적용한 데이터 g · rg, 기간까지 적용한 데이터 f · rf
g = df[df["가입채널"].isin(sel_channels) & df["플랫폼"].isin(sel_platforms)]
f = g[(g["가입월"] >= start) & (g["가입월"] <= end)]
rg = rd[rd["가입채널"].isin(sel_channels) & rd["플랫폼"].isin(sel_platforms)]
rf = rg[(rg["가입월"] >= start) & (rg["가입월"] <= end)]

def label(selected, all_values):           # 조건 줄에 쓸 말: 모두 고르면 "전체"
    if len(selected) == len(all_values):
        return "전체"
    return ", ".join(selected) if selected else "선택 없음"

period = "전체" if (start, end) == (months[0], months[-1]) else f"{start} ~ {end}"
st.caption(f"🔎 보고 있는 조건: 가입월 {period} · 가입 채널 {label(sel_channels, channels)} · "
           f"플랫폼 {label(sel_platforms, platforms)}")

if f.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다. 필터를 바꿔 주세요.")
    st.stop()

def rate(a, b):
    return a / b * 100 if b else 0

# ------------------------------------------------------------
# 3. 화면 — 탭 3개
# ------------------------------------------------------------
tab_overview, tab_explore, tab_action = st.tabs(["📊 개요", "🔍 탐색", "✅ 액션"])

# ===== [블록 A] 개요 · KPI 카드 — 규칙 문장 단계에서 이 블록을 통째로 바꿈 =====
with tab_overview:
    st.caption("이 탭에서 볼 수 있는 것: 핵심 숫자와 그 숫자를 읽는 법, 6주차 리포트의 차트 3장 (필터 적용)")

    # 3-1. KPI 카드 — ✏️ 5주차 지표 3개
    t = f[["가입자", "식당조회", "장바구니", "첫주문30일"]].sum()
    tr = rf[["첫주문고객", "재주문30일"]].sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("30일 첫 주문 전환율", f"{rate(t['첫주문30일'], t['가입자']):.1f}%")
    c2.metric("식당 조회 → 장바구니 전환율", f"{rate(t['장바구니'], t['식당조회']):.1f}%")
    c3.metric("30일 재주문율", f"{rate(tr['재주문30일'], tr['첫주문고객']):.1f}%")

    # 3-1-1. 지금 숫자 읽기 — 최근 3개월 값을 평소 범위와 비교하는 규칙 문장
    RECENT_N = 3                           # ✏️ "이번 값"으로 묶을 최근 개월 수
    MIN_BASE = 3                           # ✏️ 평소 범위를 만들 최소 개월 수
    READ_TARGETS = {                       # ✏️ 지표 이름: (분자, 분모)
        "30일 첫 주문 전환율": ("첫주문30일", "가입자"),
        "식당 조회 → 장바구니 전환율": ("장바구니", "식당조회"),
    }

    def read_sentence(name, num, den):     # 규칙에 따라 지표 하나의 읽기 문장을 만듦
        f_months = sorted(f["가입월"].unique())
        if len(f_months) < RECENT_N:
            return f"{name}: 비교하려면 선택 기간을 {RECENT_N}개월 이상 골라 주세요"

        # 이번 값: f의 마지막 3개월 합계로 계산
        recent_months = f_months[-RECENT_N:]
        recent = f[f["가입월"].isin(recent_months)][[num, den]].sum()
        if recent[den] == 0:
            return f"{name}: 최근 {RECENT_N}개월에 분모가 없어 계산할 수 없습니다"
        value = recent[num] / recent[den] * 100

        # 평소 범위: g의 기준 기간을 가입월로 묶어 분자 · 분모를 먼저 더한 뒤 월별로 나눔 (분모 0인 달 제외)
        base = g[(g["가입월"] >= base_start) & (g["가입월"] <= base_end)]
        monthly = base.groupby("가입월")[[num, den]].sum()
        monthly = monthly[monthly[den] > 0]
        if len(monthly) < MIN_BASE:
            return f"{name}: 기준 기간을 이 집단의 데이터가 있는 시기로 옮겨 주세요"
        monthly_rate = monthly[num] / monthly[den] * 100
        low, high = monthly_rate.min(), monthly_rate.max()

        head = (f"{recent_months[0]} 가입월부터 {RECENT_N}개월 {name}은 {value:.1f}%로, "
                f"평소 범위({low:.1f}–{high:.1f}%)")
        if value < low:
            return f"{head}보다 {low - value:.1f}%p 낮습니다"
        if value > high:
            return f"{head}보다 {value - high:.1f}%p 높습니다"
        return f"{head} 안에 있습니다"

    with st.container(border=True):
        st.markdown("**📏 지금 숫자 읽기**")
        st.markdown("\n".join(f"- {read_sentence(name, num, den)}"
                              for name, (num, den) in READ_TARGETS.items()))
        st.caption(f"📏 자동 계산 · 평소 범위 기준 기간 {base_start} ~ {base_end} (월별 값)")
        with st.expander("🤖 AI 해설 · 전체", expanded=False):
            st.markdown(AI_NOTES.get("전체", "아직 해설이 없습니다."))
            st.caption(AI_SOURCE)
# ===== [블록 A] 끝 =====

with tab_overview:
    st.divider()

    # ✏️ 3-2. 차트 - 6주차 차트 ① 문제 · ② 원인 · ③ 제안
    left, right = st.columns(2)

    # ① 문제: 가입월별 30일 첫 주문 전환율
    m = f.groupby("가입월")[["가입자", "첫주문30일"]].sum().reset_index()
    m["첫주문전환율"] = (m["첫주문30일"] / m["가입자"] * 100).round(1)
    fig1 = px.line(m, x="가입월", y="첫주문전환율", markers=True, hover_data=["가입자"],
                   labels={"가입월": "가입월", "첫주문전환율": "30일 첫 주문 전환율(%)", "가입자": "가입자(명)"},
                   title="① 가입월별 30일 첫 주문 전환율")
    fig1.update_layout(xaxis_title="")
    fig1.update_xaxes(type="category")       # 가입월(2025-09)을 날짜가 아니라 글자 그대로 표시
    left.plotly_chart(fig1, width="stretch")

    # ② 원인: 가입 채널별 식당 조회 → 장바구니 전환율
    ch = f.groupby("가입채널")[["식당조회", "장바구니"]].sum().reset_index()
    ch["장바구니전환율"] = (ch["장바구니"] / ch["식당조회"] * 100).round(1)
    ch = ch.sort_values("장바구니전환율")
    fig2 = px.bar(ch, x="장바구니전환율", y="가입채널", orientation="h", text_auto=".1f",
                  hover_data=["식당조회"],
                  labels={"장바구니전환율": "식당 조회 → 장바구니 전환율(%)", "가입채널": "가입 채널",
                          "식당조회": "식당 조회 가입자(명)"},
                  title="② 가입 채널별 식당 조회 → 장바구니 전환율")
    fig2.update_xaxes(range=[0, ch["장바구니전환율"].max() * 1.2])
    fig2.update_layout(yaxis_title="")
    right.plotly_chart(fig2, width="stretch")

    # ③ 제안: 가입 채널별 30일 재주문율
    rc = rf.groupby("가입채널")[["첫주문고객", "재주문30일"]].sum().reset_index()
    rc["재주문율"] = (rc["재주문30일"] / rc["첫주문고객"] * 100).round(1)
    rc = rc.sort_values("재주문율")
    fig3 = px.bar(rc, x="재주문율", y="가입채널", orientation="h", text_auto=".1f",
                  hover_data=["첫주문고객"],
                  labels={"재주문율": "30일 재주문율(%)", "가입채널": "가입 채널", "첫주문고객": "첫 주문 고객(명)"},
                  title="③ 가입 채널별 30일 재주문율")
    fig3.update_xaxes(range=[0, rc["재주문율"].max() * 1.2])
    fig3.update_layout(yaxis_title="")
    st.plotly_chart(fig3, width="stretch")

    # AI 해설을 만들 때 Gemini에 올릴 표 (필터와 관계없이 전체)
    with st.expander("📥 AI 해설용 데이터 내려받기"):
        st.download_button("가입 후 30일 퍼널 (가입월 × 가입 채널 × 플랫폼)",
                           df.to_csv(index=False).encode("utf-8-sig"), "가입월_채널_플랫폼_퍼널.csv")
        st.download_button("30일 재주문 (가입월 × 가입 채널 × 플랫폼)",
                           rd.to_csv(index=False).encode("utf-8-sig"), "가입월_채널_플랫폼_재주문.csv")

# ===== [블록 B] 탐색 — 집단 비교 · 드릴다운 단계에서 이 블록을 통째로 바꿈 =====
with tab_explore:
    st.caption("이 탭에서 볼 수 있는 것: 집단 비교와 드릴다운 — 어디서, 언제부터인가")

    # 4-1. 집단 비교 — 선택한 기준의 집단별 퍼널 5단계
    st.subheader("집단 비교")
    BASIS_NAME = {"가입채널": "가입 채널", "플랫폼": "플랫폼"}            # 화면에 보일 기준 이름
    basis = st.radio("비교 기준", list(BASIS_NAME), format_func=BASIS_NAME.get,
                     horizontal=True, key=f"비교기준_{n}")

    STAGES = ["가입자", "식당조회", "장바구니", "결제시작", "첫주문30일"]   # ✏️ 퍼널 5단계 (순서대로)
    STAGE_NAME = {"가입자": "가입", "식당조회": "식당 조회", "장바구니": "장바구니",
                  "결제시작": "결제 시작", "첫주문30일": "30일 내 첫 주문"}
    STEP_NAME = {"식당조회": "가입 → 식당 조회", "장바구니": "식당 조회 → 장바구니",
                 "결제시작": "장바구니 → 결제 시작", "첫주문30일": "결제 시작 → 첫 주문"}

    # 집단별 단계 합계 (f: 사이드바 필터 적용 데이터)
    grp = f.groupby(basis)[STAGES].sum().reset_index()

    # 작은 집단 제외 (사이드바 '고급 설정'의 체크박스)
    if hide_small:
        excluded = grp[grp["가입자"] < SMALL]
        grp = grp[grp["가입자"] >= SMALL]
        if not excluded.empty:
            names = ", ".join(excluded[basis].astype(str))
            st.caption(f"제외한 집단: {names} (가입자 {SMALL}명 미만)")

    if grp.empty:
        st.info("비교할 집단이 없습니다. 필터나 '작은 집단 제외' 설정을 바꿔 주세요.")
    else:
        # 집단 × 단계의 긴 표로 바꾸고 단계 순서대로 정렬
        long = grp.melt(id_vars=basis, value_vars=STAGES, var_name="단계", value_name="사람수")
        long["단계순서"] = long["단계"].map({s: i for i, s in enumerate(STAGES)})
        long = long.sort_values([basis, "단계순서"]).reset_index(drop=True)
        long["단계명"] = long["단계"].map(STAGE_NAME)

        # 누적 전환율: 가입자 대비 / 단계 전환율: 직전 단계 대비
        signups = long[basis].map(grp.set_index(basis)["가입자"])
        long["누적전환율"] = (long["사람수"] / signups * 100).round(1)
        prev = long.groupby(basis)["사람수"].shift(1)
        long["직전단계사람수"] = prev
        long["단계전환율"] = (long["사람수"] / prev.where(prev > 0) * 100).round(1)

        group_order = sorted(grp[basis].astype(str))                     # 두 차트의 집단 색을 같게 맞춤
        common_labels = {basis: BASIS_NAME[basis], "사람수": "이 단계 사람 수(명)",
                         "직전단계사람수": "직전 단계 사람 수(명)"}

        left_e, right_e = st.columns(2)

        # 왼쪽: 누적 전환율 퍼널 (x = 가입자 대비 %)
        fig4 = px.funnel(long, x="누적전환율", y="단계명", color=basis,
                         category_orders={"단계명": list(STAGE_NAME.values()), basis: group_order},
                         hover_data={"사람수": ":,", "단계명": False},
                         labels={**common_labels, "누적전환율": "누적 전환율(가입자 대비, %)",
                                 "단계명": "단계"},
                         title=f"{BASIS_NAME[basis]}별 누적 전환율 퍼널 (가입자 대비)")
        fig4.update_traces(texttemplate="%{x:.1f}%")
        fig4.update_layout(yaxis_title="")
        left_e.plotly_chart(fig4, width="stretch")

        # 오른쪽: 단계 전환율 막대 (직전 단계 대비 %, 집단별 나란히)
        step = long[long["단계순서"] > 0].copy()
        step["구간"] = step["단계"].map(STEP_NAME)
        fig5 = px.bar(step, x="구간", y="단계전환율", color=basis, barmode="group", text_auto=".1f",
                      category_orders={"구간": list(STEP_NAME.values()), basis: group_order},
                      hover_data={"직전단계사람수": ":,.0f", "사람수": ":,"},
                      labels={**common_labels, "단계전환율": "단계 전환율(%)", "구간": "단계 구간"},
                      title=f"{BASIS_NAME[basis]}별 단계 전환율 (직전 단계 대비)")
        top = step["단계전환율"].max()
        fig5.update_yaxes(range=[0, (top if pd.notna(top) and top > 0 else 100) * 1.15])
        fig5.update_layout(xaxis_title="")
        right_e.plotly_chart(fig5, width="stretch")

        # 남은 집단 중 작은 집단 안내
        shaky = grp[grp["가입자"] < SMALL]
        if not shaky.empty:
            names = ", ".join(shaky[basis].astype(str))
            st.caption(f"⚠️ {names}: 가입자가 {SMALL}명 미만이라 값이 크게 흔들릴 수 있습니다. "
                       f"사이드바 '고급 설정'에서 제외할 수 있어요.")

        # 4-2. 드릴다운 — 고른 집단을 다른 축으로 쪼개고, 가입월별로 따라가 봄
        st.divider()
        st.subheader("드릴다운: 언제부터, 무엇 때문일까")

        other = "플랫폼" if basis == "가입채널" else "가입채널"            # 비교 기준이 아닌 다른 축
        DRILL_STEPS = {                                                   # ✏️ 단계 이름: (분자, 분모)
            "가입 → 30일 첫 주문": ("첫주문30일", "가입자"),
            "가입 → 식당조회": ("식당조회", "가입자"),
            "식당조회 → 장바구니": ("장바구니", "식당조회"),
            "장바구니 → 결제시작": ("결제시작", "장바구니"),
            "결제시작 → 30일 첫 주문": ("첫주문30일", "결제시작"),
        }
        COUNT_NAME = {"가입자": "가입자(명)", "식당조회": "식당 조회 가입자(명)",
                      "장바구니": "장바구니 담은 가입자(명)", "결제시작": "결제 시작 가입자(명)",
                      "첫주문30일": "30일 내 첫 주문 가입자(명)"}

        s1, s2 = st.columns(2)
        sel_group = s1.selectbox("자세히 볼 집단", group_order,          # 위 집단 비교에 남은 집단만
                                 key=f"드릴집단_{basis}_{n}")
        sel_step = s2.selectbox("단계", list(DRILL_STEPS), key=f"드릴단계_{n}")
        num, den = DRILL_STEPS[sel_step]

        # 차트 ①: 고른 집단 × 다른 축 갈래별 가입월 전환율
        sub = f[f[basis].astype(str) == sel_group]
        drill_months = sorted(f["가입월"].unique())                      # 필터 기간의 가입월 전체
        branches = sorted(sub[other].astype(str).unique())
        trend = sub.groupby(["가입월", other])[[num, den]].sum()
        full_index = pd.MultiIndex.from_product([drill_months, branches], names=["가입월", other])
        trend = trend.reindex(full_index, fill_value=0).reset_index()    # 가입자가 없던 달도 빈 칸으로 보이게
        trend["전환율"] = (trend[num] / trend[den].where(trend[den] > 0) * 100).round(1)  # 분모 0 → 빈 값

        fig6 = px.line(trend, x="가입월", y="전환율", color=other, markers=True,
                       category_orders={"가입월": drill_months, other: branches},
                       hover_data={num: ":,", den: ":,"},
                       labels={"가입월": "가입월", "전환율": f"{sel_step} 전환율(%)",
                               other: BASIS_NAME[other], num: COUNT_NAME[num],
                               den: f"직전 단계 사람 수 · {COUNT_NAME[den]}"},
                       title=f"① {sel_group}의 가입월별 {sel_step} 전환율 — {BASIS_NAME[other]}별로 쪼개 보기")
        fig6.update_xaxes(type="category")
        fig6.update_layout(xaxis_title="")
        st.plotly_chart(fig6, width="stretch")
        st.caption("모든 갈래가 함께 움직였다면, 그 축은 원인이 아닙니다.")

        # 차트 ②: 가입월별 가입자 구성 — 고른 집단 vs 그 외
        OTHERS = "그 외"
        is_sel = f[basis].astype(str) == sel_group
        mix = f.assign(구분=f[basis].astype(str).where(is_sel, OTHERS))
        mix = mix.groupby(["가입월", "구분"])["가입자"].sum().reset_index()
        month_total = mix.groupby("가입월")["가입자"].transform("sum")
        mix["비중"] = (mix["가입자"] / month_total * 100).round(1)

        fig7 = px.bar(
            mix, x="가입월", y="가입자", color="구분", barmode="stack",
            category_orders={"가입월": drill_months, "구분": [sel_group, OTHERS]},
            color_discrete_map={sel_group: "#1F2A5A", OTHERS: "#B8B8B8"},
            hover_data={"비중": ":.1f"},
            labels={"가입월": "가입월", "가입자": "가입자(명)", "구분": "구분",
                    "비중": "그 달 가입자 중 비중(%)"},
            title=f"② 가입월별 가입자 수 — {sel_group} vs {OTHERS}",
        )
        fig7.update_xaxes(type="category")
        fig7.update_layout(xaxis_title="")
        st.plotly_chart(fig7, width="stretch")
        st.caption("전체 전환율이 움직인 시점에 이 집단의 비중이 함께 바뀌었다면 구성 변화가 원인 후보입니다. "
                   "함께 움직였다고 원인이 확정되지는 않습니다.")

        # AI 해설 (미리 만든 해설, 전체 기간 기준)
        with st.expander(f"🤖 AI 해설 · {sel_group}", expanded=False):
            st.markdown(AI_NOTES.get(sel_group, "이 집단의 해설은 아직 없습니다."))
            st.caption(AI_SOURCE)
# ===== [블록 B] 끝 =====

# ===== [블록 C] 액션 — 액션 카드 단계에서 이 줄 아래부터 파일 끝까지를 받은 블록으로 바꿈 =====
with tab_action:
    st.caption("측정 KPI의 현재 값은 선택한 기간의 마지막 3개월로 계산합니다")

    # 측정 KPI 계산 기간: 사이드바 가입월(start ~ end)의 마지막 3개월 (가입 채널 · 플랫폼 필터는 무시)
    KPI_N = 3                                                            # ✏️ 측정 KPI에 쓸 최근 개월 수
    period_months = [mo for mo in months if start <= mo <= end]
    kpi_months = period_months[-KPI_N:]
    kpi_span = kpi_months[0] if len(kpi_months) == 1 else f"{kpi_months[0]} ~ {kpi_months[-1]}"

    def kpi_value(data, col, value, num, den):   # (집단 조건) 데이터의 분자 ÷ 분모, 없으면 "데이터 없음"
        d = data[(data[col] == value) & data["가입월"].isin(kpi_months)]
        tot = d[[num, den]].sum()
        if d.empty or tot[den] == 0:
            return "데이터 없음"
        return f"{tot[num] / tot[den] * 100:.1f}%"

    ACTION_CARDS = [                             # ✏️ 카드 문장 · 측정 KPI · 확인할 곳
        {
            "title": "카드 1 · INSTA 30일 첫 주문 전환율",
            "body": ("* 기간 전체: 56.0%\n"
                     "* 가입월 끝을 2026-04로: 55.7%\n"
                     "* 플랫폼을 iOS만: 56.0% 그대로 (기간만 적용)"),
            "data": df, "col": "가입채널", "value": "INSTA_첫주문쿠폰",
            "num": "첫주문30일", "den": "가입자",
            "metric": "INSTA_첫주문쿠폰 30일 첫 주문 전환율 (첫주문30일 ÷ 가입자)",
            "check": "탐색 탭 › 비교 기준 '가입 채널' › 드릴다운 'INSTA_첫주문쿠폰' · 단계 '가입 → 30일 첫 주문'",
        },
        {
            "title": "카드 2 · INSTA 30일 재주문율",
            "body": ("* 기간 전체: 40.9%\n"
                     "* 가입월 끝을 2026-04로: 48.4%\n"
                     "* 플랫폼을 iOS만: 40.9% 그대로 (기간만 적용)"),
            "data": rd, "col": "가입채널", "value": "INSTA_첫주문쿠폰",
            "num": "재주문30일", "den": "첫주문고객",
            "metric": "INSTA_첫주문쿠폰 30일 재주문율 (재주문30일 ÷ 첫주문고객)",
            "check": "개요 탭 › ③ 가입 채널별 30일 재주문율 (사이드바 가입 채널을 INSTA_첫주문쿠폰만)",
        },
    ]

    for col_box, card in zip(st.columns(2), ACTION_CARDS):
        with col_box:
            with st.container(border=True):
                st.markdown(f"**{card['title']}**")
                st.markdown(card["body"])
                st.metric(card["metric"],
                          kpi_value(card["data"], card["col"], card["value"], card["num"], card["den"]),
                          help=f"가입월 {kpi_span} · 가입 채널 · 플랫폼 필터는 적용하지 않음")
                st.caption(f"대시보드에서: {card['check']}")