"""
가짜 쇼핑몰 DB(shop.db)를 만드는 스크립트. (공통 규약의 테이블 구조를 그대로 따른다)

실행: python make_db.py
- 실행할 때마다 shop.db를 지우고 새로 만든다.
- random.seed를 고정해서 매번 같은 데이터가 나온다.

테이블
- ord_mst  : ord_no, mbr_no, ord_dt, ord_amt, ord_stat_cd          (주문)
- ord_dtl  : ord_no, prd_no, qty, sale_prc                         (주문 상품)
- dlv_mst  : dlv_no, ord_no, dlv_tp_cd, dlv_stat_cd, dlv_cmpl_dt, rgn_cd  (배송)
- prd_mst  : prd_no, prd_nm, ctgr_cd                               (상품)
- ctgr_mst : ctgr_cd, ctgr_nm                                      (카테고리)
- mbr_mst  : mbr_no, join_dt, grd_cd                               (회원. 연령과 성별은 없음)
- cm_cd    : cd_grp, cd, cd_nm                                     (공통코드)
- tmp_dlv_evt_2611 : evt_no, dlv_no, evt_tp_cd, evt_dt             (최근 생긴 배송 이벤트 기록)

일부러 넣은 함정 (에이전트 시험용)
- 테이블과 컬럼 이름이 약어라서 알아보기 어렵다.
- 코드 값(예: dlv_tp_cd '02')의 뜻은 cm_cd에서만 확인할 수 있다.
- 회원의 연령과 성별은 없다.
- 쿠폰 관련 테이블과 컬럼은 어디에도 없다.
- tmp_dlv_evt_2611에는 2026년 11월 데이터만 있다. evt_tp_cd '30'이 '배송지연'이다.
"""

import os
import random
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shop.db")
random.seed(42)

START = datetime(2026, 5, 1)
END = datetime(2026, 11, 20, 23, 59, 59)
N_ORDERS = 2000
N_MEMBERS = 500


def rand_dt(start, end):
    """start와 end 사이의 임의의 시각"""
    sec = random.randint(0, int((end - start).total_seconds()))
    return start + timedelta(seconds=sec)


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


# ---------------------------------------------------------------
# 1. 테이블 구조
# ---------------------------------------------------------------
SCHEMA = """
CREATE TABLE ord_mst (
    ord_no       TEXT PRIMARY KEY,
    mbr_no       TEXT,
    ord_dt       TEXT,
    ord_amt      INTEGER,
    ord_stat_cd  TEXT
);

CREATE TABLE ord_dtl (
    ord_no    TEXT,
    prd_no    TEXT,
    qty       INTEGER,
    sale_prc  INTEGER
);

CREATE TABLE dlv_mst (
    dlv_no       TEXT PRIMARY KEY,
    ord_no       TEXT,
    dlv_tp_cd    TEXT,
    dlv_stat_cd  TEXT,
    dlv_cmpl_dt  TEXT,
    rgn_cd       TEXT
);

CREATE TABLE prd_mst (
    prd_no   TEXT PRIMARY KEY,
    prd_nm   TEXT,
    ctgr_cd  TEXT
);

CREATE TABLE ctgr_mst (
    ctgr_cd  TEXT PRIMARY KEY,
    ctgr_nm  TEXT
);

CREATE TABLE mbr_mst (
    mbr_no   TEXT PRIMARY KEY,
    join_dt  TEXT,
    grd_cd   TEXT
);

CREATE TABLE cm_cd (
    cd_grp  TEXT,
    cd      TEXT,
    cd_nm   TEXT,
    PRIMARY KEY (cd_grp, cd)
);

CREATE TABLE tmp_dlv_evt_2611 (
    evt_no     TEXT PRIMARY KEY,
    dlv_no     TEXT,
    evt_tp_cd  TEXT,
    evt_dt     TEXT
);
"""

# ---------------------------------------------------------------
# 2. 공통코드 (코드의 뜻은 여기서만 확인할 수 있다)
# ---------------------------------------------------------------
CODES = {
    "ORD_STAT_CD": {"10": "주문접수", "20": "결제완료", "30": "배송중", "40": "배송완료",
                    "90": "주문취소", "91": "반품완료"},
    "DLV_TP_CD": {"01": "일반배송", "02": "새벽배송", "03": "당일배송"},
    "DLV_STAT_CD": {"01": "배송준비", "02": "배송중", "03": "배송완료", "09": "배송취소"},
    "GRD_CD": {"01": "VVIP", "02": "VIP", "03": "GOLD", "04": "SILVER", "05": "FAMILY"},
    "RGN_CD": {"11": "서울", "41": "경기", "28": "인천", "26": "부산", "27": "대구", "99": "기타"},
    "EVT_TP_CD": {"10": "출고", "20": "배송출발", "30": "배송지연", "40": "배송완료"},
}
CAPITAL = ("11", "41", "28")  # 수도권

CATEGORIES = {"C10": "신선식품", "C20": "가공식품", "C30": "디지털/가전",
              "C40": "패션", "C50": "뷰티", "C60": "생활용품"}

PRODUCT_NAMES = {
    "C10": ["친환경 계란 30구", "한우 등심 300g", "제주 감귤 3kg", "샐러드 믹스 500g",
            "국산 삼겹살 600g", "생연어 필렛 400g", "유기농 우유 1L", "딸기 500g"],
    "C20": ["즉석밥 24개입", "라면 5입", "냉동 만두 1kg", "참치캔 10입", "시리얼 600g"],
    "C30": ["무선 이어폰", "블루투스 스피커", "보조배터리 20000mAh", "로봇청소기", "전기포트"],
    "C40": ["오버핏 후드티", "기모 슬랙스", "패딩 조끼", "운동화", "니트 머플러"],
    "C50": ["수분 크림 50ml", "선크림 SPF50", "립밤 세트", "클렌징 폼", "시트 마스크 10매"],
    "C60": ["물티슈 10팩", "세탁세제 3L", "키친타월 6롤", "칫솔 10개입", "수납 박스"],
}
PRICE_RANGE = {"C10": (5000, 45000), "C20": (3000, 25000), "C30": (19000, 390000),
               "C40": (15000, 89000), "C50": (6000, 39000), "C60": (4000, 29000)}


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript(SCHEMA)

    # 공통코드, 카테고리
    for grp, items in CODES.items():
        for cd, nm in items.items():
            cur.execute("INSERT INTO cm_cd VALUES (?,?,?)", (grp, cd, nm))
    for ctgr_cd, ctgr_nm in CATEGORIES.items():
        cur.execute("INSERT INTO ctgr_mst VALUES (?,?)", (ctgr_cd, ctgr_nm))

    # 회원 (연령, 성별 없음). 지역은 회원 테이블에 없고 배송지(rgn_cd)로만 남는다.
    members = []  # (mbr_no, 주 배송지역)
    for i in range(1, N_MEMBERS + 1):
        mbr_no = f"M{i:05d}"
        rgn = random.choices(list(CODES["RGN_CD"]), weights=[35, 30, 10, 10, 5, 10])[0]
        members.append((mbr_no, rgn))
        cur.execute(
            "INSERT INTO mbr_mst VALUES (?,?,?)",
            (mbr_no,
             fmt(rand_dt(datetime(2016, 1, 1), datetime(2026, 11, 1)))[:10],
             random.choices(list(CODES["GRD_CD"]), weights=[3, 7, 20, 30, 40])[0]),
        )

    # 상품
    products = []  # (prd_no, ctgr_cd, price)
    n = 1
    for ctgr, names in PRODUCT_NAMES.items():
        for name in names:
            for variant in ["", " (대용량)", " (기획세트)"]:
                prd_no = f"P{n:06d}"
                lo, hi = PRICE_RANGE[ctgr]
                price = random.randint(lo, hi) // 100 * 100
                products.append((prd_no, ctgr, price))
                cur.execute("INSERT INTO prd_mst VALUES (?,?,?)", (prd_no, name + variant, ctgr))
                n += 1

    # 주문, 주문 상품, 배송
    nov_dlv = []  # 11월 출고된 배송 (이벤트 기록용): (dlv_no, dlv_tp_cd, ship_dt, cmpl_dt)
    for i in range(1, N_ORDERS + 1):
        ord_dt = rand_dt(START, END)
        ord_no = f"O{ord_dt:%Y%m%d}{i:05d}"
        mbr_no, rgn = random.choice(members)
        # 최근 주문일수록 아직 배송 전일 가능성이 높다
        recent = (END - ord_dt).days < 3
        stat = random.choices(["10", "20", "30", "40", "90", "91"],
                              weights=[15, 20, 40, 15, 7, 3] if recent else [0, 1, 2, 85, 7, 5])[0]

        total = 0
        items = random.sample(products, random.randint(1, 3))
        for prd_no, ctgr, price in items:
            qty = random.choices([1, 2, 3], weights=[70, 20, 10])[0]
            total += price * qty
            cur.execute("INSERT INTO ord_dtl VALUES (?,?,?,?)", (ord_no, prd_no, qty, price))

        # 배송 유형: 새벽배송(02)은 신선식품이 들어간 주문 + 수도권에서만
        has_fresh = any(c == "C10" for _, c, _ in items)
        if has_fresh and rgn in CAPITAL and random.random() < 0.6:
            dlv_tp = "02"
        elif rgn in ("11", "41") and random.random() < 0.15:
            dlv_tp = "03"
        else:
            dlv_tp = "01"

        ship_dt = ord_dt + timedelta(hours=random.randint(3, 20))
        if stat == "90":
            dlv_stat, cmpl_dt, shipped = "09", None, False
        elif stat in ("10", "20"):
            dlv_stat, cmpl_dt, shipped = "01", None, False
        elif stat == "30":
            dlv_stat, cmpl_dt, shipped = "02", None, True
        else:  # 40 배송완료, 91 반품완료(배송은 완료됐던 건)
            hours = {"02": random.randint(4, 9), "03": random.randint(3, 8),
                     "01": random.randint(18, 72)}[dlv_tp]
            dlv_stat, cmpl_dt, shipped = "03", ship_dt + timedelta(hours=hours), True
            if cmpl_dt > END:  # 데이터 마감 시점에는 아직 배송중인 건
                stat, dlv_stat, cmpl_dt = "30", "02", None
        if shipped and ship_dt > END:  # 마감 시점에는 아직 출고 전인 건
            stat, dlv_stat, shipped = "20", "01", False

        cur.execute("INSERT INTO ord_mst VALUES (?,?,?,?,?)",
                    (ord_no, mbr_no, fmt(ord_dt), total, stat))

        dlv_no = f"D{i:06d}"
        cur.execute("INSERT INTO dlv_mst VALUES (?,?,?,?,?,?)",
                    (dlv_no, ord_no, dlv_tp, dlv_stat, fmt(cmpl_dt), rgn))
        if shipped and ship_dt >= datetime(2026, 11, 1):
            nov_dlv.append((dlv_no, dlv_tp, ship_dt, cmpl_dt))

    # 배송 이벤트 기록: 2026년 11월 출고분만 있다 (최근에 생긴 테이블)
    evt_n = 1

    def add_evt(dlv_no, tp, dt):
        nonlocal evt_n
        if dt > END:  # 마감 이후 이벤트는 아직 일어나지 않았다
            return
        cur.execute("INSERT INTO tmp_dlv_evt_2611 VALUES (?,?,?,?)",
                    (f"EV{evt_n:07d}", dlv_no, tp, fmt(dt)))
        evt_n += 1

    for dlv_no, dlv_tp, ship_dt, cmpl_dt in nov_dlv:
        add_evt(dlv_no, "10", ship_dt)
        add_evt(dlv_no, "20", ship_dt + timedelta(hours=1))
        delay_rate = {"02": 0.08, "03": 0.12, "01": 0.15}[dlv_tp]
        if random.random() < delay_rate:
            add_evt(dlv_no, "30", ship_dt + timedelta(hours=random.randint(2, 12)))
        if cmpl_dt:
            add_evt(dlv_no, "40", cmpl_dt)

    con.commit()

    # 결과 확인
    print(f"생성 완료: {DB_PATH}\n")
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        cnt = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<18} {cnt:>6}행")
    con.close()


if __name__ == "__main__":
    main()
