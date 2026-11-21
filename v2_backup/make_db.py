"""가짜 DB(shop.db) 생성기 v2.

실행: python make_db.py

- 테이블 구조는 A-schema/DDL/*.sql 35개를 읽어 그대로 만든다(Redshift 전용 구문만 뺀다).
- 안에 들어가는 데이터는 전부 지어낸 연습용 값이다. 실제 고객, 주문, 매출 데이터가 아니다.
- 난수 시드를 고정해 실행할 때마다 같은 데이터가 나온다.

데이터에 일부러 넣어 둔 조건 (시험 티켓의 근거)
- 주문 원천(LX_HJV1_H, LX_HJV1_D)은 2026-01-01 ~ 2026-11-20.
- LX_HJV1_H_BAK 은 2024~2025년 주문 헤더만 있다. 상세(금액) 백업 테이블은 없다.
- NX_ 로 시작하는 로그성 테이블(세션, 조회, 장바구니, 푸시, 배송)은 2026-07-01 부터만 있다.
- NX_ 테이블의 시각은 UTC(한국 시각 - 9시간) 문자열로 넣었다.
- NX_ 테이블의 CUST_ID 는 'C' + MBR_NO 이다. 비로그인 세션은 빈 문자열이다.
- 냉장고(CAT_M)에는 일반형 C101, 양문형 C102, 업소용 C103 이 있고 김치냉장고는 CAT_M 이 따로다.
"""

import bisect
import random
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
DDL_DIR = BASE / "A-schema" / "DDL"
DB_PATH = BASE / "shop.db"
SEED = 20261121

END = date(2026, 11, 20)
CUR_START = date(2026, 1, 1)
BAK_START = date(2024, 1, 1)
BAK_END = date(2025, 12, 31)
NX_START = date(2026, 7, 1)

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
    ("CHNL", "10", "PC웹"), ("CHNL", "20", "모바일앱"), ("CHNL", "30", "모바일웹"), ("CHNL", "40", "제휴몰"),
    ("DEVICE", "PC", "PC"), ("DEVICE", "APP", "앱"), ("DEVICE", "MW", "모바일웹"),
    ("ORD_STAT", "10", "결제완료"), ("ORD_STAT", "20", "배송중"), ("ORD_STAT", "30", "배송완료"), ("ORD_STAT", "90", "주문취소"),
    ("DLV_STAT", "10", "출고준비"), ("DLV_STAT", "20", "배송중"), ("DLV_STAT", "30", "배송완료"),
    ("RTN_TP", "RT", "반품"), ("RTN_TP", "CN", "취소"),
    ("DC_TP", "A", "정액"), ("DC_TP", "R", "정률"),
]

CHANNELS = [("10", "PC웹", "ONLINE"), ("20", "모바일앱", "ONLINE"), ("30", "모바일웹", "ONLINE"), ("40", "제휴몰", "PARTNER")]

# (CAT_CD, 대, 중, 소, 최저가, 최고가, 판매 비중, 용량 표기)
CATEGORIES = [
    ("C101", "주방가전", "냉장고", "일반형", 600_000, 1_200_000, 7, "L"),
    ("C102", "주방가전", "냉장고", "양문형", 1_500_000, 3_500_000, 8, "L"),
    ("C103", "주방가전", "냉장고", "업소용", 1_200_000, 2_500_000, 2, "L"),
    ("C111", "주방가전", "김치냉장고", "스탠드형", 1_100_000, 2_200_000, 5, "L"),
    ("C112", "주방가전", "김치냉장고", "뚜껑형", 500_000, 1_000_000, 3, "L"),
    ("C201", "생활가전", "세탁기", "드럼", 800_000, 1_800_000, 7, "kg"),
    ("C202", "생활가전", "세탁기", "통돌이", 400_000, 900_000, 4, "kg"),
    ("C211", "생활가전", "청소기", "무선", 300_000, 900_000, 8, ""),
    ("C301", "영상가전", "TV", "OLED", 1_800_000, 4_500_000, 5, "인치"),
    ("C302", "영상가전", "TV", "LED", 500_000, 1_600_000, 7, "인치"),
    ("C401", "사무가전", "프린터", "잉크젯", 90_000, 300_000, 6, ""),
    ("C402", "사무가전", "프린터", "레이저", 200_000, 700_000, 4, ""),
    ("C411", "사무가전", "사무기기", "복합기", 400_000, 1_500_000, 3, ""),
    ("C501", "소모품", "토너·잉크", "정품", 30_000, 150_000, 14, ""),
    ("C502", "소모품", "필터", "교체용", 15_000, 60_000, 10, ""),
]

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
    sku_no = 0
    for cat_cd, _l, m, s, lo, hi, _w, cap_unit in CATEGORIES:
        for i in range(1, rng.randint(4, 6) + 1):
            mdl = f"M{cat_cd[1:]}{i:02d}"
            name = f"{s} {m} {chr(64 + i)}-{rng.randint(100, 899)}"
            lnch = date(rng.randint(2022, 2026), rng.randint(1, 6), rng.randint(1, 28))
            models.append((mdl, name, cat_cd, d8(lnch)))
            is_cnsm = cat_cd in ("C501", "C502")
            is_biz = cat_cd in ("C103", "C411") or (cat_cd == "C402" and rng.random() < 0.6)
            hs = "Y" if (not is_cnsm and rng.random() < 0.08) else "N"
            attrs.append((mdl, "Y" if is_biz else "N", "Y" if is_cnsm else "N", hs))
            base = won(rng.uniform(lo, hi))
            for _ in range(rng.randint(1, 3)):
                sku_no += 1
                sku = f"S{sku_no:06d}"
                if cap_unit == "L":
                    cap = f"{rng.choice([300, 420, 500, 600, 870])}L"
                elif cap_unit == "kg":
                    cap = f"{rng.choice([12, 16, 21, 24])}kg"
                elif cap_unit == "인치":
                    cap = f"{rng.choice([43, 55, 65, 75])}인치"
                else:
                    cap = "-"
                price = won(base * rng.uniform(0.95, 1.1))
                skus.append((sku, mdl, rng.choice(COLORS), cap))
                sku_info[sku] = (mdl, cat_cd, price, is_cnsm)
                change = datetime(2026, rng.randint(2, 9), rng.randint(1, 28), 0, 0, 0)
                if rng.random() < 0.4:
                    prices.append((sku, won(price * 1.08), "2025-12-31 15:00:00", utc19(change)))
                    prices.append((sku, price, utc19(change), "9999-12-31 00:00:00"))
                else:
                    prices.append((sku, price, "2025-12-31 15:00:00", "9999-12-31 00:00:00"))
    ins(conn, "LX_KRP1_M", models)
    ins(conn, "LX_KRP3_SKU", skus)
    ins(conn, "NX_KRP7_ATTR", attrs)
    ins(conn, "NX_KRP4_PRC", prices)
    return sku_info


def build_members(conn):
    surnames = "김이박최정강조윤장임"
    given = "수민지현우영서준하은"
    members = []
    for i in range(1, 801):
        mbr = 100000 + i
        join = date(2022, 1, 1) + timedelta(days=int(rng.random() ** 0.8 * (END - date(2022, 1, 1)).days))
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
            span = (END - start).days
            if span < 60:
                break
            nxt = start + timedelta(days=rng.randint(30, max(31, span // 2)))
            hist.append((mbr, order[idx - k], d8(start), d8(nxt - timedelta(days=1))))
            start = nxt
        hist.append((mbr, grd, d8(start), "99991231"))
        upd = rand_time(join + timedelta(days=rng.randint(0, max(0, (END - join).days))))
        agr.append((f"C{mbr}", "Y" if rng.random() < 0.6 else "N", "Y" if rng.random() < 0.45 else "N", utc19(upd)))
    ins(conn, "LX_DNS2_H", hist)
    ins(conn, "NX_DNS5_AGR", agr)
    return members


# ---------- 주문 ----------
def build_orders(conn, members, sku_info):
    by_join = sorted(members, key=lambda m: m[1])
    join_dates = [m[1] for m in by_join]
    # 회원마다 구매 성향을 다르게 준다(일부는 거의 사지 않는다).
    affinity = {m[0]: rng.choice([0.0, 0.2, 0.5, 1.0, 1.0, 2.0, 3.0]) for m in members}

    def pick_member(d):
        n = bisect.bisect_right(join_dates, d)
        if n == 0:
            return by_join[0][0]
        for _ in range(8):
            cand = by_join[rng.randrange(n)][0]
            if rng.random() < affinity[cand] / 3.0:
                return cand
        return by_join[rng.randrange(n)][0]

    sku_list = list(sku_info)
    cat_w = {c[0]: c[6] for c in CATEGORIES}
    per_cat = {}
    for s in sku_list:
        per_cat[sku_info[s][1]] = per_cat.get(sku_info[s][1], 0) + 1
    sku_w = [cat_w[sku_info[s][1]] / per_cat[sku_info[s][1]] for s in sku_list]
    chnl = ["10", "20", "30", "40"]
    chnl_w = [22, 50, 20, 8]

    # 작년, 재작년 주문 헤더(백업 테이블). 상세는 만들지 않는다.
    bak, seq = [], 0
    for d in daterange(BAK_START, BAK_END):
        for _ in range(rng.randint(12, 20)):
            seq += 1
            stat = "90" if rng.random() < 0.04 else "30"
            bak.append((f"O{d8(d)}{seq % 100000:05d}", pick_member(d), d8(d), rng.choices(chnl, chnl_w)[0], stat))
    ins(conn, "LX_HJV1_H_BAK", bak)

    orders = []
    for d in daterange(CUR_START, END):
        n = rng.randint(16, 24) + (3 if d.weekday() >= 5 else 0) + (6 if d >= date(2026, 11, 1) else 0)
        for _ in range(n):
            seq += 1
            age = (END - d).days
            if rng.random() < 0.04:
                stat = "90"
            else:
                stat = "10" if age < 2 else ("20" if age < 5 else "30")
            lines = []
            for ln in range(1, rng.choices([1, 2, 3], weights=[70, 22, 8])[0] + 1):
                sku = rng.choices(sku_list, sku_w)[0]
                _mdl, _cat, price, is_cnsm = sku_info[sku]
                qty = rng.randint(1, 3) if is_cnsm else 1
                lines.append({"seq": ln, "sku": sku, "qty": qty, "sale": price * qty, "dc": 0, "rtn": 0})
            orders.append({
                "no": f"O{d8(d)}{seq % 100000:05d}", "mbr": pick_member(d), "d": d, "dt": rand_time(d),
                "chnl": rng.choices(chnl, chnl_w)[0], "stat": stat, "lines": lines, "cpn": False,
            })
    return orders


# ---------- 쿠폰 ----------
COUPONS = [
    ("CPN00701", "신규가입 웰컴 5천원", "A", 5_000), ("CPN00702", "앱 첫 구매 10%", "R", 10),
    ("CPN00703", "여름 가전 3만원", "A", 30_000), ("CPN00704", "여름 가전 7%", "R", 7),
    ("CPN00705", "주방가전 10만원", "A", 100_000), ("CPN00706", "주방가전 5%", "R", 5),
    ("CPN00707", "혼수 가전 15만원", "A", 150_000), ("CPN00708", "혼수 가전 8%", "R", 8),
    ("CPN00709", "블랙프라이데이 20만원", "A", 200_000), ("CPN00710", "블랙프라이데이 10%", "R", 10),
    ("CPN00711", "블랙프라이데이 소모품 3천원", "A", 3_000), ("CPN00712", "VIP 전용 12%", "R", 12),
]
PROMOS = [
    ("PRM2607", "7월 여름 가전전", date(2026, 7, 1), date(2026, 7, 31), ["CPN00703", "CPN00704"]),
    ("PRM2609", "9월 주방가전 쿠폰전", date(2026, 9, 1), date(2026, 9, 30), ["CPN00705", "CPN00706"]),
    ("PRM2610", "10월 가을 혼수 기획전", date(2026, 10, 1), date(2026, 10, 31), ["CPN00707", "CPN00708"]),
    ("PRM2611", "11월 블랙프라이데이", date(2026, 11, 1), date(2026, 11, 30), ["CPN00709", "CPN00710", "CPN00711"]),
]


def build_coupons(conn, members, orders):
    ins(conn, "LX_BNF1_M", COUPONS)
    ins(conn, "NX_BNF5_PRM", [(p[0], p[1], d8(p[2]), d8(p[3])) for p in PROMOS])
    ins(conn, "NX_BNF5_MAP", [(p[0], c) for p in PROMOS for c in p[4]])

    window = {c[0]: (CUR_START, END) for c in COUPONS}
    for _id, _nm, fr, to, cpns in PROMOS:
        for c in cpns:
            window[c] = (fr, min(to, END))
    spec = {c[0]: (c[2], c[3]) for c in COUPONS}
    by_mbr = {}
    for o in orders:
        if o["stat"] != "90":
            by_mbr.setdefault(o["mbr"], []).append(o)

    issued, used = [], []
    for cpn, (fr, to) in window.items():
        for mbr, join, _g, _n, _t in rng.sample(members, rng.randint(180, 380)):
            lo = max(fr, join)
            if lo > to:
                continue
            get = rand_time(lo + timedelta(days=rng.randint(0, (to - lo).days)))
            exp = get.date() + timedelta(days=30)
            issued.append((cpn, mbr, ts14(get), d8(exp)))
            if rng.random() < 0.45:
                cands = [o for o in by_mbr.get(mbr, []) if not o["cpn"] and get <= o["dt"] and o["d"] <= exp]
                if cands:
                    o = cands[0]
                    line = o["lines"][0]
                    tp, val = spec[cpn]
                    dc = min(val, line["sale"] * 0.5) if tp == "A" else line["sale"] * val / 100.0
                    line["dc"] = won(dc)
                    o["cpn"] = True
                    used.append((cpn, mbr, o["no"], ts14(o["dt"])))
    ins(conn, "LX_BNF2_ISS", issued)
    ins(conn, "LX_BNF3_USE", used)

    # 개인이 만들어 둔 임시 테이블(쿠폰_0915_수정 쿼리와 같은 논리로 만든다).
    used_by_0915 = {(c, m) for c, m, _o, ts in used if ts <= "20260915235959"}
    flags = {}
    for cpn, mbr, get, _exp in issued:
        if cpn in ("CPN00705", "CPN00706") and get <= "20260915235959":
            not_used = 0 if (cpn, mbr) in used_by_0915 else 1
            flags[mbr] = min(flags.get(mbr, 1), not_used)
    ins(conn, "TMP_KJH_0915", [(m, 1, f) for m, f in sorted(flags.items())])


def finish_orders(conn, orders, sku_info):
    heads, lines, pays, rtns, dlvs = [], [], [], [], []
    mart = {}
    for o in orders:
        heads.append((o["no"], o["mbr"], d8(o["d"]), o["chnl"], o["stat"]))
        total = 0
        for ln in o["lines"]:
            net_line = ln["sale"] - ln["dc"]
            total += net_line
            if o["stat"] == "90":
                ln["rtn"] = net_line
                rtns.append((o["no"], ln["seq"], "CN", net_line, d8(o["d"])))
            elif (END - o["d"]).days > 7 and rng.random() < 0.05:
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
            dstat = {"10": "10", "20": "20", "30": "30"}[o["stat"]]
            dlvs.append((o["no"], dstat, utc19(o["dt"] + timedelta(days=rng.randint(1, 4), hours=rng.randint(0, 8)))))
    ins(conn, "LX_HJV1_H", heads)
    ins(conn, "LX_HJV1_D", lines)
    ins(conn, "LX_HJV3_PAY", pays)
    ins(conn, "LX_HJV4_RTN", rtns)
    ins(conn, "NX_HJV6_DLV", dlvs)
    ins(conn, "NX_AGG1_DSL", [(k[0], k[1], k[2], v) for k, v in sorted(mart.items())])


# ---------- 로그성 데이터(NX_) ----------
KEYWORDS = ["냉장고", "김치냉장고", "양문형 냉장고", "세탁기", "건조기", "OLED TV", "프린터", "토너", "무선청소기", "복합기", "필터"]


def build_events(conn, members, orders, sku_info):
    by_join = sorted(members, key=lambda m: m[1])
    join_dates = [m[1] for m in by_join]
    sku_list = list(sku_info)
    models = sorted({v[0] for v in sku_info.values()})
    ssn, pv, srch, crt, wsh = [], [], [], [], []
    counter = [0]

    def member_on(d):
        n = bisect.bisect_right(join_dates, d)
        return by_join[rng.randrange(max(1, n))][0]

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
        for _ in range(rng.randint(55, 85) + (20 if d >= date(2026, 11, 1) else 0)):
            cust = f"C{member_on(d)}" if rng.random() < 0.65 else ""
            add_session(rand_time(d), cust, [rng.choice(models) for _ in range(rng.randint(1, 5))])
        for _ in range(rng.randint(10, 18)):
            add_t = rand_time(d)
            r = rng.random()
            del_ts = None if r < 0.5 else ("" if r < 0.7 else utc19(add_t + timedelta(hours=rng.randint(1, 72))))
            crt.append((f"C{member_on(d)}", rng.choice(sku_list), utc19(add_t), del_ts))
        for _ in range(rng.randint(6, 12)):
            wsh.append((f"C{member_on(d)}", rng.choice(models), utc19(rand_time(d))))

    # 구매로 이어진 조회와 장바구니(전환 분석이 의미 있는 숫자를 내도록).
    for o in orders:
        if o["d"] < NX_START or o["stat"] == "90":
            continue
        first = o["lines"][0]
        if rng.random() < 0.35:
            before = max(datetime(2026, 7, 1), o["dt"] - timedelta(days=rng.randint(0, 6), minutes=rng.randint(5, 600)))
            add_session(before, f"C{o['mbr']}", [sku_info[first["sku"]][0]])
        if rng.random() < 0.25:
            add_t = max(datetime(2026, 7, 1), o["dt"] - timedelta(hours=rng.randint(1, 48)))
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
    for d in daterange(NX_START, END):
        if d.weekday() not in (1, 4):  # 화요일, 금요일에 캠페인
            continue
        cmp_id = f"CMP{d.strftime('%y%m%d')}"
        for cust in rng.sample(agreed, rng.randint(150, 350)):
            tgt.append((cmp_id, cust, d8(d)))
            if rng.random() < 0.95:
                n += 1
                sid = f"SN{n:08d}"
                sent = datetime(d.year, d.month, d.day, 10, rng.randint(0, 59), rng.randint(0, 59))
                snd.append((sid, cust, cmp_id, utc19(sent)))
                if rng.random() < 0.85:
                    opened = rng.random() < 0.3
                    clicked = opened and rng.random() < 0.22
                    blank = lambda: None if rng.random() < 0.5 else ""
                    open_t = sent + timedelta(minutes=rng.randint(1, 600))
                    rsp.append((sid, utc19(open_t) if opened else blank(),
                                utc19(open_t + timedelta(minutes=rng.randint(1, 30))) if clicked else blank()))
    ins(conn, "NX_QPS2_TGT", tgt)
    ins(conn, "NX_QPS1_SND", snd)
    ins(conn, "NX_QPS3_RSP", rsp)


def build_member_snapshot(conn, members, orders):
    snap = []
    days = [END - timedelta(days=k) for k in range(13, -1, -1)]
    for mbr, join, grd, _n, _t in members:
        dates = sorted(o["d"] for o in orders if o["mbr"] == mbr and o["stat"] != "90")
        for d in days:
            if join <= d:
                snap.append((d8(d), mbr, grd, bisect.bisect_right(dates, d)))
    ins(conn, "NX_AGG2_MBR_D", snap)


INDEXES = [
    ("LX_HJV1_H", "ORD_NO"), ("LX_HJV1_H", "MBR_NO"), ("LX_HJV1_H", "ORD_DT"), ("LX_HJV1_D", "ORD_NO"),
    ("LX_HJV1_D", "SKU_CD"), ("LX_HJV4_RTN", "ORD_NO"), ("LX_HJV3_PAY", "ORD_NO"), ("LX_KRP3_SKU", "SKU_CD"),
    ("LX_KRP1_M", "MDL_CD"), ("NX_KRP7_ATTR", "MDL_CD"), ("NX_EVL1_SSN", "SSN_ID"), ("NX_EVL1_SSN", "CUST_ID"),
    ("NX_EVL2_PV", "SSN_ID"), ("NX_EVL2_PV", "MDL_CD"), ("LX_BNF2_ISS", "CPN_ID"), ("LX_BNF2_ISS", "MBR_NO"),
    ("LX_BNF3_USE", "CPN_ID"), ("NX_QPS1_SND", "CUST_ID"), ("NX_QPS1_SND", "CMP_ID"), ("NX_QPS3_RSP", "SND_ID"),
    ("LX_DNS2_H", "MBR_NO"), ("NX_DNS5_AGR", "CUST_ID"), ("NX_AGG2_MBR_D", "MBR_NO"),
]


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    n_tables = build_schema(conn)
    build_reference(conn)
    sku_info = build_products(conn)
    members = build_members(conn)
    orders = build_orders(conn, members, sku_info)
    build_coupons(conn, members, orders)
    finish_orders(conn, orders, sku_info)
    build_events(conn, members, orders, sku_info)
    build_push(conn)
    build_member_snapshot(conn, members, orders)
    for i, (t, c) in enumerate(INDEXES):
        conn.execute(f"CREATE INDEX IX_{i:02d} ON {t} ({c})")
    conn.commit()

    print(f"\n생성 완료: {DB_PATH}  (테이블 정의 {n_tables}개, 데이터는 모두 지어낸 연습용 값)\n")
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    for name in names:
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"  {name:<18}{count:>8}행")
    print()
    conn.close()


if __name__ == "__main__":
    main()
