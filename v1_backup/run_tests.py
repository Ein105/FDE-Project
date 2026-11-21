"""tickets.json의 시험 티켓을 하나씩 판정하고 results.md에 결과를 기록한다.

실행: python run_tests.py
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

from main import judge

BASE = Path(__file__).parent
TICKETS_PATH = BASE / "tickets.json"
RESULTS_PATH = BASE / "results.md"

# "부분 가능"이 "가능"보다 먼저 검사되도록 긴 것부터 둔다.
VERDICTS = ["사람 확인 필요", "부분 가능", "불가", "가능"]


def parse_verdict(answer: str) -> str:
    """답변에서 '판정:' 줄을 찾아 판정 값을 뽑는다. 못 찾으면 '형식 오류'."""
    for line in answer.splitlines():
        clean = line.replace("*", "").replace("#", "").strip()  # 마크다운 굵게/제목 기호 제거
        if not clean:
            continue
        m = re.match(r"^판정\s*[:：]\s*(.+)$", clean)
        if m:
            value = m.group(1).strip()
            for v in VERDICTS:
                if value.startswith(v):
                    return v
            return f"형식 오류({value})"
        break  # 첫 번째 비어 있지 않은 줄이 '판정:'이 아니면 형식 위반
    return "형식 오류"


def cell(text: str) -> str:
    """표 칸에 넣을 수 있게 줄바꿈과 | 기호를 정리한다."""
    return text.replace("|", "\\|").replace("\n", " ")


def main():
    tickets = json.loads(TICKETS_PATH.read_text(encoding="utf-8"))
    results = []

    for i, t in enumerate(tickets, start=1):
        print(f"[{i}/{len(tickets)}] {t['id']} 판정 중...", flush=True)
        start = time.time()
        try:
            answer = judge(t["text"])
        except Exception as e:  # 한 티켓이 실패해도 나머지는 계속 돌린다
            answer = f"실행 오류: {type(e).__name__}: {e}"
        elapsed = time.time() - start
        actual = parse_verdict(answer)
        ok = actual == t["expected"]
        results.append({**t, "actual": actual, "ok": ok, "answer": answer, "sec": elapsed})
        print(f"    기대: {t['expected']} / 실제: {actual} / {'일치' if ok else '불일치'} ({elapsed:.0f}초)")

    n_ok = sum(r["ok"] for r in results)
    summary = f"{len(results)}개 중 {n_ok}개 일치"

    lines = [
        "# 집계 가능 여부 판정 시험 결과",
        "",
        f"- 실행 시각: {datetime.now():%Y-%m-%d %H:%M}",
        f"- 결과: **{summary}**",
        "",
        "| ID | 티켓 | 기대 | 실제 | 일치 | 소요(초) |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['id']} | {cell(r['text'])} | {r['expected']} | {r['actual']} | "
            f"{'O' if r['ok'] else 'X'} | {r['sec']:.0f} |"
        )
    lines += ["", "## 티켓별 전체 답변", ""]
    for r in results:
        lines += [
            f"### {r['id']} (기대: {r['expected']} / 실제: {r['actual']})",
            "",
            f"> {r['text']}",
            "",
            r["answer"],
            "",
            "---",
            "",
        ]

    RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{summary}")
    print(f"결과 파일: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
