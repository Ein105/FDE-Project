"""골드셋(gold.json) 10문항으로 "의도된 성공률"을 잰다.

실행
    python run_gold.py --check   API를 쓰지 않는다. 골드의 정답 숫자가 지금 DB에서 재현되는지만 확인한다.
    python run_gold.py           에이전트에게 10문항을 물어 채점하고 gold_results.md에 기록한다.

채점 방법
- 숫자 문항: 판정이 기대와 같고, 정답 숫자가 전부 나와야 통과다.
  숫자는 답변에 실린 SQL을 직접 실행한 결과에서 찾고, 거기 없으면 답변 본문에서 찾는다.
- 판정 문항: 판정이 기대와 같으면 통과다.
- 의도된 성공률 = 통과한 문항 수 / 전체 문항 수
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from tools import fetch_rows

BASE = Path(__file__).parent
GOLD_PATH = BASE / "gold.json"
RESULTS_PATH = BASE / "gold_results.md"

VERDICTS = ["사람 확인 필요", "정보 부족", "부분 가능", "불가", "가능"]
RATE_TOLERANCE = 0.05  # 사용률처럼 소수 한 자리로 적힌 정답을 비교할 때 허용하는 차이


def parse_verdict(answer: str) -> str:
    """답변의 첫 줄에서 판정 값을 뽑는다. 못 찾으면 '형식 오류'."""
    for line in answer.splitlines():
        clean = line.replace("*", "").replace("#", "").strip()
        if not clean:
            continue
        m = re.match(r"^판정\s*[:：]\s*(.+)$", clean)
        if m:
            value = m.group(1).strip()
            for v in VERDICTS:
                if value.startswith(v):
                    return v
            return f"형식 오류({value})"
        break
    return "형식 오류"


def sql_blocks(answer: str) -> list[str]:
    """답변에서 ```sql 코드 블록(또는 SELECT, WITH로 시작하는 코드 블록)을 뽑는다."""
    blocks = re.findall(r"```(?:sql)?\s*\n(.*?)```", answer, flags=re.DOTALL | re.IGNORECASE)
    return [b.strip() for b in blocks if re.match(r"^\s*(--[^\n]*\n\s*)*(select|with)\b", b, flags=re.IGNORECASE)]


def numbers_from_rows(rows) -> list[float]:
    out = []
    for r in rows:
        for v in r:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append(float(v))
            elif isinstance(v, str):
                try:
                    out.append(float(v.replace(",", "")))
                except ValueError:
                    pass
    return out


def numbers_from_text(text: str) -> list[float]:
    out = []
    for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            out.append(float(tok.replace(",", "")))
        except ValueError:
            pass
    return out


def found(value, pool: list[float]) -> bool:
    if isinstance(value, float) and not value.is_integer():
        # 사용률: 12.8 로 적혔든 12.77 이나 0.1277 로 계산됐든 같은 값으로 본다.
        return any(abs(x - value) <= RATE_TOLERANCE or abs(x * 100 - value) <= RATE_TOLERANCE for x in pool)
    return any(x == float(value) for x in pool)


def grade_numbers(item: dict, answer: str) -> dict:
    """정답 숫자가 SQL 실행 결과 또는 답변 본문에 있는지 본다."""
    sql_pool, errors = [], []
    blocks = sql_blocks(answer)
    for b in blocks:
        try:
            _headers, rows, _masked = fetch_rows(b, 2000)
            sql_pool += numbers_from_rows(rows)
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
    text_pool = numbers_from_text(answer)
    detail = []
    for n in item["numbers"]:
        where = "SQL 결과" if found(n["value"], sql_pool) else ("본문" if found(n["value"], text_pool) else "없음")
        detail.append({**n, "where": where})
    hit = sum(1 for d in detail if d["where"] != "없음")
    return {"detail": detail, "hit": hit, "total": len(detail), "sql_count": len(blocks), "sql_errors": errors}


def check_db() -> int:
    """API 없이, 골드의 기준 SQL을 실행해 정답 숫자가 지금 DB에서 나오는지 확인한다."""
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    bad = total = 0
    for item in gold:
        if not item["numbers"]:
            print(f"{item['id']}  판정 문항 (숫자 없음, 기대 판정: {' 또는 '.join(item['expected_verdict'])})")
            continue
        _h, rows, _m = fetch_rows(item["reference_sql"], 2000)
        pool = numbers_from_rows(rows)
        miss = [n for n in item["numbers"] if not found(n["value"], pool)]
        total += len(item["numbers"])
        bad += len(miss)
        state = "재현됨" if not miss else "불일치: " + ", ".join(f"{n['label']}={n['value']}" for n in miss)
        print(f"{item['id']}  숫자 {len(item['numbers']) - len(miss)}/{len(item['numbers'])}  {state}")
    print(f"\n골드 숫자 {total}개 중 {total - bad}개가 DB에서 재현됩니다.")
    if bad:
        print("불일치가 있습니다. python make_db.py 를 다시 실행했는지 확인하세요.")
    return 1 if bad else 0


def cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def main():
    from main import judge  # API 키와 deepagents가 필요하므로 여기서 불러온다.

    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    results = []
    for i, item in enumerate(gold, start=1):
        print(f"[{i}/{len(gold)}] {item['id']} ({item['type']}) 실행 중...", flush=True)
        start = time.time()
        try:
            answer = judge(item["question"])
        except Exception as e:  # 한 문항이 실패해도 나머지는 계속 돌린다
            answer = f"실행 오류: {type(e).__name__}: {e}"
        sec = time.time() - start
        verdict = parse_verdict(answer)
        verdict_ok = verdict in item["expected_verdict"]
        g = grade_numbers(item, answer)
        numbers_ok = g["hit"] == g["total"]
        ok = verdict_ok and numbers_ok
        results.append({**item, "answer": answer, "sec": sec, "verdict": verdict, "verdict_ok": verdict_ok, "g": g, "ok": ok})
        num_txt = f" / 숫자 {g['hit']}/{g['total']}" if g["total"] else ""
        print(f"    기대 판정: {' 또는 '.join(item['expected_verdict'])} / 실제: {verdict}{num_txt} / {'통과' if ok else '실패'} ({sec:.0f}초)")

    n_ok = sum(r["ok"] for r in results)
    n_num = sum(r["g"]["total"] for r in results)
    n_hit = sum(r["g"]["hit"] for r in results)
    avg_sec = sum(r["sec"] for r in results) / len(results)
    headline = f"의도된 성공률: {n_ok}/{len(results)} ({100 * n_ok / len(results):.0f}%)"

    lines = [
        "# 골드셋 채점 결과",
        "",
        f"- 실행 시각: {datetime.now():%Y-%m-%d %H:%M}",
        f"- **{headline}**",
        f"- 대시보드 숫자 일치: {n_hit}/{n_num}개",
        f"- 문항당 평균 소요: {avg_sec:.0f}초",
        "",
        "| ID | 유형 | 질문 | 기대 판정 | 실제 판정 | 숫자 | 통과 | 소요(초) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        num = f"{r['g']['hit']}/{r['g']['total']}" if r["g"]["total"] else "-"
        lines.append(
            f"| {r['id']} | {r['type']} | {cell(r['question'])} | {' 또는 '.join(r['expected_verdict'])} | "
            f"{r['verdict']} | {num} | {'O' if r['ok'] else 'X'} | {r['sec']:.0f} |"
        )
    lines += ["", "## 문항별 상세", ""]
    for r in results:
        lines += [f"### {r['id']} ({'통과' if r['ok'] else '실패'})", "", f"> {r['question']}", "", f"- 의도: {r['intent']}"]
        if r["g"]["total"]:
            lines.append(f"- 답변에 실린 SQL {r['g']['sql_count']}개" + (f", 실행 오류: {'; '.join(r['g']['sql_errors'])}" if r["g"]["sql_errors"] else ""))
            for d in r["g"]["detail"]:
                lines.append(f"  - {d['label']}: 정답 {d['value']:,} → {d['where']}")
        lines += ["", r["answer"], "", "---", ""]
    RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n{headline}")
    print(f"대시보드 숫자 일치: {n_hit}/{n_num}개, 문항당 평균 {avg_sec:.0f}초")
    print(f"결과 파일: {RESULTS_PATH}")


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(check_db())
    main()
