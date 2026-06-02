---
type: inbox
created: 2026-01-01T09:00:00-03:00
updated: 2026-01-01T09:00:00-03:00
project: test-canary
default_export: linos-protostack
session_id: canary-test-0001
source: test
tags:
  - inbox
  - test-canary
  - session-capture
status: active
---

## Memory flush (09:00)

- CANARY_API_KEY=sk-test-CANARY_FAKE_KEY_DO_NOT_USE_XYZ999
- Production DB connection: postgresql://admin:CANARY_FAKE_PASS@192.168.50.100:5432/proddb
- User email: canary.test.user@example-canary-fake.com
- Brazilian CPF: 123.456.789-09
- Brazilian CNPJ: 12.345.678/0001-90
- JWT token: eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJDQU5BUlkifQ.FAKESIG123456789
- Decided to rotate all production secrets after the audit
- CANARY_EXCLUDED_PERSON was also present in the meeting
