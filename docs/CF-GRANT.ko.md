[English](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ko.md)

# Cloudflare 임시 키(`aib cf grant`)

서버에 오래 가는 넓은 권한의 Cloudflare 키를 두는 대신, `aib cf grant`는 매번 Touch ID를 요구한 뒤
**한 존만**, **지정한 권한만**, **대상 서버 IP에서만** 쓸 수 있고 **1~60분 후 만료되는**(기본 10분) 토큰을 만듭니다.
토큰은 ssh 표준 입력으로 서버에 바로 기록되며(권한 600), 값은 출력·로그·인수 어디에도 나오지 않습니다.

```
aib secret ask CF_TOKEN_MINTER                      # once: store the minter key (Touch ID)
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --dry-run
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --ttl 10m --var CF_API_TOKEN
aib cf list
aib cf revoke <token_id>
```

- **발급용 키**: Cloudflare 대시보드에서 **API Tokens Write**만 있는 토큰을 만들고(발급할 존에는 **Zone Read**도 추가),
  `aib secret ask CF_TOKEN_MINTER`로 저장합니다. 이 Mac의 암호화 보관함에만 두며 서버에는 필요 없습니다.
- **권한**(허용 목록): `dns-read` `dns-write` `cache-purge` `zone-read` `waf-write` `ssl-write` `routes-write` `workers-write`.
- **IP 제한**: ssh 접속 주소 + 서버 자신의 Cloudflare 방향 송신 IPv4/IPv6 + `--ip CIDR`. 하나도 없으면 만들지 않습니다.
- `grant`와 `revoke`는 반드시 Touch ID. 건너뛰는 플래그나 환경 변수는 없습니다. Mac이 아니면 만들지 않습니다.
- 서버에 전달하지 못하면 만든 토큰을 즉시 삭제합니다.
- 모든 승인(성공·거부)은 `~/.ez2-aib/audit/cf-grant.jsonl`에 추가됩니다(값 미포함).
- 지문을 기다릴 수 없는 자동 작업(인증서 갱신, 캐시 삭제)에는 존 한정·IP 제한의 작은 상시 토큰을 따로 두세요.
