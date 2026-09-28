# 통제된 씨드 회원가입 프로젝트

`SEEDED_DEVELOPMENT` 사례다. `client.py`가 `user_id`를 보내지만 등록 API 계약은 `userId`를 요구하여 422가 된다. 실제 Agolive 장애·배포·서비스 회복의 자료가 아니다.

`checks.py contract`는 같은 합성 입력의 응답과 필드 이름을 검사한다. `checks.py regression`은 여러 ID/표시 이름의 보존과 빈 ID의 422를 검사한다. 코드와 검사는 네트워크·DB를 사용하지 않는다. 수정 작업자는 원본을 보존하고 별도 사본의 요청 사전 키 문자열에만 편집한다. 검사·API·입력 파일은 해시로 고정한다.
