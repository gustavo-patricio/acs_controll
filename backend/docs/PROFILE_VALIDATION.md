# Validação e aprovação explícita do rascunho X6

O rascunho [EG8145X6-10](HUAWEI_X6_DRAFT_FROM_EXPORT.md) continua separado do
parser SOAP genérico e dos perfis aprovados. Uma conta administrativa
`administrator` usa HTTP Basic sobre HTTPS para validar a CPE cadastrada:

```text
POST /api/v1/cpes/{cpe_id}/profiles/draft/validate
{"idempotency_key":"UUID_NOVO"}
GET  /api/v1/cpes/{cpe_id}/profiles/draft
POST /api/v1/cpes/{cpe_id}/profiles/draft/approve
POST /api/v1/cpes/{cpe_id}/profiles/draft/collect
{"idempotency_key":"OUTRO_UUID_NOVO"}
```

As rotas aparecem no Swagger da API administrativa. `validate` e `collect`
retornam 202; as operações podem ser acompanhadas também em
`GET /api/v1/cpes/{cpe_id}/queries/{operation_id}`. A API não envia RPC à
ConnectionRequestURL. A operação de validação espera o próximo Inform e POST
vazio para solicitar exclusivamente:

- `InternetGatewayDevice.DeviceInfo.HardwareVersion`
- `InternetGatewayDevice.DeviceInfo.SoftwareVersion`

A resposta é correlacionada pela fila existente. O GET do rascunho apresenta
`model`, `firmware` e `hardware` individualmente, com caminho/regra, tipo XML
observado, conversão, obrigatoriedade, estado (`pending`, `valid`, `invalid`) e
motivo seguro. Não retorna o valor bruto. Modelo compara identidade já recebida
no Inform; firmware e hardware exigem observação tipada `xsd:string` da **mesma
operação**, texto não vazio e comprimento permitido. Firmware também precisa
igualar `V5R022C00S364`. Nenhum tipo original é inferido da exportação antiga.

`approve` só funciona quando os três campos obrigatórios estão válidos, a
operação terminou com sucesso e a CPE ainda apresenta a identidade e firmware
esperados. A aprovação grava administrador e data; nenhuma validação é aprovada
automaticamente. Falha, fault, timeout, resposta parcial, tipo errado ou firmware
divergente mantêm a aprovação bloqueada. Nova validação revoga a aprovação anterior.

Após aprovação, `collect` cria **uma** nova operação GPV com os dois caminhos do
rascunho. Não percorre o catálogo e não executa descoberta a cada Inform.
Se o firmware cadastrado mudar, a aprovação é revogada na mesma transação do
Inform. O GET sinaliza `needs_revalidation` mesmo se o firmware voltar à versão
anterior; somente uma nova operação de validação pode limpar esse sinal. Se a
identidade cadastrada mudar, o GET também sinaliza `needs_revalidation` e novas coletas/aprovações são bloqueadas. Uma operação já
pendente também é protegida pela verificação de perfil da fila CWMP. Uma revisão
para outro firmware exige nova evidência e outro rascunho; não se adapta a regra
atual automaticamente.

A migration `0012_profile_validations` guarda a operação de origem e a aprovação
por CPE. Não altera os valores coletados nem a migration de inventário.

```bash
uv run alembic upgrade head
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -q \
  tests/integration/test_profile_validation.py tests/unit/test_draft_profile.py
uv run ruff check .
uv run ruff format --check .
uv run pyright
```
