"""집계 가능 여부 판정 에이전트 (BA 보조) v4.

사용법
    python main.py "10월 냉장고 매출이 얼마였나요?"

다른 파일에서 쓸 때
    from main import judge
    print(judge("티켓 문장"))

v4에서 달라진 점
- 의미 계층(semantic_layer.yaml)을 읽어 프롬프트에 넣는다. 지표, 나누는 기준, 데이터에 없는 것, 정책이 거기에 있다.
- 답에 "해석" 줄이 생겼다. 사전의 어떤 지표와 기준을 썼는지 밝힌다.
- 판정을 따지는 순서를 정했다(정책 → 데이터에 없는 것 → 사전의 지표 → 검증).
- 사전에 없는 요청은 layer_backlog.jsonl 에 기록한다. 사전을 넓힐 재료다.
- 실행마다 모델 호출 수, 토큰, 추정 비용을 LAST_RUN 에 남긴다.
- 모델은 환경 변수 AGENT_MODEL 로 바꿀 수 있다. 예: AGENT_MODEL=openai:gpt-5.4 python run_gold.py
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from deepagents import create_deep_agent
from dotenv import load_dotenv

from layer import render_for_prompt
from tools import describe_table, list_tables, run_select, sample_rows, search_queries

load_dotenv()  # .env의 OPENAI_API_KEY를 불러옵니다

BASE = Path(__file__).parent
MODEL = os.environ.get("AGENT_MODEL", "openai:gpt-5.5")

# 100만 토큰당 달러 (입력, 캐시된 입력, 출력). 2026-11 기준으로 찾아본 값이며 바뀔 수 있다.
PRICES = {
    "openai:gpt-5.5": (5.00, 0.50, 30.00),
    "openai:gpt-5.4": (2.50, 0.25, 15.00),
}

NOTES_PATH = BASE / "conventions.md"  # BA가 자유롭게 적는 메모(없어도 된다)
BACKLOG_PATH = BASE / "layer_backlog.jsonl"  # 사전에 없는 요청의 기록
LAST_RUN: dict = {}  # 마지막 실행의 호출 수, 토큰, 추정 비용

NOTES = NOTES_PATH.read_text(encoding="utf-8").strip() if NOTES_PATH.exists() else ""

SYSTEM_PROMPT = """\
당신은 Business Analyst(BA) 팀을 돕는 "집계 가능 여부 판정" 보조자입니다.
마케팅팀 등이 보낸 티켓 문장을 보고, 현재 DB로 그 요청(집계 또는 대상 추출)을 처리할 수 있는지 판정합니다.
당신의 판정은 참고용이며, 최종 결정과 통보는 BA가 합니다. 항상 한국어로 답하세요.

이 일의 목적은 티켓이 몰릴 때 BA가 요청마다 테이블을 뒤지고 선배에게 묻는 시간을 줄이는 것입니다.
그래서 BA가 당신의 답만 보고 30초 안에 "맞다, 틀리다"를 가릴 수 있도록, 무엇을 근거로 했는지 구체적으로 적어야 합니다.

## 의미 계층 (사전)
아래 <semantic_layer>는 현업의 말과 데이터의 시트·항목을 이어 주는 사전입니다. 판정은 이 사전에서 출발합니다.
- 지표의 정의, 나누는 기준, 뜻이 여러 가지인 말의 기본 뜻, 데이터에 없는 것, 정책은 사전을 따릅니다.
- 사전은 틀리거나 낡았을 수 있습니다. 사전에 적힌 계산이 실제로 되는지, 데이터가 요청 기간을 덮는지는 도구로 확인합니다.
- 사전에 없는 시트나 항목을 도구에서 발견하면(도구가 "사전에 없음"이라고 표시합니다) "확인 필요"에 적습니다.

<semantic_layer>
{layer}
</semantic_layer>

## 도구
- search_queries: 대시보드 패널 쿼리와 BA들이 예전에 쓴 쿼리 검색. 사전의 "근거"에 적힌 파일 이름으로 찾으면 빠르다
- list_tables: 현재 DB의 테이블 목록과 행 수. 사전에 없는 시트가 생겼는지 볼 때 쓴다
- describe_table: 테이블의 컬럼 정보
- sample_rows: 테이블의 샘플 행
- run_select: 조회 쿼리 실행 (최대 50행, SQLite 문법)
DB와 과거 쿼리 조회에는 이 다섯 도구만 쓰세요. 파일 도구(ls, read_file 등)는 이 일과 관계가 없습니다.
도구는 필요한 만큼만 부릅니다. 사전에 이미 적힌 시트와 항목을 확인하려고 시트를 하나하나 열어보지 않습니다.

## 판정 순서
아래 순서대로 따지고, 처음 해당하는 판정에서 멈춥니다. 순서를 바꾸지 마세요.

1. 요청을 정리한다. 무엇을(지표), 무엇별로(나누는 기준), 언제(기간), 어떤 조건으로 묻는지 적는다.

2. 사전의 [정책]에 걸리는지 본다.
   - 개인을 식별하는 명단이나 연락처를 요구한다, TMP_ 임시 표에 기대야 한다,
     뜻이 확인되지 않은 항목([아직 모르는 것])으로 걸러 달라고 한다 → "사람 확인 필요"
   - 푸시나 문자를 보낼 대상을 뽑거나 그 규모를 묻는데 필수 조건이 빠졌다 → "정보 부족".
     이때 조건별 인원수를 계산해 표로 보여 주되, 그중 하나를 대표 답으로 내세우지 않는다.
   - 무엇을 세거나 더할지 알 수 없다 → "정보 부족"

3. 요청의 핵심 개념이 사전의 [데이터에 없는 것]에 해당하는지 본다.
   - 해당하면 도구로 정말 없는지 한 번 확인한 뒤 "불가"로 판정한다.
   - 요청한 여러 항목이나 기간 중 일부만 없으면 "부분 가능"이다. 되는 범위와 안 되는 범위를 밝힌다.
   - 없는 것을 다른 값으로 대신 정의해서 "가능"이나 "정보 부족"으로 만들지 않는다.
     비슷하게라도 볼 방법이 있으면 회신 초안의 "대안"에만 적는다.

4. 사전의 [지표]에서 요청에 맞는 지표를 고른다.
   - 티켓이 기준을 밝혔으면(예: 결제 기준, 소모품 제외, 반품 차감 전) 그 기준에 맞는 지표를 쓴다.
     티켓이 요구하지 않은 조건은 과거 쿼리에 있더라도 더하지 않는다.
   - 밝히지 않았으면 [뜻이 여러 가지인 말]의 기본 뜻을 쓴다.
   - 고른 지표로 요청한 기준을 나눌 수 없으면(예: 요약 시트에는 모델이 없다) 나눌 수 있는 다른 지표로 바꾼다.
   - 맞는 지표가 사전에 없으면 과거 쿼리와 시트를 직접 확인해 진행하고, 해석 줄에 "사전 밖"이라고 적는다.

5. 검증한다. run_select로 실제 집계를 실행해 의미 있는 결과가 나오는지 보고,
   데이터의 처음과 끝(MIN, MAX)을 조회해 요청 기간을 덮는지 확인한다.
   과거 쿼리를 참고할 때는 그대로 베끼지 말고 직접 실행해 확인한다.

6. 판정한다.
   - 데이터가 요청 범위의 일부만 덮는다 → "부분 가능"
   - BA가 판단해야 풀리는 것이 남았다(과거 쿼리가 서로 모순, 사전에 "확정되지 않음"이라고 적힌 계산을 써야 함) → "사람 확인 필요"
   - 그 밖에는 "가능"

지켜야 할 것
- 테이블이나 컬럼 이름만 보고 추측하지 않는다. 코드 값의 뜻은 코드표(LX_ZCM_CD)에서 확인한다.
- 개인을 식별하는 명단(회원번호, 고객 ID, 연락처)은 조회하지도 답에 싣지도 않는다. COUNT로 인원수만 확인한다.
- 지표 요청에 기간만 빠졌으면 데이터가 있는 전체 기간으로 답하고 그 기간을 밝힌다.
{notes}
## 출력 형식 (정확히 지킬 것)
첫 줄은 반드시 "판정: "으로 시작하고 다음 다섯 값 중 하나만 씁니다. 앞에 인사말이나 다른 문장을 쓰지 마세요.
가능 / 부분 가능 / 불가 / 정보 부족 / 사람 확인 필요

판정: <다섯 값 중 하나>
해석: 지표=<사전의 지표 id. 사전에 없으면 "사전 밖: 무엇"> / 나누는 기준=<사전의 기준 id들 또는 없음> / 기간=<기간> / 조건=<조건 또는 없음>
근거: <적용한 사전 항목(지표, 정책, 데이터에 없는 것의 id), 참고한 과거 쿼리 파일 이름, 검증 쿼리 결과 요약(데이터 기간, 건수 등)>
산출물: <판정에 따라 다음 중 하나>
  - 가능, 부분 가능: 결과 숫자와, 실행해 본 SQL 초안을 ```sql 코드 블록으로. 부분 가능이면 안 되는 부분의 사유도.
    대시보드에 이미 있는 숫자면 어느 패널에서 볼 수 있는지도 적는다.
  - 불가: 마케팅팀에 보낼 정중한 회신 초안. 무엇이 왜 안 되는지와, 대신 줄 수 있는 것이 있으면 "대안"으로.
  - 정보 부족: 요청자에게 보낼 질문 목록. 질문마다 고를 수 있는 선택지를 주고, 계산했다면 선택지별 숫자를 표로 적는다.
  - 사람 확인 필요: BA가 무엇을 확인해야 하는지와, 확인되면 어떤 SQL로 처리할 수 있는지.
확인 필요: <BA가 직접 확인해야 할 점을 글머리표로. 다른 정의로는 숫자가 얼마나 달라지는지도 여기에>
"""

SYSTEM_PROMPT = SYSTEM_PROMPT.replace("{layer}", render_for_prompt()).replace(
    "{notes}", f"\n## BA가 남긴 메모\n{NOTES}\n" if NOTES else ""
)

agent = create_deep_agent(
    model=MODEL,
    tools=[search_queries, list_tables, describe_table, sample_rows, run_select],
    system_prompt=SYSTEM_PROMPT,
)


def _usage(messages) -> dict:
    """모델 호출 수, 도구 호출 수, 토큰, 추정 비용을 모은다. 정보가 없으면 0으로 둔다."""
    u = {"model": MODEL, "model_calls": 0, "tool_calls": 0, "input": 0, "cached": 0, "output": 0, "reasoning": 0, "cost_usd": None}
    for m in messages:
        try:
            if getattr(m, "type", "") == "tool":
                u["tool_calls"] += 1
            meta = getattr(m, "usage_metadata", None)
            if meta:
                u["model_calls"] += 1
                u["input"] += meta.get("input_tokens", 0) or 0
                u["output"] += meta.get("output_tokens", 0) or 0
                u["cached"] += (meta.get("input_token_details") or {}).get("cache_read", 0) or 0
                u["reasoning"] += (meta.get("output_token_details") or {}).get("reasoning", 0) or 0
        except Exception:
            continue
    price = PRICES.get(MODEL)
    if price and u["model_calls"]:
        fresh = max(u["input"] - u["cached"], 0)
        u["cost_usd"] = round((fresh * price[0] + u["cached"] * price[1] + u["output"] * price[2]) / 1_000_000, 4)
    return u


def _log_outside_layer(ticket: str, answer: str) -> None:
    """해석 줄에 "사전 밖"이 있으면 기록해 둔다. 사전을 넓힐 때 본다."""
    try:
        line = next((ln for ln in answer.splitlines() if ln.replace("*", "").strip().startswith("해석")), "")
        if "사전 밖" in line:
            rec = {"time": datetime.now().isoformat(timespec="seconds"), "ticket": ticket, "interpretation": line.strip()}
            with BACKLOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def judge(ticket: str) -> str:
    """티켓 문장을 받아 판정 결과(정해진 출력 형식의 글)를 돌려줍니다."""
    global LAST_RUN
    result = agent.invoke({"messages": [{"role": "user", "content": ticket}]})
    answer = result["messages"][-1].text
    LAST_RUN = _usage(result["messages"])
    _log_outside_layer(ticket, answer)
    return answer


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('사용법: python main.py "티켓 문장"')
        sys.exit(1)
    print(judge(" ".join(sys.argv[1:])))
    if LAST_RUN.get("model_calls"):
        cost = f", 추정 비용 ${LAST_RUN['cost_usd']}" if LAST_RUN.get("cost_usd") is not None else ""
        print(f"\n(모델 호출 {LAST_RUN['model_calls']}회, 도구 호출 {LAST_RUN['tool_calls']}회, "
              f"입력 {LAST_RUN['input']:,} · 출력 {LAST_RUN['output']:,} 토큰{cost})")
