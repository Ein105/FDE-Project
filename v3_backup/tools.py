"""집계 가능 여부 판정 에이전트가 쓰는 도구 5개 (v2).

- search_queries(keyword)            : 대시보드 패널 쿼리와 BA들이 예전에 쓴 쿼리 검색
- list_tables()                      : 테이블 목록
- describe_table(table_name)         : 테이블의 컬럼 정보
- sample_rows(table_name, limit=5)   : 테이블의 샘플 행
- run_select(query)                  : 조회 전용 쿼리 실행 (결과 최대 50행)

모든 DB 함수는 shop.db를 읽기 전용으로 엽니다.
개인정보 컬럼(NM_MSK, TEL_MSK)은 어떤 쿼리로 읽어도 값이 가려집니다.
오류가 나면 예외를 던지지 않고 오류 설명 문자열을 돌려줍니다.
"""

import re
import sqlite3
import unicodedata
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "shop.db"
# 쿼리 파일(.txt, .sql)이 들어 있는 폴더와 출처 표시.
# 대시보드 패널 쿼리는 마케팅팀이 매일 보는 숫자의 정의라서 기준으로 삼습니다.
QUERY_DIRS = [
    (BASE / "nuri_q7wd2m" / "A-dashsql", "대시보드 패널"),
    (BASE / "A-sql", "BA 개인 쿼리"),
]

MAX_ROWS = 50  # run_select 결과의 최대 행 수
MAX_SAMPLE = 20  # sample_rows의 최대 행 수
MAX_QUERY_FILES = 6  # search_queries가 본문까지 보여주는 최대 파일 수

# 값이 외부 모델로 나가면 안 되는 개인정보 컬럼. 필요하면 여기에 추가하세요.
MASK_COLUMNS = {"NM_MSK", "TEL_MSK"}
MASK_NOTE = "\n(개인정보 컬럼 NM_MSK, TEL_MSK의 값은 가려져 NULL로 보입니다.)"


def _connect() -> tuple[sqlite3.Connection, list]:
    """shop.db를 읽기 전용으로 열고, 개인정보 컬럼을 읽으면 NULL이 되게 막습니다."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB 파일이 없습니다: {DB_PATH} (먼저 python make_db.py 를 실행하세요)")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    masked = []  # 가려진 컬럼이 읽혔는지 기록

    def authorizer(action, arg1, arg2, _db, _source):
        if action == sqlite3.SQLITE_READ and (arg2 or "").upper() in MASK_COLUMNS:
            masked.append(arg2)
            return sqlite3.SQLITE_IGNORE  # 별칭을 붙여도 값이 NULL로 바뀝니다
        return sqlite3.SQLITE_OK

    conn.set_authorizer(authorizer)
    return conn, masked


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _format_rows(headers: list[str], rows: list[tuple]) -> str:
    """결과를 읽기 쉬운 표 형태 문자열로 바꿉니다."""
    if not rows:
        return "(결과 0행)"
    lines = [" | ".join(headers)]
    lines.append("-" * len(lines[0]))
    for r in rows:
        lines.append(" | ".join("NULL" if v is None else str(v) for v in r))
    return "\n".join(lines)


def _nfc(text: str) -> str:
    """맥에서 한글 파일 이름의 자모가 풀려 저장되는 경우가 있어 한 가지 형태로 맞춥니다."""
    return unicodedata.normalize("NFC", text)


def _load_queries() -> list[tuple[str, str]]:
    """(표시 이름, 본문) 목록을 돌려줍니다. 표시 이름 앞에 출처가 붙습니다. 대시보드 패널이 먼저 옵니다."""
    out = []
    for folder, source in QUERY_DIRS:
        if not folder.exists():
            continue
        files = [f for f in folder.rglob("*") if f.is_file() and f.suffix.lower() in (".txt", ".sql")]
        for f in sorted(files, key=lambda p: _nfc(p.name)):
            try:
                out.append((f"[{source}] {_nfc(f.stem)}", _nfc(f.read_text(encoding="utf-8")).strip()))
            except Exception:
                continue
    return out


def _tables_in(text: str) -> list[str]:
    return sorted(set(re.findall(r"\b(?:LX|NX|TMP)_[A-Z0-9_]+\b", text.upper())))


def search_queries(keyword: str = "") -> str:
    """대시보드 패널 쿼리와 BA들이 예전에 써 둔 쿼리 파일을 검색합니다. 선배에게 "이거 전에 어떻게 뽑았어요?"라고 묻는 것과 같습니다.

    결과의 파일 이름 앞에 출처가 붙습니다.
    - [대시보드 패널]: 마케팅팀이 매일 보는 대시보드의 쿼리 원문입니다. 같은 지표라면 이 정의를 기준으로 삼으세요.
    - [BA 개인 쿼리]: BA가 그때그때 써 둔 쿼리입니다. 참고 자료이며 낡았거나 틀린 것이 섞여 있습니다.

    언제 쓰나: 판정을 시작할 때 가장 먼저 씁니다. 이 DB는 테이블 이름만으로 뜻을 알 수 없어서,
    어떤 테이블을 어떻게 조인하고 어떤 조건을 붙이는지는 과거 쿼리에만 들어 있습니다.
    요청의 핵심 단어(예: 냉장고, 쿠폰, 매출, 푸시, 장바구니)로 검색하고, 낯선 테이블이
    어디에 쓰였는지 알고 싶으면 테이블 이름(예: NX_QPS1_SND)으로 검색하세요.
    단어를 바꿔 가며 두세 번 검색하는 편이 좋습니다.

    주의: 같은 말(매출, 냉장고 등)을 파일마다 다르게 정의한 경우가 있습니다. 대시보드 패널에 정의가 있으면
    그것을 기본으로 쓰고, 다른 정의가 있다는 사실과 차이를 함께 적으세요.

    입력: keyword = 검색어. 띄어쓰기로 여러 단어를 주면 하나라도 들어 있는 파일을 찾습니다.
          빈 문자열이면 전체 파일 목록과 각 파일이 쓰는 테이블을 보여줍니다.
    호출 예시: search_queries("냉장고") / search_queries("쿠폰 사용률") / search_queries("NX_AGG1_DSL") / search_queries("")
    """
    try:
        queries = _load_queries()
        if not queries:
            return "쿼리 파일이 없습니다: " + ", ".join(str(d) for d, _s in QUERY_DIRS)
        listing = "\n".join(f"- {name}: {', '.join(_tables_in(text)) or '(테이블 없음)'}" for name, text in queries)
        terms = [t for t in re.split(r"[\s,]+", _nfc(keyword or "").lower()) if t]
        if not terms:
            return f"과거 쿼리 파일 {len(queries)}개 (파일 이름: 쓰는 테이블):\n{listing}"

        scored = []
        for name, text in queries:
            hay = (name + "\n" + text).lower()
            hits = sum(1 for t in terms if t in hay)
            if hits:
                scored.append((hits, name, text))
        if not scored:
            return f"'{keyword}'가 들어 있는 과거 쿼리가 없습니다. 다른 단어로 검색해 보세요.\n전체 파일 목록:\n{listing}"

        scored.sort(key=lambda x: (-x[0], 0 if x[1].startswith("[대시보드") else 1, x[1]))
        parts = [f"'{keyword}' 검색 결과: {len(scored)}개 파일"]
        for _hits, name, text in scored[:MAX_QUERY_FILES]:
            parts.append(f"\n===== {name} =====\n{text}")
        if len(scored) > MAX_QUERY_FILES:
            rest = ", ".join(name for _h, name, _t in scored[MAX_QUERY_FILES:])
            parts.append(f"\n(본문을 생략한 나머지 파일: {rest}. 파일 이름으로 다시 검색하면 볼 수 있습니다.)")
        parts.append("\n(주의: [대시보드 패널]의 정의가 기준입니다. [BA 개인 쿼리]는 참고 자료이며 낡았거나 틀렸을 수 있습니다.)")
        return "\n".join(parts)
    except Exception as e:
        return f"오류: {e}"


def list_tables() -> str:
    """DB에 있는 모든 테이블 이름과 각 테이블의 행 수를 보여줍니다.

    언제 쓰나: search_queries로 과거 쿼리를 본 뒤, 지금 DB에 실제로 어떤 테이블이 있는지 확인할 때 씁니다.
    테이블은 외부에서 수시로 추가되고 바뀌므로 기억이나 메모에 의존하지 말고 매번 조회하세요.
    메모나 과거 쿼리에 한 번도 나오지 않은 테이블은 새로 생긴 것일 수 있으니 describe_table로 열어보세요.

    입력: 없음.
    호출 예시: list_tables()
    """
    try:
        conn, _masked = _connect()
        with conn:
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                )
            ]
            if not names:
                return "테이블이 없습니다."
            lines = []
            for name in names:
                count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                lines.append(f"- {name} ({count}행)")
            return "테이블 목록:\n" + "\n".join(lines)
    except Exception as e:
        return f"오류: {e}"


def describe_table(table_name: str) -> str:
    """테이블 하나의 컬럼 이름과 자료형을 보여줍니다.

    언제 쓰나: 요청한 항목(날짜, 금액, 카테고리, 배송 유형 등)에 해당하는 컬럼이 있는지 확인할 때 씁니다.
    이 DB의 테이블에는 설명과 키 정보가 없습니다. 컬럼 이름이 약어라 뜻이 불분명하면
    sample_rows로 실제 값을 보고, 코드 값은 공통코드 테이블 LX_ZCM_CD에서 뜻을 찾으세요.
    자료형도 눈여겨보세요. 날짜가 CHAR(8)인지 VARCHAR(14)인지 TIMESTAMP인지에 따라 조건 쓰는 법이 다릅니다.

    입력: table_name = 테이블 이름 문자열.
    호출 예시: describe_table("LX_HJV1_H")
    """
    try:
        conn, _masked = _connect()
        with conn:
            if not _table_exists(conn, table_name):
                return f"오류: '{table_name}' 테이블이 없습니다. list_tables로 이름을 확인하세요."
            rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            # PRAGMA table_info 결과: (cid, name, type, notnull, default, pk)
            data = [(r[1], r[2] or "-", "개인정보(값 가림)" if r[1].upper() in MASK_COLUMNS else "") for r in rows]
            return f"[{table_name}] 컬럼:\n" + _format_rows(["컬럼", "자료형", "비고"], data)
    except Exception as e:
        return f"오류: {e}"


def sample_rows(table_name: str, limit: int = 5) -> str:
    """테이블 하나의 앞부분 행을 보여줍니다(기본 5행, 최대 20행).

    언제 쓰나: 컬럼 이름만으로는 무엇이 들어 있는지 알기 어려울 때, 실제 값의 모양
    (코드 값, 날짜 형식, 빈 문자열과 NULL이 섞여 있는지 등)을 확인할 때 씁니다.
    전체 기간이나 건수 확인에는 run_select를 쓰세요.

    입력: table_name = 테이블 이름 문자열, limit = 보고 싶은 행 수(선택, 1~20).
    호출 예시: sample_rows("NX_EVL2_PV") 또는 sample_rows("LX_ZCM_CD", 20)
    """
    try:
        limit = max(1, min(int(limit), MAX_SAMPLE))
        conn, masked = _connect()
        with conn:
            if not _table_exists(conn, table_name):
                return f"오류: '{table_name}' 테이블이 없습니다. list_tables로 이름을 확인하세요."
            cur = conn.execute(f'SELECT * FROM "{table_name}" LIMIT ?', (limit,))
            headers = [d[0] for d in cur.description]
            out = f"[{table_name}] 샘플 {limit}행:\n" + _format_rows(headers, cur.fetchall())
            return out + (MASK_NOTE if masked else "")
    except Exception as e:
        return f"오류: {e}"


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)  # /* ... */ 주석 제거
    sql = re.sub(r"--[^\n]*", " ", sql)  # -- 주석 제거
    return sql.strip()


def fetch_rows(query: str, max_rows: int = 1000) -> tuple[list[str], list[tuple], bool]:
    """조회 쿼리 한 개를 실행해 (컬럼 이름, 행, 개인정보 컬럼을 읽었는지)를 돌려줍니다.

    에이전트 도구가 아니라 run_select와 채점 스크립트(run_gold.py)가 함께 쓰는 내부 함수입니다.
    조회가 아닌 문장은 PermissionError로 거부합니다.
    """
    cleaned = _strip_sql_comments(query)
    cleaned = cleaned.rstrip().rstrip(";").strip()  # 끝에 붙은 세미콜론 하나는 허용

    # 1차 검사: 문장 한 개, SELECT 또는 WITH로 시작
    if ";" in cleaned:
        raise PermissionError("세미콜론으로 이어진 여러 문장은 실행할 수 없습니다. 쿼리는 한 번에 하나만 보내세요.")
    if not re.match(r"^(select|with)\b", cleaned, flags=re.IGNORECASE):
        raise PermissionError("SELECT 또는 WITH로 시작하는 조회 문장만 실행할 수 있습니다.")

    # 2차 안전장치: DB를 읽기 전용으로 열었으므로 데이터는 바뀌지 않습니다.
    conn, masked = _connect()
    with conn:
        cur = conn.execute(cleaned)
        headers = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows)
    return headers, rows, bool(masked)


def run_select(query: str) -> str:
    """조회 쿼리 한 개를 실행해 결과를 보여줍니다(최대 50행).

    언제 쓰나: 데이터가 실제로 들어 있는지, 기간(최소/최대 날짜)과 건수, 코드 값의 종류를 확인할 때,
    그리고 판정 전에 집계가 실제로 되는지 검증할 때 씁니다. 컬럼이 있어도 값이 비어 있거나
    기간이 짧을 수 있습니다. 결과가 많으면 GROUP BY나 COUNT로 요약하세요.
    같은 말의 정의가 여럿일 때는 정의별로 한 번씩 실행해 숫자가 얼마나 다른지 비교하면 좋습니다.

    제한: SELECT 또는 WITH로 시작하는 조회 문장 한 개만 실행합니다. INSERT, UPDATE, DELETE, DROP 등과
    세미콜론(;)으로 이어 붙인 여러 문장은 거부합니다. 이 DB는 SQLite 문법을 씁니다.
    개인을 식별하는 목록(회원번호, 고객 ID 목록)은 뽑지 말고 COUNT로 인원수만 확인하세요.

    입력: query = SQL 문자열.
    호출 예시:
      run_select("SELECT ORD_STAT, COUNT(*) FROM LX_HJV1_H GROUP BY ORD_STAT")
      run_select("SELECT CD, CD_NM FROM LX_ZCM_CD WHERE CD_GRP = 'ORD_STAT'")
      run_select("SELECT MIN(ORD_DT), MAX(ORD_DT) FROM LX_HJV1_H")
    """
    try:
        headers, rows, masked = fetch_rows(query, MAX_ROWS + 1)
        note = ""
        if len(rows) > MAX_ROWS:
            rows = rows[:MAX_ROWS]
            note = f"\n(결과가 많아 앞의 {MAX_ROWS}행만 표시했습니다. COUNT나 GROUP BY로 요약해 보세요.)"
        return _format_rows(headers, rows) + note + (MASK_NOTE if masked else "")
    except PermissionError as e:
        return f"거부: {e}"
    except Exception as e:
        return f"쿼리 오류: {e}"


# main.py에서 tools=ALL_TOOLS 로 한 번에 넘길 수 있습니다.
ALL_TOOLS = [search_queries, list_tables, describe_table, sample_rows, run_select]


if __name__ == "__main__":
    # python tools.py 로 실행하면 도구를 한 번씩 호출해 봅니다. (API 호출 없음)
    print("=== 1. search_queries('채널') : 쿼리 검색 (대시보드 패널이 먼저 나와야 정상) ===")
    print(search_queries("채널")[:700] + "\n...(이하 생략)")
    print("\n=== 2. list_tables() ===")
    print(list_tables())
    print("\n=== 3. describe_table('LX_HJV1_H') ===")
    print(describe_table("LX_HJV1_H"))
    print("\n=== 4. sample_rows('LX_DNS1_M', 3) : 개인정보 컬럼이 NULL이어야 정상 ===")
    print(sample_rows("LX_DNS1_M", 3))
    print("\n=== 5. run_select (주문 상태별 건수) ===")
    print(run_select("SELECT ORD_STAT, COUNT(*) AS cnt FROM LX_HJV1_H GROUP BY ORD_STAT"))
    print("\n=== 6. run_select (별칭으로 개인정보 읽기: NULL이어야 정상) ===")
    print(run_select("SELECT MBR_NO, TEL_MSK AS x FROM LX_DNS1_M LIMIT 2"))
    print("\n=== 7. run_select (DELETE 문: 거부되어야 정상) ===")
    print(run_select("DELETE FROM LX_HJV1_H"))
    print("\n=== 8. run_select (여러 문장: 거부되어야 정상) ===")
    print(run_select("SELECT 1; DROP TABLE LX_HJV1_H"))
