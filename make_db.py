"""가짜 DB(shop.db) 생성기 v4.

실행: python make_db.py

- 테이블 구조는 db/A-schema/DDL/*.sql 35개를 읽어 그대로 만든다(Redshift 전용 구문만 뺀다).
- 안에 들어가는 데이터는 전부 지어낸 연습용 값이다. 실제 고객, 주문, 매출 데이터가 아니다.
- 난수 시드를 고정해 실행할 때마다 같은 데이터가 나온다.

v3에서 달라진 점: 대시보드 캡처(2026-11-01 06시 갱신본)에 맞춰 보정했다.
- 카테고리(CAT_M) 10종, 채널 이름 4종, 프로모션 4개의 이름을 캡처와 같게 했다.
- 2026년 10월 마트(NX_AGG1_DSL) 값이 캡처의 숫자와 원 단위까지 일치한다.
  총액 2,901,488,100 / 채널 4개 / 카테고리 10개 / 일 최대 142,748,500.
- 프로모션 4개의 쿠폰 발급 수와 사용 수가 캡처와 일치한다.
- 일별 모양은 캡처의 꺾은선을 눈으로 읽은 근사치다(정확한 일별 값은 캡처에 없다).

일부러 넣어 둔 조건 (시험 문항의 근거)
- 마트(NX_AGG1_DSL)는 원천 주문의 SALE_AMT - DC_AMT - RTN_AMT 를 날짜, 채널, 카테고리로 더한 값이다.
  실제 마트가 이렇게 계산되는지는 알 수 없다. 이 연습용 DB에서의 가정이다.
- 기간은 실제 추출본의 안내문(db/A-db/데이터_안내.txt)에 적힌 대로 맞췄다(v4).
  - 회원은 4,000명이다.
  - 주문 계열(LX_HJV1_H, LX_HJV1_D, 결제, 반품)과 매출 요약은 2026-05-01 ~ 2026-11-19 이다.
  - LX_HJV1_H_BAK 은 2024-01-01 ~ 2024-11-15 의 주문 헤더만 있는 옛 백업이다. 2025년 주문은 어디에도 없다.
  - 주문과 행동 로그는 2026-11-19 분까지, 푸시 발송 이력은 2026-11-20 오전까지 있다.
- NX_ 로 시작하는 로그성 테이블(세션, 조회, 장바구니, 푸시, 배송)은 2026-07-01 부터만 있다.
  이 시작일은 안내문에 없는, 이 연습용 DB에서의 가정이다.
- NX_ 테이블의 시각은 UTC(한국 시각 - 9시간) 문자열로 넣었다.
- NX_ 테이블의 CUST_ID 는 'C' + MBR_NO 이다. 비로그인 세션은 빈 문자열이다.
- 캡처 이후에 시작한 프로모션 PRM2611 은 지어낸 것이다.
"""

import bisect
import random
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
# 제공받은 입력 자료는 db/ 폴더 안에 둔다(깃허브에는 올리지 않는 폴더).
# 예전처럼 프로젝트 폴더 바로 아래에 두었어도 찾는다.
DDL_DIR = next(
    (d for d in (BASE / "db" / "A-schema" / "DDL", BASE / "A-schema" / "DDL") if d.exists()),
    BASE / "db" / "A-schema" / "DDL",
)
DB_PATH = BASE / "shop.db"
SEED = 20261121

END = date(2026, 11, 19)        # 주문과 행동 로그의 마지막 날(야간 배치라 추출 전날까지)
SEND_END = date(2026, 11, 20)   # 푸시 발송 이력의 마지막 날(추출 시각 11/20 18:00 까지)
CUR_START = date(2026, 5, 1)    # 주문 계열의 첫날(그 이전은 아카이브라 추출에서 빠짐)
BAK_START = date(2024, 1, 1)
BAK_END = date(2024, 11, 15)    # 옛 백업이 멈춘 날
NX_START = date(2026, 7, 1)
OCT_START = date(2026, 10, 1)
OCT_END = date(2026, 10, 31)

# ---------- 대시보드 캡처에서 읽은 숫자 (10월, 기준 NET_AMT) ----------
DASH_TOTAL = 2_901_488_100
DASH_PEAK = 142_748_500
DASH_PEAK_DAY = date(2026, 10, 4)  # 꺾은선에서 가장 높은 날(눈으로 읽음)
DASH_CHANNEL = {"20": 1_228_153_900, "30": 708_460_900, "10": 529_907_400, "40": 434_965_900}
DASH_CAT_M = {
    "냉장고": 1_083_697_200, "세탁기": 447_284_400, "김치냉장고": 334_647_700, "청소기": 272_057_200,
    "정수기": 185_824_600, "공기청정기": 175_789_600, "사무기기": 132_843_400, "업소용 냉장고": 107_359_200,
    "프린터": 104_546_700, "전자레인지·오븐": 57_438_100,
}
# 캡처의 꺾은선 높이를 눈으로 읽은 값(10/01 ~ 10/31). 모양만 흉내 내는 근사치다.
DAY_SHAPE = [124, 147, 148, 200, 167, 105, 112, 145, 140, 174, 146, 163, 156, 114, 122, 121,
             155, 181, 120, 120, 106, 107, 108, 132, 124, 101, 114, 104, 98, 105, 118]
assert sum(DASH_CHANNEL.values()) == DASH_TOTAL and sum(DASH_CAT_M.values()) == DASH_TOTAL

SEASON = {1: 0.82, 2: 0.78, 3: 0.90, 4: 0.92, 5: 1.02, 6: 0.95, 7: 1.05, 8: 1.00, 9: 1.04, 10: 1.00, 11: 1.18, 12: 1.10}
CNSM_PICK = 0.35  # 소모품이 있는 카테고리에서 소모품이 팔릴 확률
U = 100  # 금액의 최소 단위(원)

rng = random.Random(SEED)


# ---------- 날짜 형식 도우미 ----------
def d8(d):
    return d.strftime("%Y%m%d")


def ts14(dt):
    return dt.strftime("%Y%m%d%H%M%S")


def ts19(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def utc19(dt):
    """한국 시각을 UTC 문자열로 바꾼다(NX_ 테이블용)."""
    return ts19(dt - timedelta(hours=9))


def rand_time(d):
    hour = rng.choices(range(24), weights=[1, 1, 1, 1, 1, 1, 2, 3, 5, 6, 7, 7, 8, 7, 6, 6, 6, 7, 8, 9, 10, 10, 8, 4])[0]
    return datetime(d.year, d.month, d.day, hour, rng.randint(0, 59), rng.randint(0, 59))


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def won(x):
    return int(round(x / 100.0)) * 100


def ins(conn, table, rows):
    if not rows:
        return
    marks = ", ".join("?" * len(rows[0]))
    conn.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)


# ---------- 스키마 ----------
def build_schema(conn):
    files = sorted(DDL_DIR.glob("*.sql"))
    if not files:
        raise SystemExit(f"테이블 정의 파일이 없습니다: {DDL_DIR}")
    for f in files:
        sql = f.read_text(encoding="utf-8")
        sql = re.sub(r"\)\s*DISTSTYLE\s+\w+\s*;", ");", sql, flags=re.IGNORECASE)
        conn.executescript(sql)
    return len(files)


# ---------- 기준 정보 ----------
CODES = [
    ("PAY", "01", "신용카드"), ("PAY", "02", "계좌이체"), ("PAY", "03", "간편결제"), ("PAY", "04", "포인트"),
    ("GRD", "G1", "일반"), ("GRD", "G2", "실버"), ("GRD", "G3", "골드"), ("GRD", "G4", "VIP"),
    ("CHNL", "10", "PC웹"), ("CHNL", "20", "앱"), ("CHNL", "30", "모바일웹"), ("CHNL", "40", "제휴몰"),
    ("DEVICE", "PC", "PC"), ("DEVICE", "APP", "앱"), ("DEVICE", "MW", "모바일웹"),
    ("ORD_STAT", "10", "결제완료"), ("ORD_STAT", "20", "배송중"), ("ORD_STAT", "30", "배송완료"), ("ORD_STAT", "90", "주문취소"),
    ("DLV_STAT", "10", "출고준비"), ("DLV_STAT", "20", "배송중"), ("DLV_STAT", "30", "배송완료"),
    ("RTN_TP", "RT", "반품"), ("RTN_TP", "CN", "취소"),
    ("DC_TP", "A", "정액"), ("DC_TP", "R", "정률"),
]

# 채널 이름은 대시보드 캡처의 표기를 따른다.
CHANNELS = [("10", "PC웹", "ONLINE"), ("20", "앱", "ONLINE"), ("30", "모바일웹", "ONLINE"), ("40", "제휴몰", "PARTNER")]
CHNL_CODES = ["10", "20", "30", "40"]
CHNL_WEIGHTS = [DASH_CHANNEL[c] for c in CHNL_CODES]

# (CAT_CD, 대, 중, 소, 최저가, 최고가, 중분류 안에서의 매출 비중, 용량 표기, 소모품 가격대, 업무용 비율)
# 중분류(CAT_M) 10종은 대시보드 캡처와 같다. CAT_CD 와 소분류는 지어낸 값이다.
# 과거 쿼리 주석에 따라 C101, C102 는 냉장고의 일반형과 양문형으로 두었다.
CATEGORIES = [
    ("C101", "주방가전", "냉장고", "일반형", 600_000, 1_300_000, 0.40, "L", None, 0.0),
    ("C102", "주방가전", "냉장고", "양문형", 1_500_000, 3_500_000, 0.60, "L", None, 0.0),
    ("C111", "주방가전", "김치냉장고", "스탠드형", 1_100_000, 2_400_000, 0.70, "L", None, 0.0),
    ("C112", "주방가전", "김치냉장고", "뚜껑형", 450_000, 900_000, 0.30, "L", None, 0.0),
    ("C121", "주방가전", "업소용 냉장고", "업소용", 1_200_000, 2_800_000, 1.00, "L", None, 1.0),
    ("C131", "주방가전", "정수기", "직수형", 400_000, 1_200_000, 1.00, "", (15_000, 60_000), 0.0),
    ("C141", "주방가전", "전자레인지·오븐", "전자레인지", 80_000, 300_000, 0.50, "", None, 0.0),
    ("C142", "주방가전", "전자레인지·오븐", "오븐", 200_000, 700_000, 0.50, "", None, 0.0),
    ("C201", "생활가전", "세탁기", "드럼", 800_000, 1_900_000, 0.65, "kg", None, 0.0),
    ("C202", "생활가전", "세탁기", "통돌이", 400_000, 900_000, 0.35, "kg", None, 0.0),
    ("C211", "생활가전", "청소기", "무선", 300_000, 900_000, 0.60, "", (20_000, 80_000), 0.0),
    ("C212", "생활가전", "청소기", "로봇", 400_000, 1_500_000, 0.40, "", None, 0.0),
    ("C221", "생활가전", "공기청정기", "스탠드형", 200_000, 900_000, 1.00, "", (20_000, 70_000), 0.0),
    ("C401", "사무가전", "프린터", "잉크젯", 90_000, 300_000, 0.50, "", (20_000, 60_000), 0.0),
    ("C402", "사무가전", "프린터", "레이저", 200_000, 700_000, 0.50, "", (40_000, 150_000), 0.6),
    ("C411", "사무가전", "사무기기", "복합기", 400_000, 1_600_000, 1.00, "", (50_000, 180_000), 1.0),
]
CAT_M_OF = {c[0]: c[2] for c in CATEGORIES}
CAT_SHARE = {c[0]: DASH_CAT_M[c[2]] / DASH_TOTAL * c[6] for c in CATEGORIES}

COLORS = ["화이트", "블랙", "실버", "베이지", "그레이"]


def build_reference(conn):
    ins(conn, "LX_ZCM_CD", CODES)
    ins(conn, "LX_ZCM_CHNL", CHANNELS)
    ins(conn, "LX_ZCM_PTNR", [(f"P{i:07d}", f"제휴사{chr(64 + i)}") for i in range(1, 6)])
    cal = []
    for d in daterange(BAK_START, date(2026, 12, 31)):
        iso = d.isocalendar()
        cal.append((d8(d), f"{iso[0]}W{iso[1]:02d}", d.strftime("%Y%m"), "Y" if d.weekday() >= 5 else "N"))
    ins(conn, "LX_ZCM_DT", cal)
    ins(conn, "NX_QPS9_LMT", [("PUSH", 2, 7), ("SMS", 1, 7), ("EMAIL", 3, 7)])


def build_products(conn):
    ins(conn, "LX_KRP2_C", [(c[0], c[1], c[2], c[3]) for c in CATEGORIES])
    models, skus, attrs, prices = [], [], [], []
    sku_info = {}  # SKU_CD -> (MDL_CD, CAT_CD, 가격, 소모품 여부)
    cat_skus = {c[0]: {"dev": [], "cns": []} for c in CATEGORIES}
    sku_no = [0]

    def add_sku(mdl, cat_cd, price, is_cnsm, cap):
        sku_no[0] += 1
        sku = f"S{sku_no[0]:06d}"
        skus.append((sku, mdl, rng.choice(COLORS), cap))
        sku_info[sku] = (mdl, cat_cd, price, is_cnsm)
        cat_skus[cat_cd]["cns" if is_cnsm else "dev"].append(sku)
        change = datetime(2026, rng.randint(2, 9), rng.randint(1, 28), 0, 0, 0)
        if rng.random() < 0.4:
            prices.append((sku, won(price * 1.08), "2025-12-31 15:00:00", utc19(change)))
            prices.append((sku, price, utc19(change), "9999-12-31 00:00:00"))
        else:
            prices.append((sku, price, "2025-12-31 15:00:00", "9999-12-31 00:00:00"))

    for cat_cd, _l, m, s, lo, hi, _split, cap_unit, cnsm, biz in CATEGORIES:
        n_dev = rng.randint(4, 6)
        for i in range(1, n_dev + 1):
            mdl = f"M{cat_cd[1:]}{i:02d}"
            label = {"C121": "업소용 냉장고", "C141": "전자레인지", "C142": "오븐", "C411": "복합기"}.get(cat_cd, f"{s} {m}")
            models.append((mdl, f"{label} {chr(64 + i)}-{rng.randint(100, 899)}", cat_cd,
                           d8(date(rng.randint(2022, 2026), rng.randint(1, 6), rng.randint(1, 28)))))
            attrs.append((mdl, "Y" if rng.random() < biz else "N", "N", "Y" if rng.random() < 0.08 else "N"))
            base = won(rng.uniform(lo, hi))
            for _ in range(rng.randint(1, 3)):
                if cap_unit == "L":
                    cap = f"{rng.choice([300, 420, 500, 600, 870])}L"
                elif cap_unit == "kg":
                    cap = f"{rng.choice([12, 16, 21, 24])}kg"
                else:
                    cap = "-"
                add_sku(mdl, cat_cd, won(base * rng.uniform(0.95, 1.1)), False, cap)
        if cnsm:
            for j, kind in enumerate(["교체 필터" if cat_cd in ("C131", "C211", "C221") else "정품 토너·잉크", "소모품 세트"], start=1):
                mdl = f"M{cat_cd[1:]}{90 + j:02d}"
                models.append((mdl, f"{m} {kind}", cat_cd, d8(date(rng.randint(2022, 2025), rng.randint(1, 12), 1))))
                attrs.append((mdl, "N", "Y", "N"))
                add_sku(mdl, cat_cd, won(rng.uniform(*cnsm)), True, "-")
    ins(conn, "LX_KRP1_M", models)
    ins(conn, "LX_KRP3_SKU", skus)
    ins(conn, "NX_KRP7_ATTR", attrs)
    ins(conn, "NX_KRP4_PRC", prices)
    return sku_info, cat_skus


def build_members(conn, n_members=4000):
    surnames = "김이박최정강조윤장임"
    given = "수민지현우영서준하은"
    members = []
    span = (END - date(2022, 1, 1)).days
    for i in range(1, n_members + 1):
        mbr = 100000 + i
        join = date(2022, 1, 1) + timedelta(days=int(rng.random() ** 0.8 * span))
        grd = rng.choices(["G1", "G2", "G3", "G4"], weights=[55, 25, 14, 6])[0]
        members.append((mbr, join, grd, f"{rng.choice(surnames)}*{rng.choice(given)}", f"010-****-{rng.randint(0, 9999):04d}"))
    ins(conn, "LX_DNS1_M", [(m, d8(j), g, nm, tel) for m, j, g, nm, tel in members])

    hist, agr = [], []
    order = ["G1", "G2", "G3", "G4"]
    for mbr, join, grd, _nm, _tel in members:
        idx = order.index(grd)
        steps = min(idx, rng.randint(0, 2))
        start = join
        for k in range(steps, 0, -1):
            left = (END - start).days
            if left < 60:
                break
            nxt = start + timedelta(days=rng.randint(30, max(31, left // 2)))
            hist.append((mbr, order[idx - k], d8(start), d8(nxt - timedelta(days=1))))
            start = nxt
        hist.append((mbr, grd, d8(start), "99991231"))
        upd = rand_time(join + timedelta(days=rng.randint(0, max(0, (END - join).days))))
        agr.append((f"C{mbr}", "Y" if rng.random() < 0.6 else "N", "Y" if rng.random() < 0.45 else "N", utc19(upd)))
    ins(conn, "LX_DNS2_H", hist)
    ins(conn, "NX_DNS5_AGR", agr)
    return members


# ---------- 주문 ----------
class Shop:
    """주문을 만들 때 쓰는 공통 재료(회원, 상품, 비중)를 묶어 둔다."""

    def __init__(self, members, sku_info, cat_skus):
        self.sku_info = sku_info
        self.cat_skus = cat_skus
        self.by_join = sorted(members, key=lambda m: m[1])
        self.join_dates = [m[1] for m in self.by_join]
        self.affinity = {m[0]: rng.choice([0.0, 0.2, 0.5, 1.0, 1.0, 2.0, 3.0]) for m in members}
        # 카테고리별 줄 평균 금액과, 매출 비중이 대시보드와 비슷해지는 줄 수 비중
        self.mean = {}
        for cat, ci in cat_skus.items():
            dev = sum(sku_info[s][2] for s in ci["dev"]) / len(ci["dev"])
            if ci["cns"]:
                cns = sum(sku_info[s][2] for s in ci["cns"]) / len(ci["cns"]) * 2
                self.mean[cat] = (1 - CNSM_PICK) * dev + CNSM_PICK * cns
            else:
                self.mean[cat] = dev
        self.cats = [c[0] for c in CATEGORIES]
        self.cat_w = [CAT_SHARE[c] / self.mean[c] for c in self.cats]
        avg_line = 1.0 / sum(self.cat_w)
        self.orders_per_day = DASH_TOTAL / 31 / (1.38 * avg_line)

    def member(self, d):
        n = bisect.bisect_right(self.join_dates, d)
        if n == 0:
            return self.by_join[0][0]
        for _ in range(8):
            cand = self.by_join[rng.randrange(n)][0]
            if rng.random() < self.affinity[cand] / 3.0:
                return cand
        return self.by_join[rng.randrange(n)][0]

    def line(self, cat=None):
        cat = cat or rng.choices(self.cats, self.cat_w)[0]
        ci = self.cat_skus[cat]
        if ci["cns"] and rng.random() < CNSM_PICK:
            sku, qty = rng.choice(ci["cns"]), rng.randint(1, 3)
        else:
            sku, qty = rng.choice(ci["dev"]), 1
        return {"sku": sku, "qty": qty, "sale": self.sku_info[sku][2] * qty, "dc": 0, "rtn": 0, "void": None, "cat": cat}

    def order(self, d, lines, stat, chnl=None, octo=False):
        return {"mbr": self.member(d), "d": d, "dt": rand_time(d), "stat": stat, "lines": lines, "cpn": False, "oct": octo,
                "chnl": chnl or rng.choices(CHNL_CODES, CHNL_WEIGHTS)[0]}


def order_amount(o):
    return sum(ln["sale"] for ln in o["lines"])


def status_for(d):
    age = (END - d).days
    return "10" if age < 2 else ("20" if age < 5 else "30")


def regular_orders(shop, days):
    out = []
    for d in days:
        n = int(round(shop.orders_per_day * SEASON[d.month] * (1.12 if d.weekday() >= 5 else 0.95) * rng.uniform(0.85, 1.15)))
        for _ in range(n):
            stat = "90" if rng.random() < 0.04 else status_for(d)
            k = rng.choices([1, 2, 3], weights=[72, 21, 7])[0]
            out.append(shop.order(d, [shop.line() for _ in range(k)], stat))
    return out


def october_orders(shop):
    """10월 주문을 대시보드 숫자에서 거꾸로 만든다. 채널 합, 카테고리 합, 최고 매출일이 원 단위까지 맞는다."""
    total = DASH_TOTAL // U
    row = {ch: v // U for ch, v in DASH_CHANNEL.items()}
    col = {}
    for m, v in DASH_CAT_M.items():
        codes = [c for c in CATEGORIES if c[2] == m]
        left = v // U
        for i, c in enumerate(codes):
            part = left if i == len(codes) - 1 else int(v // U * c[6])
            col[c[0]] = part
            left -= part

    # 1) 채널 x 카테고리 목표 행렬: 비례 배분 후 남는 값을 채운다.
    cell = {(ch, cc): row[ch] * col[cc] // total for ch in row for cc in col}
    r = {ch: row[ch] - sum(cell[ch, cc] for cc in col) for ch in row}
    c = {cc: col[cc] - sum(cell[ch, cc] for ch in row) for cc in col}
    for ch in row:
        for cc in col:
            x = min(r[ch], c[cc])
            if x > 0:
                cell[ch, cc] += x
                r[ch] -= x
                c[cc] -= x
    assert not any(r.values()) and not any(c.values())

    # 2) 칸마다 줄을 뽑고, 합이 목표와 같아지도록 금액을 맞춘다.
    lines_by_ch = {ch: [] for ch in row}
    for (ch, cc), units in cell.items():
        target = units * U
        if target == 0:
            continue
        picked, s = [], 0
        while True:
            ln = shop.line(cc)
            if picked and s + ln["sale"] > target:
                if (s + ln["sale"] - target) < (target - s):
                    picked.append(ln)
                    s += ln["sale"]
                break
            picked.append(ln)
            s += ln["sale"]
            if s >= target:
                break
        f = target / s
        tot = 0
        for ln in picked:
            ln["sale"] = int(ln["sale"] * f) // U * U
            ln["cell"] = (ch, cc)
            tot += ln["sale"]
        picked[0]["sale"] += target - tot
        lines_by_ch[ch].extend(picked)

    # 3) 줄을 주문으로 묶는다.
    orders = []
    for ch, lns in lines_by_ch.items():
        rng.shuffle(lns)
        i = 0
        while i < len(lns):
            k = rng.choices([1, 2, 3], weights=[72, 21, 7])[0]
            orders.append({"chnl": ch, "lines": lns[i:i + k]})
            i += k

    # 4) 날짜 배정: 캡처의 꺾은선 모양을 따르되 최고 매출일은 정확히 맞춘다.
    days = list(daterange(OCT_START, OCT_END))
    peak_i = days.index(DASH_PEAK_DAY)
    rest_shape = sum(DAY_SHAPE) - DAY_SHAPE[peak_i]
    goal = {d: DASH_PEAK if d == DASH_PEAK_DAY else (DASH_TOTAL - DASH_PEAK) * DAY_SHAPE[i] / rest_shape for i, d in enumerate(days)}
    cur = {d: 0 for d in days}
    for o in sorted(orders, key=order_amount, reverse=True):
        d = max(days, key=lambda x: goal[x] - cur[x])
        o["d"] = d
        cur[d] += order_amount(o)

    gap = DASH_PEAK - cur[DASH_PEAK_DAY]
    for _ in range(5000):
        if abs(gap) <= 30_000:
            break
        if gap > 0:
            cands = [o for o in orders if o["d"] != DASH_PEAK_DAY and order_amount(o) <= gap]
            if not cands:
                break
            o = max(cands, key=order_amount)
            cur[o["d"]] -= order_amount(o)
            o["d"] = DASH_PEAK_DAY
        else:
            cands = [o for o in orders if o["d"] == DASH_PEAK_DAY and order_amount(o) <= -gap]
            if not cands:
                break
            o = max(cands, key=order_amount)
            o["d"] = max((d for d in days if d != DASH_PEAK_DAY), key=lambda x: goal[x] - cur[x])
            cur[DASH_PEAK_DAY] -= order_amount(o)
        cur[o["d"]] += order_amount(o)
        gap = DASH_PEAK - cur[DASH_PEAK_DAY]

    if gap != 0:  # 남은 차이는 같은 칸의 두 줄 사이에서 옮긴다(채널 합과 카테고리 합은 그대로다).
        others = {}
        for o in orders:
            if o["d"] != DASH_PEAK_DAY:
                for ln in o["lines"]:
                    others.setdefault(ln["cell"], []).append(ln)
        done = False
        peak_lines = [ln for o in orders if o["d"] == DASH_PEAK_DAY for ln in o["lines"]]
        for l1 in sorted(peak_lines, key=lambda x: -x["sale"]):
            for l2 in others.get(l1["cell"], []):
                if min(l1["sale"], l2["sale"]) > 20 * abs(gap):
                    l1["sale"] += gap
                    l2["sale"] -= gap
                    done = True
                    break
            if done:
                break
        assert done, "최고 매출일 금액을 맞추지 못했습니다"

    out = []
    for o in orders:
        full = shop.order(o["d"], o["lines"], "30", chnl=o["chnl"], octo=True)
        out.append(full)

    # 5) 취소 주문과 반품 주문을 얹는다. 순매출에는 0으로 잡히므로 위에서 맞춘 합은 변하지 않는다.
    for _ in range(int(len(out) * 0.04)):
        d = rng.choices(days, DAY_SHAPE)[0]
        lines = [shop.line() for _ in range(rng.choices([1, 2], weights=[80, 20])[0])]
        for ln in lines:
            ln["void"] = "CN"
        out.append(shop.order(d, lines, "90", octo=True))
    for _ in range(int(len(out) * 0.05)):
        d = rng.choices(days, DAY_SHAPE)[0]
        ln = shop.line()
        ln["void"] = "RT"
        out.append(shop.order(d, [ln], "30", octo=True))
    return out


def build_orders(conn, shop):
    # 작년, 재작년 주문 헤더(백업 테이블). 상세는 만들지 않는다.
    bak = []
    for d in daterange(BAK_START, BAK_END):
        n = int(round(shop.orders_per_day * 0.85 * SEASON[d.month] * rng.uniform(0.85, 1.15)))
        for k in range(1, n + 1):
            stat = "90" if rng.random() < 0.04 else "30"
            bak.append((f"O{d8(d)}{k:05d}", shop.member(d), d8(d), rng.choices(CHNL_CODES, CHNL_WEIGHTS)[0], stat))
    ins(conn, "LX_HJV1_H_BAK", bak)

    orders = regular_orders(shop, daterange(CUR_START, OCT_START - timedelta(days=1)))
    orders += october_orders(shop)
    orders += regular_orders(shop, daterange(OCT_END + timedelta(days=1), END))
    orders.sort(key=lambda o: o["dt"])
    seq, last = 0, None
    for o in orders:
        seq = seq + 1 if o["d"] == last else 1
        last = o["d"]
        o["no"] = f"O{d8(o['d'])}{seq:05d}"
        for i, ln in enumerate(o["lines"], start=1):
            ln["seq"] = i
    return orders


# ---------- 쿠폰 ----------
COUPONS = [
    ("CPN00701", "신규가입 웰컴 5천원", "A", 5_000), ("CPN00702", "앱 첫 구매 10%", "R", 10),
    ("CPN00703", "가정의달 5만원", "A", 50_000), ("CPN00704", "가정의달 5%", "R", 5),
    ("CPN00705", "여름 쿨 3만원", "A", 30_000), ("CPN00706", "여름 쿨 7%", "R", 7),
    ("CPN00707", "추석 맞이 10만원", "A", 100_000), ("CPN00708", "추석 맞이 5%", "R", 5),
    ("CPN00709", "가을 리빙 7만원", "A", 70_000), ("CPN00710", "가을 리빙 6%", "R", 6),
    ("CPN00711", "11월 특가 10만원", "A", 100_000), ("CPN00712", "11월 특가 8%", "R", 8),
]
# (PRM_ID, 이름, 시작, 종료, 쿠폰, 발급 수, 사용 수). 앞의 4개는 대시보드 캡처의 숫자다. 기간과 쿠폰 연결은 지어냈다.
PROMOS = [
    ("PRM2605", "가정의달 감사제", date(2026, 5, 1), date(2026, 5, 31), ["CPN00703", "CPN00704"], 1057, 135),
    ("PRM2607", "여름 쿨 페스타", date(2026, 7, 1), date(2026, 7, 31), ["CPN00705", "CPN00706"], 1284, 241),
    ("PRM2609", "추석 맞이 혜택전", date(2026, 9, 1), date(2026, 9, 30), ["CPN00707", "CPN00708"], 1144, 228),
    ("PRM2610", "가을 리빙 위크", date(2026, 10, 12), date(2026, 10, 18), ["CPN00709", "CPN00710"], 1085, 186),
    ("PRM2611", "11월 특가전", date(2026, 11, 1), date(2026, 11, 30), ["CPN00711", "CPN00712"], None, None),
]
USE_CUTOFF = OCT_END  # 보정한 프로모션의 쿠폰 사용은 캡처 시점(11/1 06시) 전에 끝난 것으로 둔다.


def apply_coupon(o, tp, val):
    line = o["lines"][0]
    base = line["sale"]
    dc = won(min(val, base * 0.5)) if tp == "A" else won(base * val / 100.0)
    line["dc"] = dc
    if o["oct"]:
        line["sale"] = base + dc  # 10월 주문은 순액을 고정해 두었으므로 할인 전 금액을 올린다.
    o["cpn"] = True


def build_coupons(conn, members, orders):
    ins(conn, "LX_BNF1_M", COUPONS)
    ins(conn, "NX_BNF5_PRM", [(p[0], p[1], d8(p[2]), d8(p[3])) for p in PROMOS])
    ins(conn, "NX_BNF5_MAP", [(p[0], c) for p in PROMOS for c in p[4]])
    spec = {c[0]: (c[2], c[3]) for c in COUPONS}
    join_of = {m[0]: m[1] for m in members}

    def usable(o):
        return o["stat"] != "90" and not o["cpn"] and not any(ln["void"] for ln in o["lines"])

    issued, used = [], []

    # 1) 대시보드에 나온 프로모션 4개: 발급 수와 사용 수를 정확히 맞춘다.
    for _id, _nm, fr, to, cpns, n_iss, n_use in PROMOS:
        if n_iss is None:
            continue
        last = min(to + timedelta(days=30), USE_CUTOFF)
        pool = [o for o in orders if fr <= o["d"] <= last and usable(o) and join_of[o["mbr"]] <= to]
        rng.shuffle(pool)
        users = {}
        for o in pool:
            if o["mbr"] not in users:
                users[o["mbr"]] = o
            if len(users) == n_use:
                break
        assert len(users) == n_use, "쿠폰 사용 건수를 맞추지 못했습니다"
        for i, (mbr, o) in enumerate(users.items()):
            cpn = cpns[i % len(cpns)]
            lo = max(fr, join_of[mbr], o["d"] - timedelta(days=30))
            hi = min(to, o["d"])
            get = rand_time(lo + timedelta(days=rng.randint(0, (hi - lo).days)))
            if get >= o["dt"]:
                get = max(datetime(fr.year, fr.month, fr.day), o["dt"] - timedelta(minutes=rng.randint(1, 600)))
            issued.append((cpn, mbr, ts14(get), d8(get.date() + timedelta(days=30))))
            apply_coupon(o, *spec[cpn])
            used.append((cpn, mbr, o["no"], ts14(o["dt"])))
        others = [m for m in members if m[0] not in users and m[1] <= to]
        for i, (mbr, join, _g, _n, _t) in enumerate(rng.sample(others, n_iss - n_use)):
            lo = max(fr, join)
            get = rand_time(lo + timedelta(days=rng.randint(0, (to - lo).days)))
            issued.append((cpns[i % len(cpns)], mbr, ts14(get), d8(get.date() + timedelta(days=30))))

    # 2) 상시 쿠폰과 캡처 이후 프로모션: 무작위로 만든다.
    by_mbr = {}
    for o in orders:
        by_mbr.setdefault(o["mbr"], []).append(o)
    loose = [("CPN00701", CUR_START, END), ("CPN00702", CUR_START, END), ("CPN00711", date(2026, 11, 1), END), ("CPN00712", date(2026, 11, 1), END)]
    for cpn, fr, to in loose:
        for mbr, join, _g, _n, _t in rng.sample(members, rng.randint(500, 900)):
            lo = max(fr, join)
            if lo > to:
                continue
            get = rand_time(lo + timedelta(days=rng.randint(0, (to - lo).days)))
            exp = get.date() + timedelta(days=30)
            issued.append((cpn, mbr, ts14(get), d8(exp)))
            if rng.random() < 0.45:
                cands = [o for o in by_mbr.get(mbr, []) if usable(o) and get <= o["dt"] and o["d"] <= exp]
                if cands:
                    apply_coupon(cands[0], *spec[cpn])
                    used.append((cpn, mbr, cands[0]["no"], ts14(cands[0]["dt"])))
    ins(conn, "LX_BNF2_ISS", issued)
    ins(conn, "LX_BNF3_USE", used)

    # 개인이 만들어 둔 임시 테이블(과거 쿼리 '쿠폰_0915_수정'과 같은 논리로 만든다).
    prm2609 = set(PROMOS[2][4])
    used_by_0915 = {(c, m) for c, m, _o, ts in used if ts <= "20260915235959"}
    flags = {}
    for cpn, mbr, get, _exp in issued:
        if cpn in prm2609 and get <= "20260915235959":
            not_used = 0 if (cpn, mbr) in used_by_0915 else 1
            flags[mbr] = min(flags.get(mbr, 1), not_used)
    ins(conn, "TMP_KJH_0915", [(m, 1, f) for m, f in sorted(flags.items())])


def finish_orders(conn, orders, sku_info):
    heads, lines, pays, rtns, dlvs = [], [], [], [], []
    mart = {}
    last_moment = datetime(END.year, END.month, END.day, 23, 59, 59)  # 추출 시점 이후의 시각은 만들지 않는다
    for o in orders:
        heads.append((o["no"], o["mbr"], d8(o["d"]), o["chnl"], o["stat"]))
        total = 0
        for ln in o["lines"]:
            net_line = ln["sale"] - ln["dc"]
            total += net_line
            if o["stat"] == "90" or ln["void"] == "CN":
                ln["rtn"] = net_line
                rtns.append((o["no"], ln["seq"], "CN", net_line, d8(o["d"])))
            elif ln["void"] == "RT" or (not o["oct"] and (END - o["d"]).days > 7 and rng.random() < 0.05):
                ln["rtn"] = net_line
                rtns.append((o["no"], ln["seq"], "RT", net_line, d8(min(END, o["d"] + timedelta(days=rng.randint(3, 14))))))
            lines.append((o["no"], ln["seq"], ln["sku"], ln["qty"], ln["sale"], ln["dc"]))
            key = (d8(o["d"]), o["chnl"], sku_info[ln["sku"]][1])
            mart[key] = mart.get(key, 0) + net_line - ln["rtn"]
        mth = rng.choices(["01", "02", "03", "04"], weights=[55, 10, 30, 5])[0]
        if rng.random() < 0.06 and total > 20_000:
            point = won(total * rng.uniform(0.02, 0.1))
            pays.append((o["no"], mth if mth != "04" else "01", total - point, ts19(o["dt"])))
            pays.append((o["no"], "04", point, ts19(o["dt"])))
        else:
            pays.append((o["no"], mth, total, ts19(o["dt"])))
        if o["d"] >= NX_START and o["stat"] != "90":
            when = min(o["dt"] + timedelta(days=rng.randint(1, 4), hours=rng.randint(0, 8)), last_moment)
            dlvs.append((o["no"], o["stat"], utc19(when)))
    ins(conn, "LX_HJV1_H", heads)
    ins(conn, "LX_HJV1_D", lines)
    ins(conn, "LX_HJV3_PAY", pays)
    ins(conn, "LX_HJV4_RTN", rtns)
    ins(conn, "NX_HJV6_DLV", dlvs)
    ins(conn, "NX_AGG1_DSL", [(k[0], k[1], k[2], v) for k, v in sorted(mart.items()) if v != 0])


# ---------- 로그성 데이터(NX_) ----------
KEYWORDS = ["냉장고", "김치냉장고", "양문형 냉장고", "세탁기", "건조기", "정수기", "공기청정기", "프린터", "토너", "무선청소기", "복합기", "필터"]


def build_events(conn, shop, orders):
    sku_info = shop.sku_info
    sku_list = list(sku_info)
    models = sorted({v[0] for v in sku_info.values()})
    ssn, pv, srch, crt, wsh = [], [], [], [], []
    counter = [0]

    def member_on(d):
        n = bisect.bisect_right(shop.join_dates, d)
        return shop.by_join[rng.randrange(max(1, n))][0]

    def add_session(dt, cust, mdls):
        counter[0] += 1
        sid = f"SS{counter[0]:08d}"
        ssn.append((sid, cust, utc19(dt), rng.choices(["APP", "MW", "PC"], weights=[55, 25, 20])[0]))
        t = dt
        for m in mdls:
            t += timedelta(seconds=rng.randint(20, 400))
            pv.append((sid, m, utc19(t)))
        if rng.random() < 0.4:
            for _ in range(rng.randint(1, 2)):
                srch.append((sid, rng.choice(KEYWORDS), utc19(dt + timedelta(seconds=rng.randint(5, 300)))))

    for d in daterange(NX_START, END):
        for _ in range(rng.randint(110, 170) + (40 if d >= date(2026, 11, 1) else 0)):
            cust = f"C{member_on(d)}" if rng.random() < 0.65 else ""
            add_session(rand_time(d), cust, [rng.choice(models) for _ in range(rng.randint(1, 5))])
        for _ in range(rng.randint(20, 36)):
            add_t = rand_time(d)
            x = rng.random()
            del_ts = None if x < 0.5 else ("" if x < 0.7 else utc19(add_t + timedelta(hours=rng.randint(1, 72))))
            crt.append((f"C{member_on(d)}", rng.choice(sku_list), utc19(add_t), del_ts))
        for _ in range(rng.randint(12, 24)):
            wsh.append((f"C{member_on(d)}", rng.choice(models), utc19(rand_time(d))))

    # 구매로 이어진 조회와 장바구니(전환 분석이 의미 있는 숫자를 내도록).
    floor = datetime(NX_START.year, NX_START.month, NX_START.day)
    for o in orders:
        if o["d"] < NX_START or o["stat"] == "90":
            continue
        first = o["lines"][0]
        if rng.random() < 0.35:
            before = max(floor, o["dt"] - timedelta(days=rng.randint(0, 6), minutes=rng.randint(5, 600)))
            add_session(before, f"C{o['mbr']}", [sku_info[first["sku"]][0]])
        if rng.random() < 0.25:
            add_t = max(floor, o["dt"] - timedelta(hours=rng.randint(1, 48)))
            crt.append((f"C{o['mbr']}", first["sku"], utc19(add_t), utc19(o["dt"])))

    ins(conn, "NX_EVL1_SSN", ssn)
    ins(conn, "NX_EVL2_PV", pv)
    ins(conn, "NX_EVL5_SRCH", srch)
    ins(conn, "NX_EVL3_CRT", crt)
    ins(conn, "NX_EVL4_WSH", wsh)


def build_push(conn):
    agreed = [r[0] for r in conn.execute("SELECT CUST_ID FROM NX_DNS5_AGR WHERE PUSH_YN = 'Y'")]
    tgt, snd, rsp = [], [], []
    n = 0
    for d in daterange(NX_START, SEND_END):
        if d.weekday() not in (1, 4):  # 화요일, 금요일에 캠페인
            continue
        cmp_id = f"CMP{d.strftime('%y%m%d')}"
        for cust in rng.sample(agreed, rng.randint(300, 700)):
            tgt.append((cmp_id, cust, d8(d)))
            if rng.random() < 0.95:
                n += 1
                sid = f"SN{n:08d}"
                sent = datetime(d.year, d.month, d.day, 10, rng.randint(0, 59), rng.randint(0, 59))
                snd.append((sid, cust, cmp_id, utc19(sent)))
                if rng.random() < 0.85:
                    opened = rng.random() < 0.3
                    clicked = opened and rng.random() < 0.22
                    open_t = sent + timedelta(minutes=rng.randint(1, 600))
                    blank = [None if rng.random() < 0.5 else "" for _ in range(2)]
                    rsp.append((sid, utc19(open_t) if opened else blank[0],
                                utc19(open_t + timedelta(minutes=rng.randint(1, 30))) if clicked else blank[1]))
    ins(conn, "NX_QPS2_TGT", tgt)
    ins(conn, "NX_QPS1_SND", snd)
    ins(conn, "NX_QPS3_RSP", rsp)


def build_member_snapshot(conn, members, orders):
    dates = {}
    for o in orders:
        if o["stat"] != "90":
            dates.setdefault(o["mbr"], []).append(o["d"])
    snap = []
    days = [END - timedelta(days=k) for k in range(13, -1, -1)]
    for mbr, join, grd, _n, _t in members:
        mine = sorted(dates.get(mbr, []))
        for d in days:
            if join <= d:
                snap.append((d8(d), mbr, grd, bisect.bisect_right(mine, d)))
    ins(conn, "NX_AGG2_MBR_D", snap)


def check_dashboard(conn):
    """보정이 맞았는지 대시보드 패널과 같은 방식으로 다시 계산해 본다. 하나라도 다르면 멈춘다."""
    one = lambda sql: conn.execute(sql).fetchall()
    where = "DT BETWEEN '20261001' AND '20261031'"
    assert one(f"SELECT SUM(NET_AMT) FROM NX_AGG1_DSL WHERE {where}")[0][0] == DASH_TOTAL
    assert one(f"SELECT MAX(s) FROM (SELECT SUM(NET_AMT) s FROM NX_AGG1_DSL WHERE {where} GROUP BY DT)")[0][0] == DASH_PEAK
    got = dict(one(f"SELECT m.CHNL_CD, SUM(m.NET_AMT) FROM NX_AGG1_DSL m WHERE {where} GROUP BY 1"))
    assert got == DASH_CHANNEL, got
    got = dict(one(f"SELECT k.CAT_M, SUM(m.NET_AMT) FROM NX_AGG1_DSL m JOIN LX_KRP2_C k ON m.CAT_CD = k.CAT_CD WHERE {where} GROUP BY 1"))
    assert got == DASH_CAT_M, got
    got = {r[0]: (r[1], r[2]) for r in one(
        "SELECT p.PRM_ID, COUNT(*), COUNT(u.CPN_ID) FROM NX_BNF5_MAP p JOIN LX_BNF2_ISS i ON p.CPN_ID = i.CPN_ID "
        "LEFT JOIN LX_BNF3_USE u ON i.CPN_ID = u.CPN_ID AND i.MBR_NO = u.MBR_NO GROUP BY 1")}
    for pid, _nm, _fr, _to, _c, n_iss, n_use in PROMOS:
        if n_iss is not None:
            assert got[pid] == (n_iss, n_use), (pid, got[pid])


INDEXES = [
    ("LX_HJV1_H", "ORD_NO"), ("LX_HJV1_H", "MBR_NO"), ("LX_HJV1_H", "ORD_DT"), ("LX_HJV1_D", "ORD_NO"),
    ("LX_HJV1_D", "SKU_CD"), ("LX_HJV4_RTN", "ORD_NO"), ("LX_HJV3_PAY", "ORD_NO"), ("LX_KRP3_SKU", "SKU_CD"),
    ("LX_KRP1_M", "MDL_CD"), ("NX_KRP7_ATTR", "MDL_CD"), ("NX_EVL1_SSN", "SSN_ID"), ("NX_EVL1_SSN", "CUST_ID"),
    ("NX_EVL2_PV", "SSN_ID"), ("NX_EVL2_PV", "MDL_CD"), ("LX_BNF2_ISS", "CPN_ID"), ("LX_BNF2_ISS", "MBR_NO"),
    ("LX_BNF3_USE", "CPN_ID"), ("LX_BNF3_USE", "MBR_NO"), ("NX_QPS1_SND", "CUST_ID"), ("NX_QPS1_SND", "CMP_ID"),
    ("NX_QPS3_RSP", "SND_ID"), ("LX_DNS2_H", "MBR_NO"), ("NX_DNS5_AGR", "CUST_ID"), ("NX_AGG2_MBR_D", "MBR_NO"),
    ("NX_AGG1_DSL", "DT"), ("LX_HJV1_H_BAK", "ORD_DT"),
]


def main():
    if not any(DDL_DIR.glob("*.sql")):
        # 테이블 정의가 없으면 새로 만들 수 없으므로, 있던 shop.db 를 지우기 전에 멈춘다.
        raise SystemExit(
            f"테이블 정의 파일이 없습니다: {DDL_DIR}\n"
            "제공받은 입력 자료를 db/ 폴더에 넣은 뒤 다시 실행하세요. shop.db 는 건드리지 않았습니다."
        )
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    n_tables = build_schema(conn)
    build_reference(conn)
    sku_info, cat_skus = build_products(conn)
    members = build_members(conn)
    shop = Shop(members, sku_info, cat_skus)
    orders = build_orders(conn, shop)
    build_coupons(conn, members, orders)
    finish_orders(conn, orders, sku_info)
    build_events(conn, shop, orders)
    build_push(conn)
    build_member_snapshot(conn, members, orders)
    for i, (t, c) in enumerate(INDEXES):
        conn.execute(f"CREATE INDEX IX_{i:02d} ON {t} ({c})")
    conn.commit()
    check_dashboard(conn)

    print(f"\n생성 완료: {DB_PATH}  (테이블 정의 {n_tables}개, 데이터는 모두 지어낸 연습용 값)")
    print("대시보드 캡처의 10월 숫자 재현 확인: 통과 (총액, 채널 4개, 카테고리 10개, 일 최대, 쿠폰 4행)\n")
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    for name in names:
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"  {name:<18}{count:>8}행")
    print()
    conn.close()


if __name__ == "__main__":
    main()
