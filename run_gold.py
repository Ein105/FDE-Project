"""골드셋(gold.json)으로 "의도된 성공률"을 잰다.

실행
    python run_gold.py --check        API를 쓰지 않는다. 정답 숫자와 기준 계산식이 지금 DB에서 맞는지만 확인한다.
    python run_gold.py                전체 문항을 에이전트에게 물어 채점한다. (문항 수만큼 API 비용이 든다)
    python run_gold.py G07 G09        고른 문항만 돌린다.
    python run_gold.py --failed       지난번에 실패한 문항만 다시 돌린다.
    python run_gold.py --type 불가     기대 판정이나 유형에 이 말이 들어간 문항만 돌린다.

채점 방법
- 판정이 기대와 같아야 한다.
- 숫자 문항은 정답 숫자가 전부 나와야 한다. 숫자는 답변에 실린 SQL을 직접 실행한 결과에서 찾고, 없으면 답변 본문에서 찾는다.
  - 대시보드 문항: 정답이 캡처의 숫자로 고정돼 있다.
  - 그 밖의 숫자 문항: 문항에 적힌 기준 계산식(reference_sql)을 지금 DB에서 실행한 결과가 정답이다.
- 의도된 성공률 = 통과한 문항 수 / 돌린 문항 수
- "해석 일치"는 답의 해석 줄에 기대한 지표 id가 적혔는지 본다. 통과 여부에는 넣지 않고 따로 센다.
"""

import csv
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
LAST_PATH = BASE / "gold_last.json"
HISTORY_PATH = BASE / "gold_history.csv"

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


def interpretation_line(answer: str) -> str:
    for line in answer.splitlines():
        clean = line.replace("*", "").strip()
        if clean.startswith("해석"):
            return clean
    return ""


def sql_blocks(answer: str) -> list:
    """답변에서 SELECT 또는 WITH로 시작하는 코드 블록을 뽑는다."""
    blocks = re.findall(r"```(?:sql)?\s*\n(.*?)```", answer, flags=re.DOTALL | re.IGNORECASE)
    return [b.strip() for b in blocks if re.match(r"^\s*(--[^\n]*\n\s*)*(select|with)\b", b, flags=re.IGNORECASE)]


def numbers_from_rows(rows, typed_only=False) -> list:
    """결과 표에서 숫자를 모은다. typed_only면 숫자형 칸만(문자로 적힌 '202610' 같은 이름표는 뺀다)."""
    out = []
    for r in rows:
        for v in r:
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out.append(float(v))
            elif isinstance(v, str) and not typed_only:
                try:
                    out.append(float(v.replace(",", "")))
                except ValueError:
                    pass
    return out


def numbers_from_text(text: str) -> list:
    out = []
    for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            out.append(float(tok.replace(",", "")))
        except ValueError:
            pass
    return out


def found(value, pool) -> bool:
    value = float(value)
    if not value.is_integer():
        # 사용률: 12.8 로 적혔든 12.77 이나 0.1277 로 계산됐든 같은 값으로 본다.
        return any(abs(x - value) <= RATE_TOLERANCE or abs(x * 100 - value) <= RATE_TOLERANCE for x in pool)
    return any(x == value for x in pool)


def expected_numbers(item: dict) -> list:
    """문항의 정답 숫자 목록. 'reference'면 기준 계산식을 지금 DB에서 실행해 만든다."""
    nums = item.get("numbers") or []
    if nums == "reference":
        _h, rows, _m = fetch_rows(item["reference_sql"], 5000)
        return [{"label": f"기준 계산식 결과 {i + 1}", "value": v} for i, v in enumerate(numbers_from_rows(rows, typed_only=True))]
    return nums


def grade_numbers(item: dict, answer: str) -> dict:
    expected = expected_numbers(item)
    sql_pool, errors = [], []
    blocks = sql_blocks(answer)
    for b in blocks:
        try:
            _h, rows, _m = fetch_rows(b, 5000)
            sql_pool += numbers_from_rows(rows)
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
    text_pool = numbers_from_text(answer)
    detail = []
    for n in expected:
        where = "SQL 결과" if found(n["value"], sql_pool) else ("본문" if found(n["value"], text_pool) else "없음")
        detail.append({**n, "where": where})
    hit = sum(1 for d in detail if d["where"] != "없음")
    return {"detail": detail, "hit": hit, "total": len(detail), "sql_count": len(blocks), "sql_errors": errors}


def db_matches_guide():
    """DB의 기간이 데이터 안내문과 맞는지 본다. 예전 연습용 DB로 돌려 API 비용을 버리는 일을 막는다."""
    try:
        _h, a, _m = fetch_rows("select min(ORD_DT), max(ORD_DT) from LX_HJV1_H")
        _h, b, _m = fetch_rows("select max(ORD_DT) from LX_HJV1_H_BAK")
    except Exception as e:
        return False, f"DB를 읽지 못했습니다: {e}"
    if a[0][0] < "20260501" or b[0][0] > "20241115":
        return False, (f"DB의 기간이 데이터 안내문과 다릅니다(주문 {a[0][0]}~{a[0][1]}, 옛 백업 ~{b[0][0]}). "
                       "python make_db.py 를 먼저 실행하세요.")
    return True, f"DB 기간 확인: 주문 {a[0][0]}~{a[0][1]}, 옛 백업 ~{b[0][0]}"


def load_gold() -> list:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


def check_db() -> int:
    """API 없이: 고정 정답은 기준 계산식으로 재현되는지, 기준 계산식은 실행되고 숫자를 내는지 본다."""
    gold = load_gold()
    bad = total = 0
    by_verdict, by_type = {}, {}
    ok, msg = db_matches_guide()
    print(msg + "\n")
    if not ok:
        return 1
    for item in gold:
        v = " 또는 ".join(item["expected_verdict"])
        by_verdict[v] = by_verdict.get(v, 0) + 1
        by_type[item["type"]] = by_type.get(item["type"], 0) + 1
        nums = item.get("numbers") or []
        if not nums:
            print(f"{item['id']}  판정 문항  기대: {v}")
            continue
        try:
            _h, rows, _m = fetch_rows(item["reference_sql"], 5000)
        except Exception as e:
            bad += 1
            print(f"{item['id']}  기준 계산식 오류: {e}")
            continue
        pool = numbers_from_rows(rows)
        if nums == "reference":
            n = len(numbers_from_rows(rows, typed_only=True))
            total += n
            state = f"기준 계산식이 숫자 {n}개를 냄" if n else "기준 계산식이 숫자를 내지 못함"
            bad += 0 if n else 1
        else:
            miss = [x for x in nums if not found(x["value"], pool)]
            total += len(nums)
            bad += len(miss)
            state = f"고정 정답 {len(nums) - len(miss)}/{len(nums)} 재현" + ("" if not miss else " · 불일치: " + ", ".join(f"{x['label']}={x['value']}" for x in miss))
        print(f"{item['id']}  숫자 문항  {state}")
    print(f"\n문항 {len(gold)}개 · 정답 숫자 {total}개")
    print("기대 판정별: " + ", ".join(f"{k} {v}" for k, v in by_verdict.items()))
    print("유형별: " + ", ".join(f"{k} {v}" for k, v in by_type.items()))
    print("이상 없음." if not bad else f"문제 {bad}건. python make_db.py 를 다시 실행했는지 확인하세요.")
    return 1 if bad else 0


def pick(gold: list, args: list) -> list:
    """명령줄 인자에 따라 돌릴 문항을 고른다."""
    if "--failed" in args:
        if not LAST_PATH.exists():
            sys.exit("지난 실행 기록(gold_last.json)이 없습니다. 먼저 전체를 한 번 돌리세요.")
        last = json.loads(LAST_PATH.read_text(encoding="utf-8"))
        ids = {k for k, v in last.items() if not v}
        return [g for g in gold if g["id"] in ids]
    if "--type" in args:
        word = args[args.index("--type") + 1]
        return [g for g in gold if word in g["type"] or word in g["expected_verdict"]]
    ids = {a.upper() for a in args if re.match(r"^[Gg]\d+$", a)}
    return [g for g in gold if g["id"] in ids] if ids else gold


def cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def main(args: list):
    import main as agent_module  # API 키와 deepagents가 필요하므로 여기서 불러온다.
    from layer import load_layer

    ok, msg = db_matches_guide()
    if not ok:
        sys.exit(msg)
    items = pick(load_gold(), args)
    if not items:
        sys.exit("돌릴 문항이 없습니다.")
    results = []
    for i, item in enumerate(items, start=1):
        print(f"[{i}/{len(items)}] {item['id']} ({item['type']}) 실행 중...", flush=True)
        start = time.time()
        try:
            answer = agent_module.judge(item["question"])
            usage = dict(getattr(agent_module, "LAST_RUN", {}) or {})
        except Exception as e:  # 한 문항이 실패해도 나머지는 계속 돌린다
            answer, usage = f"실행 오류: {type(e).__name__}: {e}", {}
        sec = time.time() - start
        verdict = parse_verdict(answer)
        verdict_ok = verdict in item["expected_verdict"]
        g = grade_numbers(item, answer)
        interp = interpretation_line(answer)
        interp_ok = None if not item.get("metric") else (item["metric"] in interp.replace(" ", ""))
        ok = verdict_ok and g["hit"] == g["total"]
        results.append({**item, "answer": answer, "sec": sec, "verdict": verdict, "g": g, "ok": ok,
                        "interp": interp, "interp_ok": interp_ok, "usage": usage})
        num_txt = f" / 숫자 {g['hit']}/{g['total']}" if g["total"] else ""
        cost_txt = f" / ${usage['cost_usd']}" if usage.get("cost_usd") is not None else ""
        print(f"    기대: {' 또는 '.join(item['expected_verdict'])} / 실제: {verdict}{num_txt} / {'통과' if ok else '실패'} ({sec:.0f}초{cost_txt})")

    n, n_ok = len(results), sum(r["ok"] for r in results)
    n_num, n_hit = sum(r["g"]["total"] for r in results), sum(r["g"]["hit"] for r in results)
    with_metric = [r for r in results if r["interp_ok"] is not None]
    n_interp = sum(1 for r in with_metric if r["interp_ok"])
    avg_sec = sum(r["sec"] for r in results) / n
    costs = [r["usage"].get("cost_usd") for r in results if r["usage"].get("cost_usd") is not None]
    total_cost = round(sum(costs), 2) if costs else None
    calls = [r["usage"].get("model_calls") for r in results if r["usage"].get("model_calls")]
    layer_version = (load_layer().get("meta") or {}).get("version", "?")
    headline = f"의도된 성공률: {n_ok}/{n} ({100 * n_ok / n:.0f}%)"

    lines = [
        "# 골드셋 채점 결과", "",
        f"- 실행 시각: {datetime.now():%Y-%m-%d %H:%M} · 모델: {agent_module.MODEL} · 의미 계층 버전: {layer_version}",
        f"- **{headline}**" + ("" if n == len(load_gold()) else f" (전체 {len(load_gold())}문항 중 {n}문항만 실행)"),
        f"- 숫자 일치: {n_hit}/{n_num}개",
        f"- 해석 일치(기대한 지표를 골랐는가): {n_interp}/{len(with_metric)}문항",
        f"- 문항당 평균 소요: {avg_sec:.0f}초" + (f" · 평균 모델 호출 {sum(calls) / len(calls):.1f}회" if calls else ""),
        f"- 추정 비용: ${total_cost}" if total_cost is not None else "- 추정 비용: 알 수 없음(토큰 정보 없음)",
        "", "## 유형별", "", "| 유형 | 문항 | 통과 |", "|---|---|---|",
    ]
    types = {}
    for r in results:
        t = types.setdefault(r["type"], [0, 0])
        t[0] += 1
        t[1] += int(r["ok"])
    lines += [f"| {k} | {v[0]} | {v[1]} |" for k, v in types.items()]
    lines += ["", "## 문항별", "", "| 번호 | 유형 | 질문 | 기대 판정 | 실제 판정 | 숫자 | 해석 | 통과 | 초 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        num = f"{r['g']['hit']}/{r['g']['total']}" if r["g"]["total"] else "-"
        itp = "-" if r["interp_ok"] is None else ("O" if r["interp_ok"] else "X")
        lines.append(f"| {r['id']} | {r['type']} | {cell(r['question'])} | {' 또는 '.join(r['expected_verdict'])} | {r['verdict']} | {num} | {itp} | {'O' if r['ok'] else 'X'} | {r['sec']:.0f} |")
    lines += ["", "## 문항별 상세", ""]
    for r in results:
        lines += [f"### {r['id']} ({'통과' if r['ok'] else '실패'})", "", f"> {r['question']}", "",
                  f"- 의도: {r['intent']}", f"- 정답의 근거: {r['basis']}" + ("" if r.get("confirmed") else " (BA 확인 전)")]
        if r.get("metric"):
            lines.append(f"- 기대한 지표: {r['metric']} → 답의 해석 줄: {r['interp'] or '(없음)'}")
        if r["g"]["total"]:
            lines.append(f"- 답변에 실린 SQL {r['g']['sql_count']}개" + (f", 실행 오류: {'; '.join(r['g']['sql_errors'])}" if r["g"]["sql_errors"] else ""))
            miss = [d for d in r["g"]["detail"] if d["where"] == "없음"]
            lines.append(f"- 정답 숫자 {r['g']['total']}개 중 {r['g']['hit']}개 일치" + ("" if not miss else " · 못 찾은 값: " + ", ".join(f"{d['label']}={d['value']:,.10g}" for d in miss[:12])))
        u = r["usage"]
        if u.get("model_calls"):
            lines.append(f"- 모델 호출 {u['model_calls']}회, 도구 호출 {u['tool_calls']}회, 입력 {u['input']:,} · 출력 {u['output']:,} 토큰" + (f", 추정 ${u['cost_usd']}" if u.get("cost_usd") is not None else ""))
        lines += ["", r["answer"], "", "---", ""]
    RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")

    # 다음 실행의 --failed 와, 해상도가 올라가는지 볼 이력을 남긴다.
    last = json.loads(LAST_PATH.read_text(encoding="utf-8")) if LAST_PATH.exists() else {}
    last.update({r["id"]: r["ok"] for r in results})
    LAST_PATH.write_text(json.dumps(last, ensure_ascii=False, indent=1), encoding="utf-8")
    new_file = not HISTORY_PATH.exists()
    with HISTORY_PATH.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["실행 시각", "모델", "의미 계층 버전", "문항", "통과", "숫자 일치", "숫자 전체", "해석 일치", "해석 대상", "평균 초", "추정 비용(USD)"])
        w.writerow([f"{datetime.now():%Y-%m-%d %H:%M}", agent_module.MODEL, layer_version, n, n_ok, n_hit, n_num, n_interp, len(with_metric), f"{avg_sec:.0f}", total_cost if total_cost is not None else ""])

    print(f"\n{headline}")
    print(f"숫자 일치 {n_hit}/{n_num}개 · 해석 일치 {n_interp}/{len(with_metric)}문항 · 문항당 평균 {avg_sec:.0f}초" + (f" · 추정 비용 ${total_cost}" if total_cost is not None else ""))
    failed = [r["id"] for r in results if not r["ok"]]
    if failed:
        print("실패: " + ", ".join(failed) + "  (다시 돌리려면: python run_gold.py --failed)")
    print(f"결과 파일: {RESULTS_PATH}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--check" in argv:
        sys.exit(check_db())
    main(argv)
