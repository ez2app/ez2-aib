[English](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ko.md)

# Cloudflare 임시 키(`aib cf grant`)

서버에 오래가고 권한이 큰 Cloudflare 키를 두지 않습니다. `aib cf grant` 는 매번 Touch ID를 요구한 뒤,
**존 하나만**, **지정한 권한만**, **대상 서버의 IP에서만 쓸 수 있고**, **1~10분 뒤 만료되는**(기본 10분) 토큰을 그 자리에서 발급합니다.
토큰은 메모리에만 있다가 ssh stdin으로 서버에 바로 기록됩니다(권한 600). 이 Mac의 디스크에 남지 않고, 출력·로그·인수에도 나오지 않습니다.

```
aib secret ask CF_TOKEN_MINTER                      # once: seal the minter key into this Mac's Secure Enclave
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --dry-run
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --ttl 10m --var CF_API_TOKEN
aib cf list
aib cf revoke <token_id>
```

## 방어선은 무엇인가

- **마스터 키는 이 Mac의 Secure Enclave에만 봉인합니다.** Cloudflare에서 **API Tokens Write**만(발급할 존에는 **Zone Read**도) 있는 토큰을 만들고,
  본인이 `aib secret ask CF_TOKEN_MINTER` 를 실행합니다. 값은 Secure Enclave 키(CryptoKit `SecureEnclave.P256`, 접근 제어 `biometryCurrentSet`)로
  암호화되어 `~/.ez2-aib/secrets/se/CF_TOKEN_MINTER.sealed` 에 저장됩니다.
- **마스터 키를 쓸 때마다 시스템 자체가 Touch ID를 요구합니다.** 봉인 해제에는 Secure Enclave 안에서의 키 합의가 필요하고, 그 키는 매번 생체 인증을 요구합니다.
  이는 macOS / Secure Enclave가 강제하는 것이며 aib 자체 검사가 아닙니다. 봉인 파일을 복사해 가도 이 Mac의 Secure Enclave와 본인 지문 없이는 열 수 없습니다.
  등록된 지문을 바꾸면 무효가 되므로 `aib secret ask CF_TOKEN_MINTER` 를 다시 실행하세요.
- **창의 문구는 aib가 만듭니다**: 존, 권한, TTL, 대상 호스트. 읽고 나서 누르세요. 한 번 승인에 토큰 하나만 발급합니다.
- **마스터 키를 파일에 두는 것은 허용하지 않습니다.** 키 보관소(`cloudflare.env`, LINE 채우기 포함)는 `CF_TOKEN_MINTER` 를 거부합니다.
  이미 그 줄이 있는 파일이 있으면 `grant` / `list` / `revoke` 는 거부합니다(`minter_in_file`). 위 방법으로 봉인한 뒤 그 줄을 지우세요.
- **감사 기록은 기록일 뿐 방어선이 아닙니다.** 봉인 해제 단계까지 간 시도(승인·거부 모두)는 `~/.ez2-aib/audit/cf-grant.jsonl` 에 한 줄 추가됩니다(값 없음).

## 그 밖의 규칙

- **권한**(허용 목록): `dns-read` `dns-write` `cache-purge` `zone-read` `waf-write` `ssl-write` `zone-settings-read` `zone-settings-write` `routes-write` `workers-write` `redirect-read` `redirect-write` `bulk-redirect-read` `rule-lists-read` `analytics-read`.
- **IP 잠금**: ssh가 접속하는 주소 + 서버 자신의 Cloudflare 출구 IPv4/IPv6 + 추가 `--ip CIDR`. 하나도 찾지 못하면 발급하지 않습니다.
- Touch ID를 건너뛰는 플래그나 환경 변수는 없습니다. Mac이 아니면 발급하지 않습니다. `AIB_TOUCHID=0` 은 끄기만 합니다.
- `--dry-run` 은 인수와 IP 잠금을 확인하고 계획을 출력합니다. 존 조회에는 마스터 키(= 지문)가 필요하므로 dry-run은 Cloudflare를 호출하지 않습니다.
- 서버에 전달하지 못하면 방금 발급한 토큰을 즉시 삭제합니다.
- 지문을 기다릴 수 없는 자동 작업(인증서 갱신, 캐시 삭제)에는 존 한정·IP 잠금 소형 토큰을 따로 주세요. 이 명령은 사람이 승인하는 변경용입니다.
- 처음 쓸 때 Command Line Tools의 `swiftc` 로 작은 보조 프로그램(`~/.ez2-aib/bin/aib-sehelper`, 소스는 `aib/src/cockpit/sehelper/`)을 빌드하며,
  소스가 바뀔 때만 다시 빌드합니다. `swiftc` 가 없으면 Command Line Tools 설치를 안내합니다.

## 남는 위험(있는 그대로)

- **같은 계정의 프로그램은 사용자가 쓸 수 있는 코드를 바꿀 수 있습니다.** `~/.ez2-aib/bin/aib-sehelper` 와 aib 본체는 본인 계정이 쓸 수 있는 위치에 있습니다.
  같은 계정의 AI가 이를 바꿔치기하면, 본인이 **정당한 요청을 한 번 승인할 때** 그때 해제된 마스터 키를 함께 가져갈 수 있습니다.
  Secure Enclave는 "본인 지문 없이 가져가는 것"을 막지만 "본인이 눌렀는데 실행된 프로그램이 이미 바뀐 경우"는 막지 못합니다. 후속 제안: 보조 프로그램을 root 소유 위치에 두고
  실행 전에 서명된 목록의 해시와 대조(이번 버전에는 없음, 통합 담당이 결정).
- 마스터 키는 어떤 권한의 토큰이든 발급할 수 있으며, 그 안전성은 위 Secure Enclave 관문에만 달려 있습니다. 두 번째 장치로 Cloudflare에서 마스터 키에 IP 제한(본인 Mac / 사무실 출구만)을 거세요.

## 도입 매뉴얼

세 단계입니다. ① 단계는 본인만 진행하며, ③ 단계의 각 항목은 운영 환경이므로 하나씩 본인의 승인을 받습니다.

### ① 마스터 키를 만들어 Secure Enclave에 봉인(본인만, AI는 관여하지 않음)
1. Cloudflare 대시보드 → My Profile → API Tokens → Create Custom Token:
   - 권한: User → **API Tokens: Edit**, Zone → **Zone: Read**(발급할 존, 또는 All zones).
   - 권장: Client IP Address Filtering = 이 Mac의 출구 IP(고정이 아니면 생략).
2. 본인이 터미널에서 실행(Claude Code에서는 `!`, 한 줄, 앞에 공백 없이): `!aib secret ask CF_TOKEN_MINTER`
   가려진 입력란에 붙여넣기 → 지문. 값은 입력란에서 Secure Enclave 봉인 파일로만 들어가며 대화나 `.env`에는 들어가지 않습니다.
3. `aib cf list`가 지문을 요구하고 계정 토큰을 나열하면 마스터 키가 동작하는 것입니다.

### ② 실기기 테스트(본인이 지문을 누름)
1. `aib cf grant <zone> zone-read --push <host>:/root/cf-grant-test.env --ttl 2m --why "도입 테스트" --dry-run`: 계획을 확인. IP 잠금에 대상 호스트의 IPv4 / IPv6가 포함되어야 합니다.
2. `--dry-run` 없이 다시 실행: 대화상자에 존, 권한, 2m, 대상 호스트가 표시되어야 합니다. 승인합니다.
3. 대상 호스트에서 그 토큰 사용: `/user/tokens/verify`가 active, 존 읽기 성공, DNS 읽기 거부.
4. 2분 뒤 다시 verify ⇒ 만료. `aib cf list`에서 만료가 보입니다.
5. grant를 한 번 더 실행하고 취소 ⇒ 토큰이 생성되지 않고 감사 기록에 거부가 1건 추가됩니다.
6. 대상 호스트의 테스트 파일을 삭제(단일 대상)하고 결과를 기록·보고합니다.

### ③ 기존의 넓은 키 철거(운영 환경, 하나씩 본인 승인 후)
지문을 기다릴 수 없는 자동 작업(인증서 갱신, 캐시 삭제 등)을 먼저 존 한정·IP 잠금 소형 토큰으로 옮긴 뒤:
1. 로컬 금고에 예전 마스터 키 줄이 남아 있으면 ⇒ 본인이 그 줄을 삭제.
2. 서버의 global key와 넓은 범위 토큰: 백업 후 설정 파일에서 제거. 같은 디렉터리의 예전 백업에도 같은 키가 있으므로 함께 처리.
3. Cloudflare에서 넓은 범위 토큰을 폐기하고, global key는 본인이 **Change / Roll**(비활성화만은 불가).
4. 24시간 안에 인증서 갱신, 캐시 삭제, 도메인 감사 등의 로그를 확인.
