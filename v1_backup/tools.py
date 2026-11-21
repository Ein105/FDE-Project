"""집계 가능 여부 판정 에이전트가 쓰는 DB 조회 도구 4개.

- list_tables()                      : 테이블 목록
- describe_table(table_name)         : 테이블의 컬럼 정보
- sample_rows(table_name, limit=5)   : 테이블의 샘플 행
- run_select(query)                  : SELECT 전용 쿼리 실행 (결과 최대 50행)

모든 함수는 shop.db를 읽기 전용으로 엽니다.
오류가 나면 예외를 던지지 않고 오류 설명 문자열을 돌려줍니다.
"""

import re
import sqlite3
from pathlib import Path

# tools.py와 같은 폴더의 shop.db를 사용합니다.
DB_PATH = Path(__file__).parent / "shop.db"

MAX_ROWS = 50  # run_select 결과의 최대 행 수
MAX_SAMPLE = 20  # sample_rows의 최대 행 수


def _connect() -> sqlite3.Connection:
    """shop.db를 읽기 전용(mode=ro)으로 엽니다."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB 파일이 없습니다: {DB_PATH} (먼저 python make_db.py 를 실행하세요)")
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


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


def list_tables() -> str:
    """DB에 있는 모든 테이블 이름과 각 테이블의 행 수를 보여줍니다.

    언제 쓰나: 판정을 시작할 때 가장 먼저 호출합니다. 테이블은 외부(삼성 SDS)에서
    수시로 추가되므로, 기억에 의존하지 말고 매번 현재 목록을 확인하세요.
    이름만으로 뜻을 알기 어려운 테이블(예: tmp_로 시작하는 테이블)도 후보에서 빼지 마세요.

    입력: 없음.
    호출 예시: list_tables()
    """
    try:
        with _connect() as conn:
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
    """테이블 하나의 컬럼 이름, 자료형, NULL 허용 여부, 기본키 여부를 보여줍니다.

    언제 쓰나: list_tables로 후보 테이블을 고른 뒤, 요청한 항목(날짜, 지역, 배송 유형 등)에
    해당하는 컬럼이 있는지 확인할 때 씁니다. 컬럼 이름은 약어(예: dlv_tp_cd = 배송 유형 코드)가
    많으니, 뜻이 불분명하면 sample_rows로 실제 값을 보거나 cm_cd 테이블에서 코드 뜻을 찾으세요.

    입력: table_name = 테이블 이름 문자열.
    호출 예시: describe_table("dlv_mst")
    """
    try:
        with _connect() as conn:
            if not _table_exists(conn, table_name):
                return f"오류: '{table_name}' 테이블이 없습니다. list_tables로 이름을 확인하세요."
            rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            # PRAGMA table_info 결과: (cid, name, type, notnull, default, pk)
            data = [
                (r[1], r[2] or "-", "NOT NULL" if r[3] else "NULL 허용", "PK" if r[5] else "")
                for r in rows
            ]
            return f"[{table_name}] 컬럼:\n" + _format_rows(["컬럼", "자료형", "NULL", "키"], data)
    except Exception as e:
        return f"오류: {e}"


def sample_rows(table_name: str, limit: int = 5) -> str:
    """테이블 하나의 앞부분 행을 보여줍니다(기본 5행, 최대 20행).

    언제 쓰나: 컬럼 이름만으로는 무엇이 들어 있는지 알기 어려울 때, 실제 값의 모양
    (코드 값, 날짜 형식 등)을 확인할 때 씁니다. 전체 기간이나 건수 확인에는 run_select를 쓰세요.

    입력: table_name = 테이블 이름 문자열, limit = 보고 싶은 행 수(선택, 1~20).
    호출 예시: sample_rows("tmp_dlv_evt_2611") 또는 sample_rows("cm_cd", 20)
    """
    try:
        limit = max(1, min(int(limit), MAX_SAMPLE))
        with _connect() as conn:
            if not _table_exists(conn, table_name):
                return f"오류: '{table_name}' 테이블이 없습니다. list_tables로 이름을 확인하세요."
            cur = conn.execute(f'SELECT * FROM "{table_name}" LIMIT ?', (limit,))
            headers = [d[0] for d in cur.description]
            return f"[{table_name}] 샘플 {limit}행:\n" + _format_rows(headers, cur.fetchall())
    except Exception as e:
        return f"오류: {e}"


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)  # /* ... */ 주석 제거
    sql = re.sub(r"--[^\n]*", " ", sql)  # -- 주석 제거
    return sql.strip()


def run_select(query: str) -> str:
    """SELECT 쿼리 한 개를 실행해 결과를 보여줍니다(최대 50행).

    언제 쓰나: 데이터가 실제로 들어 있는지, 기간(최소/최대 날짜)과 건수, 코드 값의 종류를
    확인할 때 씁니다. 컬럼이 있어도 값이 비어 있거나 기간이 짧을 수 있으니, 판정 전에
    반드시 이 도구로 실제 데이터를 확인하세요. 결과가 많으면 GROUP BY나 COUNT로 요약하세요.

    제한: SELECT로 시작하는 문장 한 개만 실행합니다. INSERT, UPDATE, DELETE, DROP 등
    SELECT로 시작하지 않는 문장과, 세미콜론(;)으로 이어 붙인 여러 문장은 거부합니다.
    WITH(CTE)로 시작하는 문장도 거부되니 서브쿼리로 바꿔 쓰세요.

    입력: query = SQL 문자열.
    호출 예시:
      run_select("SELECT dlv_tp_cd, COUNT(*) FROM dlv_mst GROUP BY dlv_tp_cd")
      run_select("SELECT cd, cd_nm FROM cm_cd WHERE cd_grp = 'DLV_TP_CD'")
      run_select("SELECT MIN(ord_dt), MAX(ord_dt) FROM ord_mst")
    """
    try:
        cleaned = _strip_sql_comments(query)
        cleaned = cleaned.rstrip().rstrip(";").strip()  # 끝에 붙은 세미콜론 하나는 허용

        # 1차 검사: 문장 한 개, SELECT로 시작
        if ";" in cleaned:
            return "거부: 세미콜론으로 이어진 여러 문장은 실행할 수 없습니다. 쿼리는 한 번에 하나만 보내세요."
        if not re.match(r"^select\b", cleaned, flags=re.IGNORECASE):
            return "거부: SELECT로 시작하는 조회 문장만 실행할 수 있습니다."

        # 2차 안전장치: DB를 읽기 전용으로 열었으므로 데이터는 바뀌지 않습니다.
        with _connect() as conn:
            cur = conn.execute(cleaned)
            headers = [d[0] for d in cur.description]
            rows = cur.fetchmany(MAX_ROWS + 1)
            note = ""
            if len(rows) > MAX_ROWS:
                rows = rows[:MAX_ROWS]
                note = f"\n(결과가 많아 앞의 {MAX_ROWS}행만 표시했습니다. COUNT나 GROUP BY로 요약해 보세요.)"
            return _format_rows(headers, rows) + note
    except Exception as e:
        return f"쿼리 오류: {e}"


# main.py에서 tools=ALL_TOOLS 로 한 번에 넘길 수 있습니다.
ALL_TOOLS = [list_tables, describe_table, sample_rows, run_select]


if __name__ == "__main__":
    # python tools.py 로 실행하면 4개 함수를 한 번씩 호출해 봅니다.
    print("=== 1. list_tables() ===")
    print(list_tables())
    print("\n=== 2. describe_table('dlv_mst') ===")
    print(describe_table("dlv_mst"))
    print("\n=== 3. sample_rows('dlv_mst', 3) ===")
    print(sample_rows("dlv_mst", 3))
    print("\n=== 4. run_select (배송 유형별 건수) ===")
    print(run_select("SELECT dlv_tp_cd, COUNT(*) AS cnt FROM dlv_mst GROUP BY dlv_tp_cd"))
    print("\n=== 5. run_select (DELETE 문: 거부되어야 정상) ===")
    print(run_select("DELETE FROM ord_mst"))
    print("\n=== 6. run_select (여러 문장: 거부되어야 정상) ===")
    print(run_select("SELECT 1; DROP TABLE ord_mst"))
