"""의미 계층(semantic_layer.yaml)을 읽어 쓰는 도우미.

- load_layer()          : 파일을 읽어 사전(dict)으로 돌려준다
- render_for_prompt()   : 에이전트의 프롬프트에 넣을 글로 바꾼다
- table_label(name)     : 시트의 쉬운 이름 (사전에 없으면 None)
- column_labels(name)   : 시트의 항목별 쉬운 이름 {항목: 뜻}
- metric_ids()          : 지표 id 목록
"""

from functools import lru_cache
from pathlib import Path

import yaml

LAYER_PATH = Path(__file__).parent / "semantic_layer.yaml"


@lru_cache(maxsize=1)
def load_layer() -> dict:
    if not LAYER_PATH.exists():
        return {}
    return yaml.safe_load(LAYER_PATH.read_text(encoding="utf-8")) or {}


def table_label(table: str):
    info = (load_layer().get("tables") or {}).get(table)
    return info.get("name") if info else None


def column_labels(table: str) -> dict:
    info = (load_layer().get("tables") or {}).get(table) or {}
    return dict(info.get("columns") or {})


def metric_ids() -> list:
    return [m["id"] for m in load_layer().get("metrics") or []]


def _join(items) -> str:
    return ", ".join(str(x) for x in items or [])


def render_for_prompt() -> str:
    """사전을 프롬프트용 글로 바꾼다. 사람이 읽어도 같은 내용이다."""
    layer = load_layer()
    if not layer:
        return "(semantic_layer.yaml 파일이 없습니다. 과거 쿼리와 시트를 직접 확인해 판단하세요.)"
    out = [f"사전 버전 {layer.get('meta', {}).get('version', '?')} (고친 날 {layer.get('meta', {}).get('updated', '?')})"]

    out.append("\n[지표] id [상태] 정의")
    for m in layer.get("metrics") or []:
        line = f"- {m['id']} [{m.get('status', '추정')}] {m.get('definition', '')}"
        if m.get("aliases"):
            line += f" | 이렇게도 부른다: {_join(m['aliases'])}"
        if m.get("calc"):
            line += f" | 계산: {m['calc']}"
        if m.get("time"):
            t = m["time"]
            line += f" | 날짜 항목: {t.get('column')} ({t.get('format')}{', ' + t['zone'] if t.get('zone') else ''})"
        if m.get("split_by"):
            line += f" | 나눌 수 있는 기준: {_join(m['split_by'])}"
        if m.get("source"):
            line += f" | 근거: {_join(m['source'])}"
        if m.get("notes"):
            line += f" | 주의: {m['notes']}"
        out.append(line)

    out.append("\n[나누는 기준]")
    for d in layer.get("dimensions") or []:
        out.append(f"- {d['id']}: {d.get('how', '')}")

    out.append("\n[뜻이 여러 가지인 말]")
    for t in layer.get("terms") or []:
        out.append(f"- {t['term']} → 기본: {t.get('default')} / 다른 뜻: {_join(t.get('others'))} / 규칙: {t.get('rule', '')}")

    out.append("\n[시트를 잇는 규칙]")
    for r in layer.get("relations") or []:
        line = f"- {r['from']} → {r['to']}: {r['on']}"
        if r.get("caution"):
            line += f" (주의: {r['caution']})"
        out.append(line)

    out.append("\n[데이터에 없는 것]")
    for g in layer.get("gaps") or []:
        line = f"- {g['id']} (이렇게 묻는다: {_join(g.get('asked_as'))}): {g.get('why', '')}"
        if g.get("instead"):
            line += f" | 대신 가능한 것: {g['instead']}"
        out.append(line)

    out.append("\n[정책]")
    for p in layer.get("policies") or []:
        out.append(f"- {p['id']}: {p.get('rule', '')}")

    out.append("\n[아직 모르는 것]")
    for u in layer.get("unknowns") or []:
        out.append(f"- {u}")

    out.append("\n[데이터가 있는 기간] (안내문 기준. 실제 최소·최대 날짜는 도구로 다시 확인)")
    for a in layer.get("availability") or []:
        out.append(f"- {a['what']}: {a['period']}")

    out.append("\n[시트와 항목] 시트 이름 = 쉬운 이름: 항목(뜻)")
    for name, info in (layer.get("tables") or {}).items():
        cols = ", ".join(f"{c}({label})" for c, label in (info.get("columns") or {}).items())
        line = f"- {name} = {info.get('name')}: {cols}"
        if info.get("notes"):
            line += f" | {info['notes']}"
        out.append(line)
    return "\n".join(out)


if __name__ == "__main__":
    text = render_for_prompt()
    print(text)
    print(f"\n(글자 수 {len(text):,}자, 지표 {len(metric_ids())}개)")
