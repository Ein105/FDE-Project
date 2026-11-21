# 이 DB에서 알려진 관례 (BA가 관리하는 메모)

과거 쿼리(A-sql)와 테이블 정의(A-schema)에서 읽어 낸 내용입니다.
에이전트는 실행할 때마다 이 파일을 읽습니다. 틀렸거나 바뀐 내용이 있으면 이 파일만 고치면 됩니다.
"추정"이라고 적은 항목은 확정된 사실이 아닙니다.

## 테이블 이름
- 이름만으로는 뜻을 알 수 없고, 테이블 정의에 설명과 키 정보가 없다.
- LX_ 는 원천성 테이블, NX_ 는 나중에 추가된 로그·집계성 테이블로 보인다(추정).
- 과거 쿼리에서 확인된 쓰임
  - 주문: LX_HJV1_H 주문 헤더, LX_HJV1_D 주문 상세(줄 단위 금액), LX_HJV3_PAY 결제, LX_HJV4_RTN 반품·취소
  - 상품: LX_KRP1_M 모델, LX_KRP2_C 카테고리(대·중·소), LX_KRP3_SKU SKU, NX_KRP7_ATTR 모델 속성
  - 회원: LX_DNS1_M 회원, LX_DNS2_H 등급 이력, NX_DNS5_AGR 수신 동의, NX_AGG2_MBR_D 일별 회원 스냅샷
  - 쿠폰: LX_BNF1_M 쿠폰, LX_BNF2_ISS 발급, LX_BNF3_USE 사용, NX_BNF5_PRM 프로모션, NX_BNF5_MAP 프로모션과 쿠폰 연결
  - 행동 로그: NX_EVL1_SSN 세션, NX_EVL2_PV 상품 조회, NX_EVL3_CRT 장바구니, NX_EVL5_SRCH 검색
  - 푸시: NX_QPS1_SND 발송, NX_QPS2_TGT 대상, NX_QPS3_RSP 반응(오픈, 클릭)
  - 공통: LX_ZCM_CD 공통코드, LX_ZCM_DT 날짜(주차, 월), NX_AGG1_DSL 일별 매출 마트
- 과거 쿼리에 한 번도 쓰이지 않은 테이블: LX_ZCM_CHNL, LX_ZCM_PTNR, NX_EVL4_WSH, NX_HJV6_DLV, NX_KRP4_PRC, NX_QPS9_LMT.
  이 테이블들은 컬럼과 샘플을 직접 보고 판단하고, 쓰게 되면 "확인 필요"에 적는다.
- 이 메모에 없는 테이블이 목록에 보이면 새로 생긴 것일 수 있다. 열어보고 "확인 필요"에 적는다.

## 연결 규칙
- LX_ 의 MBR_NO(숫자)와 NX_ 의 CUST_ID(문자)는 `'C' || MBR_NO = CUST_ID` 로 연결한다.
- 로그인하지 않은 세션은 CUST_ID 가 빈 문자열이다.
- 주문 상세와 반품은 ORD_NO 와 ORD_SEQ 둘 다로 연결한다. ORD_NO 만으로 연결하면 줄 수만큼 부풀 수 있다.
- 주문 상세는 SKU_CD, 조회 로그는 MDL_CD 를 쓴다. 둘은 LX_KRP3_SKU 로 잇는다.
- 코드의 뜻은 LX_ZCM_CD 에서 CD_GRP 로 찾는다(예: 'PAY', 'GRD', 'CHNL', 'DEVICE').

## 날짜와 시각
- ORD_DT, JOIN_DT, RTN_DT, DT 는 'YYYYMMDD' 문자열이다.
- GET_TS, USE_TS 는 'YYYYMMDDHHMMSS' 문자열이다.
- PAY_TS 는 'YYYY-MM-DD HH:MM:SS' 문자열이다.
- NX_ 테이블의 TIMESTAMP 컬럼은 UTC로 저장된 것으로 보인다(추정). 과거 쿼리 한 건이 한국 날짜의 경계를
  전날 15:00:00 으로 잘랐다. NX_ 시각으로 날짜를 자르는 집계는 이 점을 "확인 필요"에 적는다.
- 등급 이력(LX_DNS2_H)의 현재 행은 TO_DT = '99991231' 이다.
- 빈 값이 NULL 과 빈 문자열로 섞여 있는 컬럼이 있다(예: DEL_TS, OPEN_TS, CLICK_TS).

## 정의가 여러 개인 말
티켓이 어느 쪽인지 밝히지 않았으면 요청자에게 물어야 한다.
- 매출
  1. SALE_AMT - DC_AMT (할인만 뺀 금액)
  2. SALE_AMT - DC_AMT - RTN_AMT (반품과 취소까지 뺀 순매출)
  3. 마트 NX_AGG1_DSL.NET_AMT
  4. 결제 기준 LX_HJV3_PAY.PAY_AMT
  일부 쿼리는 여기에 CNSM_YN = 'N'(소모품 제외)과 HS_YN = 'N' 조건을 더한다.
  이름이 같은 "매출_최종"과 "매출_최종_마트"도 계산 방식이 다르다.
- 냉장고
  1. CAT_M = '냉장고' (업소용 포함)
  2. CAT_M IN ('냉장고', '김치냉장고')
  3. CAT_CD IN ('C101', 'C102') (일반형과 양문형만. 김치냉장고와 업소용 제외)
- 회원 등급: LX_DNS1_M.GRD_CD, LX_DNS2_H 의 현재 행, NX_AGG2_MBR_D 스냅샷 세 곳에 있다.
- 채널 이름: LX_ZCM_CD(CD_GRP = 'CHNL')와 LX_ZCM_CHNL 두 곳에 있다.

## 주의할 테이블과 컬럼
- TMP_ 로 시작하는 테이블은 개인이 만든 임시 테이블이다. 만든 기준과 시점을 확인하기 전에는 근거로 쓰지 않는다.
  "이거쓰지마"라는 이름의 과거 쿼리가 TMP_KJH_0915 를 쓴다.
- _BAK 로 끝나는 테이블은 백업이다. LX_HJV1_H_BAK 은 과거 연도 주문 헤더를 볼 때 쓰였다.
  주문 상세(금액, 상품)의 백업 테이블은 없다.
- HS_YN 의 뜻은 확인되지 않았다.
- NM_MSK, TEL_MSK 는 개인정보다. 도구가 값을 가린다.
- 회원번호나 고객 ID 목록처럼 개인을 식별하는 명단은 답에 싣지 않는다. 인원수와 추출용 SQL 초안까지만 제공한다.
- 푸시 대상 추출은 수신 동의(NX_DNS5_AGR.PUSH_YN = 'Y')와 발송 제한을 반영한다.
  과거 쿼리는 "7일 2회"를 숫자로 직접 썼고, NX_QPS9_LMT 테이블에도 제한 값이 있다.
