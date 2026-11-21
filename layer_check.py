"""의미 계층(semantic_layer.yaml)이 지금 데이터와 맞는지 점검한다. API를 쓰지 않는다.

실행
    python layer_check.py            점검 결과를 보여준다.
    python layer_check.py --draft    사전에 없는 시트와 항목을 사전에 붙여 넣을 수 있는 초안으로 출력한다.

보는 것
1. 새로 생긴 것: DB에는 있는데 사전에 없는 시트와 항목
2. 사라진 것: 사전에는 있는데 DB에 없는 시트와 항목
3. 깨진 계산: 사전의 지표 계산식이 지금 DB에서 실행되는가
4. 검증 범위: 지표마다 골드셋 문항이 몇 개 있는가 (문항이 없는 지표는 아직 검증되지 않은 지표다)
5. 사전 밖 요청: 에이전트가 "사전에 없다"고 기록한 요청 (layer_backlog.jsonl)
6. 확인 현황: BA가 확인한 항목이 몇 개인가
"""

import json
import sqlite3
import sys
from pathlib import Path

from layer import load_layer

BASE = Path(__file__).parent
DB_PATH = BASE / "shop.db"
GOLD_PATH = BASE / "gold.json"
BACKLOG_PATH = BASE / "layer_backlog.jsonl"


def db_schema():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    schema = {n: [(r[1], r[2]) for r in conn.execute(f'PRAGMA table_info("{n}")')] for n in names}
    return conn, schema


def sample_values(conn, table: str, column: str) -> str:
    try:
        rows = conn.execute(f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT 3').fetchall()
        return ", ".join(str(r[0]) for r in rows)
    except Exception:
        return ""


def main() -> int:
    layer = load_layer()
    if not layer:
        print("semantic_layer.yaml 을 읽지 못했습니다.")
        return 1
    if not DB_PATH.exists():
        print(f"DB 파일이 없습니다: {DB_PATH}")
        return 1
    conn, schema = db_schema()
    tables = layer.get("tables") or {}
    metrics = layer.get("metrics") or []
    problems = 0

    new_tables = [t for t in schema if t not in tables]
    new_cols = {t: [c for c, _ty in cols if c not in (tables[t].get("columns") or {})] for t, cols in schema.items() if t in tables}
    new_cols = {t: c for t, c in new_cols.items() if c}
    gone_tables = [t for t in tables if t not in schema]
    gone_cols = {t: [c for c in (info.get("columns") or {}) if c not in {x for x, _ in schema.get(t, [])}] for t, info in tables.items() if t in schema}
    gone_cols = {t: c for t, c in gone_cols.items() if c}

    if "--draft" in sys.argv:
        if not new_tables and not new_cols:
            print("# 사전에 없는 시트나 항목이 없습니다.")
            return 0
        print("# 아래를 semantic_layer.yaml 의 tables: 아래에 붙이고, '뜻을 적어 주세요'를 채우세요.")
        print("# 괄호 안은 그 항목에 실제로 들어 있는 값의 예입니다(개인정보일 수 있으니 붙이기 전에 지우세요).")
        for t in new_tables:
            print(f"  {t}:\n    name: 뜻을 적어 주세요\n    group: 뜻을 적어 주세요\n    columns:")
            for c, ty in schema[t]:
                print(f"      {c}: 뜻을 적어 주세요   # {ty} (예: {sample_values(conn, t, c)})")
        for t, cols in new_cols.items():
            print(f"  # {t} ({tables[t].get('name')}) 의 columns: 에 추가")
            for c in cols:
                ty = dict(schema[t]).get(c, "")
                print(f"      {c}: 뜻을 적어 주세요   # {ty} (예: {sample_values(conn, t, c)})")
        return 0

    n_cols_db = sum(len(c) for c in schema.values())
    n_cols_known = sum(1 for t, cols in schema.items() for c, _ in cols if c in ((tables.get(t) or {}).get("columns") or {}))
    print("== 1. 새로 생긴 것 (DB에는 있는데 사전에 없음) ==")
    if not new_tables and not new_cols:
        print("없음")
    for t in new_tables:
        problems += 1
        print(f"- 새 시트 {t}: 항목 {', '.join(c for c, _ in schema[t])}")
    for t, cols in new_cols.items():
        problems += 1
        print(f"- {t} ({tables[t].get('name')}) 에 새 항목: {', '.join(cols)}")
    if new_tables or new_cols:
        print("  → python layer_check.py --draft 로 붙여 넣을 초안을 만들 수 있습니다.")

    print("\n== 2. 사라진 것 (사전에는 있는데 DB에 없음) ==")
    if not gone_tables and not gone_cols:
        print("없음")
    for t in gone_tables:
        problems += 1
        print(f"- 시트 {t} ({tables[t].get('name')}) 가 없습니다.")
    for t, cols in gone_cols.items():
        problems += 1
        print(f"- {t} 의 항목이 없습니다: {', '.join(cols)}")

    print("\n== 3. 지표 계산식 ==")
    broken = 0
    for m in metrics:
        if not m.get("calc"):
            continue
        try:
            conn.execute("SELECT " + m["calc"] + " LIMIT 1").fetchall()
        except Exception as e:
            broken += 1
            problems += 1
            print(f"- {m['id']}: 실행되지 않음 ({e})")
    with_calc = sum(1 for m in metrics if m.get("calc"))
    print(f"계산식이 있는 지표 {with_calc}개 중 {with_calc - broken}개가 실행됩니다. 계산식이 없는 지표 {len(metrics) - with_calc}개.")

    print("\n== 4. 검증 범위 (지표별 골드셋 문항 수) ==")
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8")) if GOLD_PATH.exists() else []
    count = {}
    for g in gold:
        if g.get("metric"):
            count[g["metric"]] = count.get(g["metric"], 0) + 1
    covered = [m["id"] for m in metrics if count.get(m["id"])]
    uncovered = [m["id"] for m in metrics if not count.get(m["id"])]
    print("문항이 있는 지표: " + ", ".join(f"{i}({count[i]})" for i in covered))
    print("문항이 없는 지표: " + (", ".join(uncovered) if uncovered else "없음"))
    unknown_metric = sorted({g["metric"] for g in gold if g.get("metric") and g["metric"] not in {m["id"] for m in metrics}})
    if unknown_metric:
        problems += 1
        print("골드셋이 사전에 없는 지표를 가리킵니다: " + ", ".join(unknown_metric))

    print("\n== 5. 사전 밖 요청 (에이전트가 사전에 없다고 기록한 요청) ==")
    backlog = []
    if BACKLOG_PATH.exists():
        for line in BACKLOG_PATH.read_text(encoding="utf-8").splitlines():
            try:
                backlog.append(json.loads(line))
            except Exception:
                pass
    print(f"{len(backlog)}건" + ("" if not backlog else " · 최근 것부터"))
    for rec in backlog[-5:][::-1]:
        print(f"- {rec.get('time')} | {rec.get('ticket')} | {rec.get('interpretation')}")

    print("\n== 6. 확인 현황 ==")
    status = {}
    for m in metrics:
        status[m.get("status", "추정")] = status.get(m.get("status", "추정"), 0) + 1
    confirmed = sum(1 for m in metrics if m.get("confirmed"))
    print("지표 상태: " + ", ".join(f"{k} {v}" for k, v in status.items()) + f" · BA가 확인한 지표 {confirmed}/{len(metrics)}")
    gold_confirmed = sum(1 for g in gold if g.get("confirmed"))
    print(f"골드셋 문항 중 BA가 정답을 확인한 문항 {gold_confirmed}/{len(gold)}")

    print("\n== 요약 ==")
    print(f"시트 {len(schema) - len(new_tables)}/{len(schema)}장, 항목 {n_cols_known}/{n_cols_db}개가 사전에 등록돼 있습니다.")
    print(f"지표 {len(metrics)}개 중 골드셋으로 검증하는 지표 {len(covered)}개.")
    print("사전과 데이터가 맞습니다." if not problems else f"손볼 곳 {problems}건.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
