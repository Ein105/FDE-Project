# BA 자유 메모

의미 계층(semantic_layer.yaml)에 넣기 애매한 메모를 여기에 적습니다. 에이전트가 실행할 때마다 읽습니다.
지표의 정의, 시트와 항목의 뜻, 데이터에 없는 것, 정책은 이 파일이 아니라 semantic_layer.yaml 에 적으세요.

- 날짜 표기는 시트마다 다르다(YYYYMMDD, YYYYMMDDHHMMSS, YYYY-MM-DD HH:MM:SS). 조건을 쓰기 전에 실제 값을 확인한다.
- 빈 값이 NULL과 빈 문자열로 섞여 있는 항목이 있다(DEL_TS, OPEN_TS, CLICK_TS).
- 주문과 행동 로그는 야간 배치로 들어와 추출 전날분까지만 있다. 수신 동의와 푸시 발송 이력은 추출 시각까지 있다.
