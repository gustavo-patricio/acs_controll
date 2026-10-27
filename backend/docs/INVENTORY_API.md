# API do inventário normalizado

`GET /api/v1/cpes/{cpe_id}/inventory` disponibiliza uma visão consistente dos
valores e metadados já persistidos. O endpoint aparece no Swagger da aplicação
`acs.api.app:app`, na porta administrativa. Ele não consulta a CPE nem inicia RPCs.

A listagem, o detalhe simples, as sessões e os eventos mantêm seus contratos.
O novo recurso usa `schema_version: "1"`; não há alteração de banco nesta etapa.
É necessário aplicar `uv run alembic upgrade head`, incluindo `0005_read_queries`.

## Campos da resposta

- `cpe_id` e `last_seen`: identificação interna e último Inform aceito. A data
  não comprova conectividade atual nem classifica a CPE como online/offline.
- `fields`: objeto fechado com `model`, `firmware` e `hardware`.
- `capabilities`: catálogo fechado de Wi-Fi, WAN, óptica e telefonia.
- `extensions`: lista vazia nesta versão; o schema rejeita elementos arbitrários.
- `remote_queries_available`: `true`, pois o ACS oferece operações de leitura.
  Não declara elegibilidade deste equipamento: a criação de consultas valida o
  perfil homologado. Veja [consultas remotas](READ_QUERIES.md).

Cada campo contém:

A procedência e a última observação incluem `source_method` (`Inform` ou
`GetParameterValues`) e `operation_id` (UUID da operação ou `null` para Inform).
Informs posteriores podem atualizar a procedência; GPN não produz valores.

| Propriedade | Significado |
| --- | --- |
| `value` | Último texto válido conhecido, ou `null` |
| `value_status` | `available` quando existe valor; `missing` quando não existe |
| `provenance` | Origem, perfil, versão e horário do último valor válido; `null` se desconhecidos |
| `last_observation` | Última observação acompanhada, com estado `valid`/`invalid`, origem, perfil, versão, horário e motivo seguro |

Os horários são timezone-aware. Os motivos de rejeição continuam sendo `empty`,
`too_long`, `control_character` e `conflicting_values`; valores rejeitados não
entram na resposta. Origem pode ser `null` em um conflito entre caminhos.

Exemplo parcial, ilustrativo, de firmware preservado após uma leitura inválida:

```json
{
  "value": "V5R022C00S364",
  "value_status": "available",
  "provenance": {
    "source_parameter": "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
    "profile_id": "huawei-eg8145x6-10-v5r022c00s364",
    "profile_version": "1",
    "received_at": "2026-10-25T20:17:22Z",
    "source_method": "Inform",
    "operation_id": null
  },
  "last_observation": {
    "state": "invalid",
    "source_parameter": "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
    "profile_id": "generic-device-info",
    "profile_version": "1",
    "received_at": "2026-10-25T20:18:22Z",
    "invalid_reason": "empty",
    "source_method": "Inform",
    "operation_id": null
  }
}
```

Um valor legado pode estar `available` com `provenance: null`. Um campo sem valor
pode estar `missing` com uma última observação inválida. A interface deve tratar
disponibilidade do valor e qualidade da última leitura separadamente. Um Inform
parcial pode atualizar `last_seen` sem alterar os horários de campos não enviados.

Valores e metadados são lidos dentro de uma transação PostgreSQL `REPEATABLE READ`,
evitando misturar o valor de uma atualização com a origem de outra enquanto os
Informs continuam chegando.

## Capacidades e extensões

Cada capacidade tem três dimensões:

- `device_support`: `unknown`, `supported` ou `not_supported`.
- `acs_implemented`: se o ACS tem a implementação necessária para esse recurso.
- `profile_validated`: se o recurso foi validado para o perfil do equipamento.

Nesta versão, `wifi`, `wan`, `optical` e `voice` retornam `unknown`, `false`,
`false`. Não existem evidências suficientes nem coletores implementados para
essas seções. O perfil Huawei atual valida somente inventário básico; não permite
afirmar que óptica ou Wi-Fi estejam homologados. A ausência de parâmetros no
Inform não equivale a `not_supported`.

Não há extensão proprietária homologada. Por isso `extensions` aceita somente
`[]`, inclusive no OpenAPI. Antes de adicionar uma extensão, será necessário
definir seu identificador, versão, tipos, unidades, apresentação, lista permitida
de campos e testes baseados em evidência. Não será repassado um dicionário bruto
de parâmetros à interface de gestão.

## Acesso e erros

O endpoint segue a mesma política `inventory:read` das consultas existentes.
Na bancada, a opção explícita `ACS_API_ALLOW_ANONYMOUS_INVENTORY=true` também se
aplica a ele. Isso não concede permissão para criar operações ou alterar a CPE.

Respostas: `200`, `401`, `403`, `404`, `422` e `503`, conforme autenticação,
existência da CPE, validade do UUID e disponibilidade do banco. No modo anônimo
de laboratório, o Swagger retira o requisito de autenticação dessas leituras.
As respostas têm `Cache-Control: no-store` e `X-Request-ID`; erros não expõem
SQL ou mensagens internas. A resposta nova não inclui serial, credenciais,
tokens de sessão, comandos ou parâmetros fora da lista de inventário.

## Validação

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -q
```

Os testes cobrem Swagger, estados ausente/legado/válido/inválido, autorização,
leitura anônima de laboratório, erros seguros, extensões fechadas, preservação dos
contratos existentes e consistência da leitura durante uma atualização concorrente.

O próximo incremento proposto é a [coleta remota por perfil](REMOTE_COLLECTION_PLAN.md).

## Verificação no ambiente local

O endpoint foi verificado por HTTPS na API em execução, com HTTP 200, presença
no OpenAPI e acesso ao Swagger. Os dados reais armazenados da Huawei EG8145X6-10
retornaram modelo, firmware V5R022C00S364, hardware 343D.D, origem e perfil por campo.
Na verificação, a última observação retornada foi `2026-10-25T21:27:30.319762Z`.
Não foi observado novo Inform após o início do receptor nessa verificação;
esse resultado confirma a leitura dos dados persistidos, não a conectividade atual
da CPE. Os testes automatizados verificaram o recebimento e a leitura concorrente
em PostgreSQL usando fixtures anonimizadas.

## Coleta WAN e óptica

A entrega de endereços IPv4/IPv6, MAC, DNS e potência óptica usa descoberta
controlada e consultas por perfil. Consulte [contrato, comandos, evidências e
limitações](WAN_OPTICAL_TELEMETRY.md). O endpoint
`GET /api/v1/cpes/{cpe_id}/telemetry` está disponível no Swagger da API.
